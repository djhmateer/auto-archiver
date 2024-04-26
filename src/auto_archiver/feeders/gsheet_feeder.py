import gspread, os

from loguru import logger
from slugify import slugify
import json

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
                },
                "uwazi_case_template_id": {
                    "default": '',
                    "help": "",
                }

            })

    def __iter__(self) -> Metadata:
        # DM TEST this is where an APIErrpr can happen if google sheets API is offline
        try:
            sh = self.open_sheet()
        except gspread.exceptions.APIError as e:
            logger.warning(f"**DM caught gspread.exceptions.APIError and raising again {e}")
            raise
        except Exception as e:
            logger.warning(f"**DM caught Exception in gsheet_feeder and raising again {e}")
            raise

        # DM make sure Incidents worksheet is enumerated first if exists
        def custom_sort(wks):
            #Prioritize 'Incidents' by giving it a lower sort value
            return (0 if wks.title == 'Incidents' else 1, wks.title)

        sorted_worksheets = sorted(sh.worksheets(), key=custom_sort)

        # for ii, wks in enumerate(sh.worksheets()):
        for ii, wks in enumerate(sorted_worksheets):

            logger.info(f'Opening worksheet {ii=}: {sh.title} {wks.title=} header={self.header}')

            try:
                gw = GWorksheet(wks, header_row=self.header, columns=self.columns)
            except:
                logger.debug('exception trying read header - probable block this sheet, but need this for uwazi. This is probably fine.')

            # special case for Uwazi integration to process the Incidents tab
            # both conditions have to be true
            if self.uwazi_integration == True and wks.title == "Incidents":
               logger.debug("Found uwazi integration Incidents (CASES) sheet to process - doing this first before Sheet1")

               # New CASES (maybe an Incident)
               # reading from Incidents tab only
               for row in range(1 + self.header, gw.count_rows() + 1):
                    # has to have a case_id ie not blank rows
                    url = gw.get_cell(row, 'icase_id').strip()
                    if not len(url): continue

                    iimport_to_uwazi = gw.get_cell(row, 'iimport_to_uwazi')
                    if iimport_to_uwazi == 'y': pass
                    else:
                        # logger.debug('Skipping incident as not y in import_to_uwazi')
                        continue

                    idate_imported_to_uwazi = gw.get_cell(row, 'idate_imported_to_uwazi').strip()
                    if idate_imported_to_uwazi == "": pass
                    else:
                        # logger.debug('Incident already imported to Uwazi')
                        continue

 
                    import_to_uwazi_notes = ''

                    ititle = gw.get_cell(row, 'ititle')
                    icase_id = gw.get_cell(row, 'icase_id')

                    logger.info(f'Importing {icase_id} {ititle}')

                    idescription = gw.get_cell(row, 'idescription')
                    ineighbourhood = gw.get_cell(row, 'ineighbourhood')

                    # Date_Reported 
                    idate_reported = gw.get_cell(row, 'idate_reported')
                    idate_reported_unix = 0
                    if idate_reported == "": pass
                    else:
                        try:
                            datetime_obj = datetime.fromisoformat(idate_reported)
                            idate_reported_unix = datetime_obj.replace(tzinfo=timezone.utc).timestamp()
                        except:
                            message = 'Unknown idate_reported timestamp converstion from iso. '
                            logger.warning(message)
                            import_to_uwazi_notes = message

                    # Date_Accessed
                    idate_assessed = gw.get_cell(row, 'idate_assessed')
                    idate_assessed_unix = 0
                    if idate_assessed == "": pass
                    else:
                        try:
                            datetime_obj = datetime.fromisoformat(idate_assessed)
                            idate_assessed_unix = datetime_obj.replace(tzinfo=timezone.utc).timestamp()
                        except:
                            message = 'Unknown idate_assessed timestamp converstion from iso. '
                            logger.warning(message)
                            import_to_uwazi_notes += message

                    uwazi_adapter = UwaziAdapter(user=self.uwazi_user, password=self.uwazi_password, url=self.uwazi_url) 

                    # HARM_SOURCE - single select
                    harm_source_from_spreadsheet = gw.get_cell(row, 'iharm_source')

                    harm_source_dictionary_element_id = None
                    if harm_source_from_spreadsheet == '': 
                        logger.debug("no harm source from spreadsheet")
                    else:
                         harm_source_dictionary_element_id = uwazi_adapter.entities.get_dictionary_element_id_by_dictionary_name_and_element_title("HARM_SOURCE", harm_source_from_spreadsheet)

                         if harm_source_dictionary_element_id is None:
                            message = f"Dictionary element in HARM_SOURCE not found: {harm_source_from_spreadsheet}. "
                            logger.warning(message)
                            import_to_uwazi_notes += message


                    # OBJECT_AFFECTED - multi select with groups in the thesauri/dictionary
                    object_affected_from_spreadsheet = gw.get_cell(row, 'iobject_affected')
                    objects = object_affected_from_spreadsheet.split(',')

                    object_affected_list = []
                    if objects == ['']:
                        logger.debug('no object_affected from spreadsheet')
                    else:
                        for o in objects:
                            # what if Medical is passed which is a group name - business rule.. don't do this.
                            # need to pass all the element names explicitly if want them all
                            
                            object_affected_dictionary_element_id  = uwazi_adapter.entities.get_dictionary_element_id_by_dictionary_name_and_element_title("OBJECT_AFFECTED", o)
                            if object_affected_dictionary_element_id is None:
                                message = f'Couldnt find {o} in dictionary for OBJECT AFFECTED so not appending. '
                                logger.debug(message)
                                import_to_uwazi_notes += message
                            else:
                                object_affected_list.append(object_affected_dictionary_element_id)

                    # create json list to send
                    object_affected_result_list = []
                    for oa in object_affected_list:
                        # Create a new dictionary with the current value
                        new_dict = {"value": oa}
                        object_affected_result_list.append(new_dict)


                    # PEOPLE_HARMED - multi select but no groups 
                    people_harmed_from_spreadsheet = gw.get_cell(row, 'ipeople_harmed')
                    objects = people_harmed_from_spreadsheet.split(',')

                    people_harmed_list = []

                    if objects == ['']:
                        logger.debug('no people_harmed from spreadsheet')
                    else:
                        for o in objects:
                            people_harmed_dictionary_element_id  = uwazi_adapter.entities.get_dictionary_element_id_by_dictionary_name_and_element_title("PEOPLE_HARMED", o)
                            if people_harmed_dictionary_element_id is None:
                                message = f'Couldnt find {o} in dictionary for PEOPLE_HARMED so not appending. '
                                logger.debug(message)
                                import_to_uwazi_notes += message
                            else:
                                people_harmed_list.append(people_harmed_dictionary_element_id)

                    # create json list to send
                    people_harmed_result_list = []
                    for ph in people_harmed_list:
                        # Create a new dictionary with the current value
                        new_dict = {"value": ph}
                        people_harmed_result_list.append(new_dict)


                    # GOVERNORATE - single select
                    governorate_from_spreadsheet = gw.get_cell(row, 'igovernorate')

                    governorate_dictionary_element_id = None
                    if governorate_from_spreadsheet == '': 
                        logger.debug("no governorate from spreadsheet")
                    else:
                         governorate_dictionary_element_id = uwazi_adapter.entities.get_dictionary_element_id_by_dictionary_name_and_element_title("GOVERNORATE", governorate_from_spreadsheet)

                         if governorate_dictionary_element_id is None:
                            message = f"Dictionary element in GOVERNORATE not found: {governorate_from_spreadsheet}. "
                            logger.warning(message)
                            import_to_uwazi_notes += message

                        
                    # CAMP - single select
                    camp_from_spreadsheet = gw.get_cell(row, 'icamp')

                    camp_dictionary_element_id = None
                    if camp_from_spreadsheet == '': 
                        logger.debug("no camp from spreadsheet")
                    else:
                         camp_dictionary_element_id = uwazi_adapter.entities.get_dictionary_element_id_by_dictionary_name_and_element_title("CAMP", camp_from_spreadsheet)

                         if camp_dictionary_element_id is None:
                            message = f"Dictionary element in CAMP not found: {camp_from_spreadsheet}. "
                            logger.warning(message)
                            import_to_uwazi_notes += message

                    # GEOLOCATION with comma or pipe
                    igeolocation = gw.get_cell(row, 'igeolocation').strip()
                    if igeolocation != "":
                        try:
                            if "," in igeolocation:
                                parts = igeolocation.split(",", 1)
                            elif "|" in igeolocation:
                                parts = igeolocation.split("|", 1) 

                            lat = float(parts[0].strip())
                            long = float(parts[1].strip())
                            geolocation = [{
                                "value": {
                                    "lat": lat,
                                    "lon": long,
                                    "label": ""
                                }
                            }]
                        except:
                            message = f"Geolocation failed to parse {igeolocation}. "
                            logger.warning(message)
                            import_to_uwazi_notes += message
                            geolocation = []
                    else: 
                        geolocation = []


                    # CASE_NATURE - single select
                    case_nature_from_spreadsheet = gw.get_cell(row, 'icase_nature').strip()    
                    case_nature_dictionary_element_id = None
                    if case_nature_from_spreadsheet == '': 
                        logger.debug("no case_nature from spreadsheet")
                    else:
                         case_nature_dictionary_element_id = uwazi_adapter.entities.get_dictionary_element_id_by_dictionary_name_and_element_title("CASE_NATURE", case_nature_from_spreadsheet)

                         if case_nature_dictionary_element_id is None:
                            message = f"Dictionary element in CASE_NATURE not found in Uwazi: {case_nature_from_spreadsheet}. "
                            logger.warning(message)
                            import_to_uwazi_notes += message

                    
                    # Create a new CASE
                    case_entity = {
                            'title': ititle,
                            'template': self.uwazi_case_template_id, 
                            "documents": [],
                            "metadata": {
                                "generated_id": [ { "value": icase_id} ],
                                "image": [ { "value": "" } ],
                                "case_id": [ { "value": icase_id } ],
                                "description": [ { "value": idescription } ],
                                "neighbourhood": [ { "value": ineighbourhood } ],
                                "date_reported": [ { "value": idate_reported_unix } ],
                                "date_assessed": [ { "value": idate_assessed_unix } ],
                                "harm_source": [ { "value": harm_source_dictionary_element_id } ],
                                "object_affected": object_affected_result_list,
                                "people_harmed": people_harmed_result_list,
                                "governorate": [ { "value": governorate_dictionary_element_id } ],
                                "camp": [ { "value": camp_dictionary_element_id } ],
                                "case_nature": [ { "value": case_nature_dictionary_element_id } ],
                                "geolocation_geolocation": geolocation
                                }
                        }

                    uwazi_adapter = UwaziAdapter(user=self.uwazi_user, password=self.uwazi_password, url=self.uwazi_url) 
                    case_shared_id = uwazi_adapter.entities.upload(entity=case_entity, language='en')
                    if case_shared_id.startswith('Error'):
                        # case_shared_id contains the error message - look in Entities upload.
                        message = f"{icase_id}  - {case_shared_id}"
                        logger.warning(message)
                        import_to_uwazi_notes += message
                    else: 
                        logger.success(f'Sent new CASE to Uwazi - {ititle}')

                    gw.set_cell(row, 'idate_imported_to_uwazi',datetime.utcnow().replace(tzinfo=timezone.utc).isoformat())

                    if import_to_uwazi_notes == '': import_to_uwazi_notes = "success"
                    gw.set_cell(row, 'iimport_to_uwazi_notes', import_to_uwazi_notes)

               continue
            elif not self.should_process_sheet(wks.title):
                logger.debug(f"SKIPPED worksheet '{wks.title}' due to allow/block rules")
                continue

            if len(missing_cols := self.missing_required_columns(gw)):
                logger.warning(f"SKIPPED worksheet '{wks.title}' due to missing required column(s) for {missing_cols}")
                continue

            # Normal archiving loop including Content to Uwazi
            for row in range(1 + self.header, gw.count_rows() + 1):
                url = gw.get_cell(row, 'url').strip()
                if not len(url): continue

                original_status = gw.get_cell(row, 'status')

                # special case Uwazi Integration
                # we need image_url1 etc to be working - see gsheet_db.py - essentially just need column names there are it will auto populate
                if self.uwazi_integration == True:
                    # we're using keep_going rather than continue as the normal archiver run 
                    # will go through this code too for Glan and needs to keep on going

                    keep_going = True
                    # has the archiver already been run?
                    if original_status == '':
                        logger.debug('Archiver not been run for the first time, so dont even check if y is in import_to_uwazi')
                        keep_going = False

                    if keep_going:
                        # check uwazi column exists
                        try:
                            _ = gw.col_exists('import_to_uwazi')
                        except: 
                            keep_going = False
                            logger.error('Uwazi feature is on but import_to_uwazi column not present')
                            continue # to next row. 

                     # is import_to_uwazi column 'y'
                    if keep_going:
                        stu = gw.get_cell(row, 'import_to_uwazi').lower()
                        if stu == 'y':
                            pass
                        else:
                            keep_going = False

                    # have we already sent it to uwazi?
                    if keep_going:
                        di = gw.get_cell(row, 'date_imported_to_uwazi').lower()
                        if di == '':
                            pass
                        else:
                            # logger.debug('already imported to uwazi so ignore')
                            keep_going = False

                    # assume that user only presses y to uwazi if a successful archive has taken place
                    if keep_going:
                        import_to_uwazi_notes = ''

                        entry_number = gw.get_cell(row, 'folder')

                        logger.debug(f'sending Content  {entry_number} to Uwazi!')

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
                                message = 'unknown dateposted timestamp converstion from iso.'
                                logger.warning(message)
                                import_to_uwazi_notes += message

                        # a description
                        upload_title = gw.get_cell(row, 'title')

                        hash = gw.get_cell(row, 'hash')

                        # digital ocean link
                        archive_location = gw.get_cell(row, 'archive_location')

                        # geolocation_geolocation
                        geolocation = gw.get_cell(row, 'geolocation_geolocation')

                        if geolocation == "case" or geolocation == "CASE": 
                            # handle further down as need to copy from the case
                            pass
                        elif geolocation == "":
                            # do nothing and leave blank
                             geolocation_geolocation = []
                        else:
                            try:
                                if "," in geolocation:
                                    parts = geolocation.split(",", 1)
                                elif "|" in geolocation:
                                    parts = geolocation.split("|", 1) 

                                lat = float(parts[0].strip())
                                long = float(parts[1].strip())
                                geolocation_geolocation = [{
                                    "value": {
                                        "lat": lat,
                                        "lon": long,
                                        "label": ""
                                    }
                                }]
                            except:
                                message = f"geolocation failed to parse {parts}"
                                logger.warning(message)
                                import_to_uwazi_notes +=  message
                                geolocation_geolocation = []

                        # HERE - need to figure out the CASE entity value
                        # get it from the spreadsheet CASE
                        # eg GAZ088
                        case_id = gw.get_cell(row, 'case_id')

                        if len(case_id) == 0:
                            message = 'NOT IMPORTED - CASE_ID not found in spreadsheet - not imported into Uwazi as each Content template entity should have a CASE'
                            logger.warning(message)
                            import_to_uwazi_notes += message
                            gw.set_cell(row, 'import_to_uwazi_notes', import_to_uwazi_notes)
                            # set date_imported as otherwise it will try every run to import
                            gw.set_cell(row, 'date_imported_to_uwazi',datetime.utcnow().replace(tzinfo=timezone.utc).isoformat())
                            continue

                        description = gw.get_cell(row, 'description')

                        screenshot = gw.get_cell(row, 'screenshot')

                        # Does this CASE exist in Uwazi already?
                        # It should have been created above if new
                        uwazi_adapter = UwaziAdapter(user=self.uwazi_user, password=self.uwazi_password, url=self.uwazi_url) 

                        # fooxx = uwazi_adapter.entities.get_shared_ids_search_by_case_id(self.uwazi_case_template_id, 30, case_id)
                        fooxx = uwazi_adapter.entities.get_shared_ids_search_v2_by_case_id(self.uwazi_case_template_id, case_id)

                        case_id_mongo = ''
                        if len(fooxx) == 0:
                            message = 'NOT IMPORTED as CASE not found - problem. It should have been created in Uwazi before'
                            logger.warning(message)
                            import_to_uwazi_notes += message
                            gw.set_cell(row, 'import_to_uwazi_notes', import_to_uwazi_notes)
                            # set date_imported as otherwise it will try every run to import
                            gw.set_cell(row, 'date_imported_to_uwazi',datetime.utcnow().replace(tzinfo=timezone.utc).isoformat())
                            continue

                        else:
                            # There were CASES found in the search
                            # if the search for GAZ088 came back with multiple CASES we would be in trouble
                            if len(fooxx) > 1:
                                # This should never happen but it might if there are multiples cases in Uwazi
                                message = f'Search term {case_id} found multiple CASES in Uwazi - duplicates in Uwazi? Have taken the last one in search results'
                                logger.warning(message)
                                import_to_uwazi_notes += message
                                # take the last one as it was probably the one we're after
                                case_id_mongo = fooxx[-1]
                            else:
                                case_id_mongo = fooxx[0]

                            # only if actively set to 'case' in the content spreadsheet should we copy from CASE
                            # get the geolocation of this CASE and copy it onto the new Content entity we are making
                            # if there isn't a geolocation there already
                            if geolocation == 'case' or geolocation == "CASE":
                                try:
                                      ggg = uwazi_adapter.entities.get_one(case_id_mongo, "en")
                                      case_geoloc_from_uwazi_json = ggg['metadata']['geolocation_geolocation'][0]['value']
                                      lat = case_geoloc_from_uwazi_json['lat']
                                      lon = case_geoloc_from_uwazi_json['lon']
                                      geolocation_geolocation = [{
                                            "value": {
                                                "lat": lat,
                                                "lon": lon,
                                                "label": ""
                                            }
                                        }]
                                except:
                                      logger.debug('no geolocation in Uwazi for this case')
                                
                            
                        # Content
                        entity = {
                                'title': uwazi_title,
                                'template': self.uwazi_content_template_id, 
                                "type": "entity",
                                "documents": [],
                                'metadata': {
                                    "description":[{"value":description}], 
                                    "screenshot2":[{"value":screenshot}], 
                                    
                                    "screenshot": [{
                                            "value": {
                                                "label": "screenshot",
                                                "url": screenshot
                                            }
                                        }],

                                    "video_url1":[{"value":video_url1}],
                                    "video_url2":[{"value":video_url2}],
                                    "image_url1":[{"value":image_url1}],
                                    "image_url2":[{"value":image_url2}],
                                    "image_url3":[{"value":image_url3}],
                                    "image_url4":[{"value":image_url4}],
                                    # "generated_id":[{"value":"KJY5630-3351"}], # need to generate something here to send it
                                    "generated_id":[{"value":entry_number}], 
                                    # "date_posted":[{"value":1644155025}], # 2022/02/06 13:43:45
                                    "date_posted":[{"value":unix_timestamp}], 
                                     # "case": [{ "value": "06oxg0tt4m1m" } ],
                                    "case": [{ "value": case_id_mongo } ],
                                    # "case":[],
                                    "geolocation_geolocation": geolocation_geolocation,
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
                        
                        # uploads the new Content Entity
                        shared_id = uwazi_adapter.entities.upload(entity=entity, language='en')

                        if shared_id.startswith('Error'):
                            message = f"{entry_number} - {shared_id}"
                            logger.error(message)
                            import_to_uwazi_notes += message
                        else:
                            logger.success(f'Sent new Content to Uwazi - {uwazi_title}')
                            # if successful import then write date to spreadsheet
                            gw.set_cell(row, 'date_imported_to_uwazi',datetime.utcnow().replace(tzinfo=timezone.utc).isoformat())
                        
                        if import_to_uwazi_notes == '': import_to_uwazi_notes = 'success'

                        gw.set_cell(row, 'import_to_uwazi_notes',import_to_uwazi_notes)
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
