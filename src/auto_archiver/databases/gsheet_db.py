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


class GsheetsDb(Database):
    """
        NB: only works if GsheetFeeder is used. 
        could be updated in the future to support non-GsheetFeeder metadata 
    """
    name = "gsheet_db"

    def __init__(self, config: dict) -> None:
        # without this STEP.__init__ is not called
        super().__init__(config)

    @staticmethod
    def configs() -> dict:
        return {}

    def started(self, item: Metadata) -> None:
        logger.info(f"STARTED {item}")
        gw, row = self._retrieve_gsheet(item)
        gw.set_cell(row, 'status', 'Archive in progress')

    def failed(self, item: Metadata) -> None:
        logger.error(f"FAILED {item}")
        self._safe_status_update(item, 'Archive failed')

    def aborted(self, item: Metadata) -> None:
        logger.warning(f"ABORTED {item}")
        self._safe_status_update(item, '')

    def fetch(self, item: Metadata) -> Union[Metadata, bool]:
        """check if the given item has been archived already"""
        return False

    def done(self, item: Metadata) -> None:
        """archival result ready - should be saved to DB"""
        logger.success(f"DONE {item.get_url()}")
        gw, row = self._retrieve_gsheet(item)
        # self._safe_status_update(item, 'done')

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

        cell_updates.append((row, 'status', item.status))

        media: Media = item.get_final_media()
        if hasattr(media, "urls"):
            batch_if_valid('archive', "\n".join(media.urls))
        batch_if_valid('date', True, datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc).isoformat())
        batch_if_valid('title', item.get_title())
        batch_if_valid('text', item.get("content", ""))
        batch_if_valid('timestamp', item.get_timestamp())
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

        gw.batch_set_cell(cell_updates)

        ## DM hack in auto tweet
        # if item.status =='wacz: success':
        hash = media.get("hash")
        logger.info(f'{hash=}')
        if (hash == None):
            logger.debug("no hash so write to spreadsheet and continue")
        elif (cred_mssql.server == ''):
            logger.debug("no db for auto twitter so write to spreadsheet and continue")
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
                    logger.error(f'Hash problem is {hash}')
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
        gw: GWorksheet = ArchivingContext.get("gsheet").get("worksheet")
        row: int = ArchivingContext.get("gsheet").get("row")
        return gw, row
