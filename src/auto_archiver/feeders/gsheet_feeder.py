import gspread, os

from loguru import logger
from slugify import slugify

# from . import Enricher
from . import Feeder
from ..core import Metadata, ArchivingContext
from ..utils import Gsheets, GWorksheet
from datetime import datetime, timezone
from ..uwazi_api.UwaziAdapter import UwaziAdapter

class GsheetsFeeder(Gsheets, Feeder):
    name = "gsheet_feeder"

    def __init__(self, config: dict) -> None:
        # without this STEP.__init__ is not called
        super().__init__(config)
        self.gsheets_client = gspread.service_account(filename=self.service_account)

    @staticmethod
    def configs() -> dict:
        return dict(
            Gsheets.configs(),
            ** {
                "allow_worksheets": {
                    "default": set(),
                    "help": "(CSV) only worksheets whose name is included in allow are included (overrides worksheet_block), leave empty so all are allowed",
                    "cli_set": lambda cli_val, cur_val: set(cli_val.split(","))
                },
                "block_worksheets": {
                    "default": set(),
                    "help": "(CSV) explicitly block some worksheets from being processed",
                    "cli_set": lambda cli_val, cur_val: set(cli_val.split(","))
                },
                "use_sheet_names_in_stored_paths": {
                    "default": True,
                    "help": "if True the stored files path will include 'workbook_name/worksheet_name/...'",
                },
                "fb_archiver": {
                    "default": False,
                    "help": "if True only run on facebook.com urls. referenced in gsheet_feeder''",
                } ,
                "auto_tweet": {
                    "default": False,
                    "help": "DM - if True then auto tweet. see gsheet_feeder",
                },
                "uwazi_integration": {
                    "default": False,
                    "help": "DM - if True then send to Uwazi",
                },
                "uwazi_user": {
                    "default": '',
                    "help": "",
                },
                "uwazi_password": {
                    "default": '',
                    "help": "",
                },
                "uwazi_url": {
                    "default": '',
                    "help": "",
                },
                "uwazi_content_template_id": {
                    "default": '',
                    "help": "",
                }

            })

    def __iter__(self) -> Metadata:
        sh = self.open_sheet()
        for ii, wks in enumerate(sh.worksheets()):
            if not self.should_process_sheet(wks.title):
                logger.debug(f"SKIPPED worksheet '{wks.title}' due to allow/block rules")
                continue

            logger.info(f'Opening worksheet {ii=}: {sh.title} {wks.title=} header={self.header}')
            gw = GWorksheet(wks, header_row=self.header, columns=self.columns)

            if len(missing_cols := self.missing_required_columns(gw)):
                logger.warning(f"SKIPPED worksheet '{wks.title}' due to missing required column(s) for {missing_cols}")
                continue

            for row in range(1 + self.header, gw.count_rows() + 1):
                url = gw.get_cell(row, 'url').strip()
                if not len(url): continue

                original_status = gw.get_cell(row, 'status')

                # special case Uwazi
                # DM Tue 20th
                # we need image_url1 etc to be working - see gsheet_db.py - essentially just need column names there are it will auto populate
                if self.uwazi_integration == True:
                    keep_going = True
                    # check uwazi column exists
                    try:
                         _ = gw.col_exists('send_to_uwazi')
                    except: 
                        keep_going = False
                        logger.error('Uwazi feature is on but send_to_uwazi column not present')
                        continue # to next row. todo - throw

                    # we're using keep_going rather than continue as the normal archiver run 
                    # will go through this code too for Glan
                    if keep_going:
                        # has the archiver already been run?
                        if original_status == '':
                            logger.debug('Archiver not been run for the first time, so dont send yet to uwazi')
                            keep_going = False

                     # is send_to_uwazi column 'y'
                    if keep_going:
                        stu = gw.get_cell(row, 'send_to_uwazi').lower()
                        if stu == 'y':
                            pass
                        else:
                            keep_going = False

                    # have we already sent it uwazi?
                    if keep_going:
                        di = gw.get_cell(row, 'date_imported_to_uwazi').lower()
                        if di == '':
                            pass
                        else:
                            # logger.debug('already imported to uwazi so ignore')
                            keep_going = False

                    # assume that user only presses send to uwazi if a successful archive has taken plan
                    if keep_going:
                        logger.debug('sending to Uwazi!')

                        entry_number = gw.get_cell(row, 'folder')

                        uwazi_title = gw.get_cell(row, 'uwazi_title')
                        if uwazi_title == '':
                            uwazi_title = entry_number

                        link = gw.get_cell(row, 'url')

                        image_url1= gw.get_cell(row, 'image_url1')
                        image_url2= gw.get_cell(row, 'image_url2')
                        image_url3= gw.get_cell(row, 'image_url3')
                        image_url4= gw.get_cell(row, 'image_url4')

                        video_url1= gw.get_cell(row, 'video_url1')
                        video_url2= gw.get_cell(row, 'video_url2')

                        # Date Posted - make it Upload Timestamp (of the original image eg Twitter)
                        # it may be blank
                        # eg '2022-05-11T10:51:35+00:00'
                        upload_timestamp = gw.get_cell(row, 'timestamp')

                        # Convert the string to a datetime object
                        # it can be that it is blank (not sure why)
                        unix_timestamp = 0
                        if upload_timestamp == "":
                            pass
                        else:
                            try:
                                datetime_obj = datetime.fromisoformat(upload_timestamp)
                                unix_timestamp = datetime_obj.replace(tzinfo=timezone.utc).timestamp()
                            except:
                                logger.warning('unkown dateposted timestamp converstion from iso')
                                pass

                        # a description
                        upload_title = gw.get_cell(row, 'title')

                        hash = gw.get_cell(row, 'hash')

                        # digital ocean link
                        archive_location = gw.get_cell(row, 'archive_location')

                        # geolocation_geolocation
                        geolocation = gw.get_cell(row, 'geolocation_geolocation')


                        # todo - figure out a way to remove geolocation if we don't have it
                        if geolocation != "":
                            parts = geolocation.split(",", 1) 
                            lat = float(parts[0].strip())
                            long = float(parts[1].strip())
                            entity = {
                                'title': uwazi_title,
                                # 'template': '65c21763b86e4246e7ea57f6', # Content on pfsense
                                'template': self.uwazi_content_template_id, 
                                "type": "entity",
                                "documents": [],
                                'metadata': {
                                    "video_url1":[{"value":video_url1}],
                                    "video_url2":[{"value":video_url2}],
                                    # "image_url1":[{"value":""}],
                                    "image_url1":[{"value":image_url1}],
                                    "image_url2":[{"value":image_url2}],
                                    "image_url3":[{"value":image_url3}],
                                    "image_url4":[{"value":image_url4}],
                                    # "generated_id":[{"value":"KJY5630-3351"}], # need to generate something here to send it
                                    "generated_id":[{"value":entry_number}], 
                                    # "date_posted":[{"value":1644155025}], # 2022/02/06 13:43:45
                                    "date_posted":[{"value":unix_timestamp}], 
                                    "case":[],
                                    "upload_title":[{"value":upload_title}], 
                                    "hash":[{"value":hash}], 
                                    "link": [{
                                            "value": {
                                                "label": link,
                                                "url": link
                                            }
                                        }],
                                    "geolocation_geolocation": [{
                                                    "value": {
                                                        "lat": lat,
                                                        "lon": long,
                                                        "label": ""
                                                    }
                                                }],
                                    "archive_location": [{
                                            "value": {
                                                "label": archive_location,
                                                "url": archive_location
                                            }
                                        }]
                                    }
                                }
                        else:
                            entity = {
                                'title': uwazi_title,
                                # 'template': '65c21763b86e4246e7ea57f6', # Content on pfsense
                                'template': self.uwazi_content_template_id, 
                                "type": "entity",
                                "documents": [],
                                'metadata': {
                                    "video_url1":[{"value":video_url1}],
                                    "video_url2":[{"value":video_url2}],
                                    # "image_url1":[{"value":""}],
                                    "image_url1":[{"value":image_url1}],
                                    "image_url2":[{"value":image_url2}],
                                    "image_url3":[{"value":image_url3}],
                                    "image_url4":[{"value":image_url4}],
                                    # "generated_id":[{"value":"KJY5630-3351"}], # need to generate something here to send it
                                    "generated_id":[{"value":entry_number}], 
                                    # "date_posted":[{"value":1644155025}], # 2022/02/06 13:43:45
                                    "date_posted":[{"value":unix_timestamp}], 
                                    "case":[],
                                    "upload_title":[{"value":upload_title}], 
                                    "hash":[{"value":hash}], 
                                    "link": [{
                                            "value": {
                                                "label": link,
                                                "url": link
                                            }
                                        }],
                                    "archive_location": [{
                                            "value": {
                                                "label": archive_location,
                                                "url": archive_location
                                            }
                                        }]
                                    }
                                }

                        # uwazi_adapter = UwaziAdapter(user='admin', password='change this password now', url='http://pfsense:3000')
                        uwazi_adapter = UwaziAdapter(user=self.uwazi_user, password=self.uwazi_password, url=self.uwazi_url)
                        
                        # uploads the new Entity
                        shared_id = uwazi_adapter.entities.upload(entity=entity, language='en')

                        if shared_id is None:
                            logger.error('Problem with uploading to Uwazi')
                            gw.set_cell(row, 'date_imported_to_uwazi','Problem see logs')
                        else:
                            # if successful import then write date to spreadsheet
                            gw.set_cell(row, 'date_imported_to_uwazi',datetime.utcnow().replace(tzinfo=timezone.utc).isoformat())

                        continue # to next row of the sheet (don't want to run any more code below as have just sent data to Uwazi)

                # special code path for only FB archiver 
                # as we only want it to feed when facebook.com is in url and wayback in status 
                # ie normal archiver has ran already
                # todo long term - see if can get all archivers running on proxmox,
                # or maybe get fb working on cloud
                # then don't need a separate fb archiver
                if self.fb_archiver == True:
                    if 'facebook.com' in url and 'wayback:' in original_status:
                        # ignore 2022
                        original_archive_date = gw.get_cell(row, 'date')
                        if original_archive_date.startswith('2022-'): continue
                        
                        # do a fresh call to the sheet to make sure
                        status = gw.get_cell(row, 'status', fresh='wayback:' in original_status)
                        if 'wayback:' not in status: continue
                    else: 
                        continue

                else:
                    # normal code path - non fb archiver
                    # archive status has to be blank for it to work

                    # 30th Jan 24, when a status has been blanked, this comes back as True - good.
                    foo = original_status in ['', None]

                    # 30th Jan - refresh the status just in case
                    status = gw.get_cell(row, 'status', fresh=original_status in ['', None])

                    # 30th Jan - when refreshed cell comes back, it is now a string 'None'
                    # I had just done a pipenv update to gspread 6.0.0
                    # reverting to 5.12.4 works as expected
                    if status not in ['', None]: continue

                    # this worked with 6.0.0 but not happy as it may have other effects in codebase
                    # if status not in ['', None, 'None']: continue


                # All checks done - archival process starts here
                m = Metadata().set_url(url)
                ArchivingContext.set("gsheet", {"row": row, "worksheet": gw}, keep_on_reset=True)
                if gw.get_cell_or_default(row, 'folder', "") is None:
                    folder = ''
                else:
                    folder = slugify(gw.get_cell_or_default(row, 'folder', "").strip())
                    # if folder == '':
                if len(folder):
                    if self.use_sheet_names_in_stored_paths:
                        ArchivingContext.set("folder", os.path.join(folder, slugify(self.sheet), slugify(wks.title)), True)
                    else:
                        ArchivingContext.set("folder", folder, True)
                else:
                    # DM fail out of entire run
                    # should probably check that folder: entry number   is set.
                    raise ValueError("Cant find entry number on spreadsheet")

                yield m

            logger.info(f'Finished worksheet {sh.title} - {wks.title}')

    def should_process_sheet(self, sheet_name: str) -> bool:
        if len(self.allow_worksheets) and sheet_name not in self.allow_worksheets:
            # ALLOW rules exist AND sheet name not explicitly allowed
            return False
        if len(self.block_worksheets) and sheet_name in self.block_worksheets:
            # BLOCK rules exist AND sheet name is blocked
            return False
        return True

    def missing_required_columns(self, gw: GWorksheet) -> list:
        missing = []
        for required_col in ['url', 'status']:
            if not gw.col_exists(required_col):
                missing.append(required_col)
        return missing
