from typing import Union, Tuple
import datetime
from urllib.parse import quote

from loguru import logger

from . import Database
from ..core import Metadata, Media, ArchivingContext
from ..utils import GWorksheet

# credentials for db - need something to be there for code to work!
from .. import cred_mssql
import pyodbc 
import time

from ..uwazi_api.UwaziAdapter import UwaziAdapter


class GsheetsDb(Database):
    """
        NB: only works if GsheetFeeder is used. 
        could be updated in the future to support non-GsheetFeeder metadata 
    """
    name = "gsheet_db"

    def __init__(self, config: dict) -> None:
        # without this STEP.__init__ is not called
        super().__init__(config)
        self.auto_tweet = config.get('gsheet_feeder').get("auto_tweet")
        self.fb_archiver = config.get('gsheet_feeder').get("fb_archiver")
        self.uwazi_integration = config.get('gsheet_feeder').get("uwazi_integration")

    @staticmethod
    def configs() -> dict:
        return {}

    def started(self, item: Metadata) -> None:
        logger.info(f"STARTED {item}")
        gw, row = self._retrieve_gsheet(item)
        gw.set_cell(row, 'status', 'Archive in progress')
        spreadsheet = gw.wks.spreadsheet.title
        worksheet = gw.wks.title
        logger.info(f" row: {row} on {spreadsheet} : {worksheet}")


    def failed(self, item: Metadata, reason:str) -> None:
        logger.error(f"FAILED {item}")
        self._safe_status_update(item, f'Archive failed {reason}')

    def aborted(self, item: Metadata) -> None:
        logger.warning(f"ABORTED {item}")
        self._safe_status_update(item, '')

    def fetch(self, item: Metadata) -> Union[Metadata, bool]:
        """check if the given item has been archived already"""
        return False

    def done(self, item: Metadata, cached: bool=False) -> None:
        """archival result ready - should be saved to DB"""
        logger.success(f"DONE {item.get_url()}")

        gw, row = self._retrieve_gsheet(item)
        # self._safe_status_update(item, 'done')

        spreadsheet = gw.wks.spreadsheet.title
        worksheet = gw.wks.title
        logger.success(f" row {row} on {spreadsheet} : {worksheet}")

        cell_updates = []
        row_values = gw.get_row(row)

        # DM hack - we are overwriting values in the FB archiver so don't check if blank already
        def batch_if_valid(col, val, final_value=None):
            final_value = final_value or val
            try:
                # DM
                # if val and gw.col_exists(col) and gw.get_cell(row_values, col) == '':
                if val and gw.col_exists(col):
                    cell_updates.append((row, col, final_value))
            except Exception as e:
                logger.error(f"Unable to batch {col}={final_value} due to {e}")
        status_message = item.status
        if cached:
            status_message = f"[cached] {status_message}"
        cell_updates.append((row, 'status', status_message))

        media: Media = item.get_final_media()
        if hasattr(media, "urls"):
            batch_if_valid('archive', "\n".join(media.urls))
        batch_if_valid('date', True, datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc).isoformat())
        batch_if_valid('title', item.get_title())
        batch_if_valid('text', item.get("content", ""))
        batch_if_valid('timestamp', item.get_timestamp())

        # DM 2nd oct 24 - add wayback status to spreadsheet
        # batch_if_valid('wayback_status', item.get("wayback_status_from_enricher", ""))
        batch_if_valid('wayback_status', item.get("wayback_status", ""))
        batch_if_valid('archive_detail', item.get("archive_detail", ""))

        # DM July add for youtube extra data
        youtube_extra = False
        try:
            _ = gw.col_exists('view_count')
            youtube_extra = True
        except: pass

        if youtube_extra:
            all_media = item.get_all_media()
            duration = 0
            for m in all_media:
                foo = m.get('duration')
                if foo != None:
                    duration = foo
            batch_if_valid('duration', duration)

            # DM July - maybe better to have view_count on the media, but lets see
            view_count = item.get("view_count")
            batch_if_valid('view_count', view_count)

            location = item.get("location")
            batch_if_valid('location', location)

            comment_count = item.get("comment_count")
            batch_if_valid('comment_count', comment_count)

            like_count = item.get("like_count")
            batch_if_valid('like_count', like_count)

            channel = item.get("channel")
            batch_if_valid('channel', channel)

            channel_follower_count = item.get("channel_follower_count")
            batch_if_valid('channel_follower_count', channel_follower_count)

            # set the screen1, screen2, screen3, screen4
            # there may be a raw video downloaded or not
            # first_media = all_media[0]
            for m in all_media[0:]:
                if m.filename.endswith('1.png'):
                    # =IMAGE("./tmp4lzmqs7x/1.png")
                    # 'pl029/efcdc26649e24290800ebe12.png'
                    foo = "https://pluro.nyc3.cdn.digitaloceanspaces.com/" + m.key
                    # batch_if_valid('screen1', f'=IMAGE("{foo}")')
                    batch_if_valid('screen1', f'{foo}')
                if m.filename.endswith('2.png'):
                    foo = "https://pluro.nyc3.cdn.digitaloceanspaces.com/" + m.key
                    # batch_if_valid('screen2', f'=IMAGE("{foo}")')
                    batch_if_valid('screen2', f'{foo}')
                if m.filename.endswith('3.png'):
                    foo = "https://pluro.nyc3.cdn.digitaloceanspaces.com/" + m.key
                    # batch_if_valid('screen3', f'=IMAGE("{foo}")')
                    batch_if_valid('screen3', f'{foo}')
                if m.filename.endswith('4.png'):
                    foo = "https://pluro.nyc3.cdn.digitaloceanspaces.com/" + m.key
                    # batch_if_valid('screen4', f'=IMAGE("{foo}")')
                    batch_if_valid('screen4', f'{foo}')


        # DM - if Archive status is wayback, then don't write hash to spreadsheet
        # or no archiver we don't want hash in spreadsheet
        if item.status == 'wayback: success':
            pass
        elif item.status == "no archiver":
            pass
        else:
            if media: batch_if_valid('hash', media.get("hash", "not-calculated"))

        # merge all pdq hashes into a single string, if present
        pdq_hashes = []
        all_media = item.get_all_media()
        for m in all_media:
            if pdq := m.get("pdq_hash"):
                pdq_hashes.append(pdq)
        if len(pdq_hashes):
            batch_if_valid('pdq_hash', ",".join(pdq_hashes))

        if (screenshot := item.get_media_by_id("screenshot")) and hasattr(screenshot, "urls"):
            batch_if_valid('screenshot', "\n".join(screenshot.urls))

        if (thumbnail := item.get_first_image("thumbnail")):
            if hasattr(thumbnail, "urls"):
                batch_if_valid('thumbnail', f'=IMAGE("{thumbnail.urls[0]}")')

        if (browsertrix := item.get_media_by_id("browsertrix")):
            batch_if_valid('wacz', "\n".join(browsertrix.urls))
            batch_if_valid('replaywebpage', "\n".join([f'https://replayweb.page/?source={quote(wacz)}#view=pages&url={quote(item.get_url())}' for wacz in browsertrix.urls]))

        # DM Image URL1,2,3,4 and Video URL1,2 feature
        # only run this feature if the column exists in the definition ie in orchestration in the db (assume if first column exists, then others do)
        image_and_video_url_feature = False
        try:
            _ = gw.col_exists('image_url1')
            image_and_video_url_feature = True
        except: pass

        if image_and_video_url_feature:
            # for uwazi import below lets assign default values for urls
            # image_url1 = image_url2 = image_url3 = image_url4 = video_url1 = video_url2 = '' 

            # get first media
            # if there is no media then there will be a screenshot 
            try:
                first_media = all_media[0]
                # a screenshot has no source, so this returns None.
                first_media_url= first_media.get('src')

                # is it a twitter video?
                if (first_media_url is not None and '.mp4' in first_media_url):
                    # will only write to spreadsheet if the column is defined in orchestration
                    batch_if_valid('video_url1', first_media_url)
                    # video_url1 = first_media_url
                # is it a youtubedlp video ie local?
                elif 'video' in first_media.mimetype:
                    first_media_url = first_media.urls[0]
                    batch_if_valid('video_url1', first_media_url)
                # instagram
                elif 'image/webp' in first_media.mimetype:
                    first_media_url = first_media.urls[0]
                    batch_if_valid('image_url1', first_media_url)
                else:
                    batch_if_valid('image_url1', first_media_url)
                    # image_url1 = first_media_url

            except Exception as e:
                pass

            try:
                # if multiple videos then we have thumbnails which we don't want to consider
                # so lets filter out any with properties of id thumbnail_
                new_array = []
                for media in all_media[1:]:
                    dd = media.get('id')
                    if dd is None:
                        new_array.append(media) 
                second_media = new_array[0]
                second_media_url= second_media.get('src')

                # is it a twitter video?
                if ('.mp4' in second_media_url):
                    batch_if_valid('video_url2', second_media_url)
                else:
                    batch_if_valid('image_url2', second_media_url)
            except Exception as e:
                pass

            try:
                third_media = all_media[2]
                third_media_url= third_media.get('src')
                batch_if_valid('image_url3', third_media_url)
            except: pass

            try:
                fourth_media = all_media[3]
                fourth_media_url= fourth_media.get('src')
                batch_if_valid('image_url4', fourth_media_url)
            except: pass


        # DM trying to catch APIError if Google if offline
        # gspread.exceptions.APIError: APIError: [503]: The service is currently unavailable.
        # this is a problem as it will leave the status as Archive in progress
        gw.batch_set_cell(cell_updates)

        ## DM hack in auto tweet
        # if item.status =='wacz: success':
        if item.status == 'nothing archived':
            logger.debug("nothing archiver - maybe an error in instagram archiving, so no hash to send")
        else:
            hash = media.get("hash")
            logger.info(f'{hash=}')
            if (hash == None):
                logger.debug("no hash so write to spreadsheet and continue")
            elif item.status == 'wayback: success':
                logger.debug("dont want wayback hashes - may be a normal website or facebook which other archvier will get soon")
            elif (self.auto_tweet == False):
                logger.debug("auto_tweet not enabled in config")
            elif (cred_mssql.server == ''):
                logger.debug("no db for auto twitter so write to spreadsheet and continue")
                    # elif ('facebook.com' in item.get_url()) and (self.fb_archiver == False):
                # logger.info('normal archiver doing a wayback archive for facebook, so dont want to write hash as facebook archiver will do that')
            else:
                retry_flag = True
                retry_count = 0

                # DocumentName eg  AA Demo Main
                document_name = gw.wks.spreadsheet.title

                # TabName eg Sheet1
                tab_name = gw.wks.title

                # Entry Number eg AA008
                entry_number = row_values[0]

                while retry_flag and retry_count < 5:
                    try:
                        cnxn = pyodbc.connect('DRIVER={ODBC Driver 18 for SQL Server};SERVER='+cred_mssql.server+';DATABASE='+cred_mssql.database+';ENCRYPT=yes;UID='+cred_mssql.username+';PWD='+ cred_mssql.password)
                        cursor = cnxn.cursor()

                        cursor.execute(
                            'INSERT INTO Hash (HashText, DocumentName, TabName, EntryNumber, HasBeenTweeted) VALUES (?,?,?,?,?)',
                            hash, document_name, tab_name, entry_number, '0')
                        cnxn.commit()

                        retry_flag = False
                    except Exception as e:
                        logger.error(f'Hash is {hash}, DocumentName: {document_name}, TabName: {tab_name}, EntryNumber: {entry_number}')
                        logger.error(f"DB Retry after 30 secs as {e}")
                        retry_count = retry_count + 1
                        time.sleep(30)
                
                if (retry_flag): pass
                    # insert failed into db so alert on sheet
                    # result.status = result.status + " TWEET FAILED"
                else:
                    logger.success(f"Inserted hash into db {hash}")



    def _safe_status_update(self, item: Metadata, new_status: str) -> None:
        try:
            gw, row = self._retrieve_gsheet(item)
            gw.set_cell(row, 'status', new_status)
        except Exception as e:
            logger.debug(f"Unable to update sheet: {e}")

    def _retrieve_gsheet(self, item: Metadata) -> Tuple[GWorksheet, int]:
        # TODO: to make gsheet_db less coupled with gsheet_feeder's "gsheet" parameter, this method could 1st try to fetch "gsheet" from ArchivingContext and, if missing, manage its own singleton - not needed for now
        if gsheet := ArchivingContext.get("gsheet"):
            gw: GWorksheet = gsheet.get("worksheet")
            row: int = gsheet.get("row")
        elif self.sheet_id:
            print(self.sheet_id)


        return gw, row
