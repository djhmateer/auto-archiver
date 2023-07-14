import os, datetime, traceback, random, tempfile

from loguru import logger
from slugify import slugify

from archivers import TelethonArchiver, TelegramArchiver, TiktokArchiver, YoutubeDLArchiver, TwitterArchiver, TwitterApiArchiver, VkArchiver, WaybackArchiver, ArchiveResult, Archiver, FacebookArchiver
from utils import GWorksheet, mkdir_if_not_exists, expand_url
from configs import Config
from storages import Storage

# credentials for db - need something to be there for code to work!
import cred_mssql
import pyodbc 

random.seed()

# This is a specialised version of auto_archive.py which only runs the facebook_archiver

# I have kept the code as similar as possible to auto_archive.py
# even leaving in commented out code
# so it should be easier ot update this when auto_archive.py is updated

# This runs on a different server to the main archiver because of facebook blocking issues
# so running from a server inside the residential IP address works

def update_sheet(gw, row, result: ArchiveResult):
    cell_updates = []
    row_values = gw.get_row(row)

    def batch_if_valid(col, val, final_value=None):
        final_value = final_value or val

        # if val and gw.col_exists(col) and gw.get_cell(row_values, col) == '':
        # DM for facebook archiver the values will not be '' (even though I reset them earlier in code it is a snapshot from before then)
        if val and gw.col_exists(col):
            cell_updates.append((row, col, final_value))

    cell_updates.append((row, 'status', result.status))

    batch_if_valid('archive', result.cdn_url)
    batch_if_valid('date', True, datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc).isoformat())
    batch_if_valid('thumbnail', result.thumbnail, f'=IMAGE("{result.thumbnail}")')
    batch_if_valid('thumbnail_index', result.thumbnail_index)
    batch_if_valid('title', result.title)
    batch_if_valid('duration', result.duration, str(result.duration))
    batch_if_valid('screenshot', result.screenshot)
    batch_if_valid('hash', result.hash)

    if result.timestamp is not None:
        if type(result.timestamp) == int:
            timestamp_string = datetime.datetime.fromtimestamp(result.timestamp).replace(tzinfo=datetime.timezone.utc).isoformat()
        elif type(result.timestamp) == str:
            timestamp_string = result.timestamp
        else:
            timestamp_string = result.timestamp.isoformat()

        batch_if_valid('timestamp', timestamp_string)

    gw.batch_set_cell(cell_updates)


def missing_required_columns(gw: GWorksheet):
    missing = False
    for required_col in ['url', 'status']:
        if not gw.col_exists(required_col):
            logger.warning(f'Required column for {required_col}: "{gw.columns[required_col]}" not found, skipping worksheet {gw.wks.title}')
            missing = True
    return missing


def should_process_sheet(c, sheet_name):
    if len(c.worksheet_allow) and sheet_name not in c.worksheet_allow:
        # ALLOW rules exist AND sheet name not explicitly allowed
        return False
    if len(c.worksheet_block) and sheet_name in c.worksheet_block:
        # BLOCK rules exist AND sheet name is blocked
        return False
    return True


def process_sheet(c: Config):
    sh = c.gsheets_client.open(c.sheet)

    # loop through worksheets to check
    for ii, wks in enumerate(sh.worksheets()):
        if not should_process_sheet(c, wks.title):
            logger.info(f'Ignoring worksheet "{c.sheet} - {wks.title}" due to allow/block configurations')
            continue

        logger.info(f'Opening worksheet {ii=}: {c.sheet} - {wks.title} {c.header=}')
        gw = GWorksheet(wks, header_row=c.header, columns=c.column_names)

        if missing_required_columns(gw): continue

        # archives will default to being in a folder 'doc_name/worksheet_name'
        default_folder = os.path.join(slugify(c.sheet), slugify(wks.title))
        c.set_folder(default_folder)
        storage = c.get_storage()

        # loop through rows in worksheet
        for row in range(1 + c.header, gw.count_rows() + 1):
            url = gw.get_cell(row, 'url')
            original_status = gw.get_cell(row, 'status')
            status = gw.get_cell(row, 'status', fresh=original_status in ['', None] and url != '')

            is_retry = False

            # condition for special FB archiver (which this version of auto-archiver.py is)
            if 'facebook.com/' in url:
                # logger.info(f"found facebook.com url {url} on {row=}")
                if status is not None:
                    # if the fb has worked with youtubedl, then we don't want to do it again.
                    # if it resorted to wayback we do
                    # if wayback failed (which is does) we want to try
                    # if 'wayback:' in status or 'failed:' in status:
                    if 'wayback:' in status or status.startswith('failed:'):
                        # check date
                        original_archive_date = gw.get_cell(row, 'date')
                        # eg 2023-01-03T08:56:12.055561+00:00
                        # an empty date so that we can rerun the FB archiver by setting status to: wayback: and not worry about the date
                        # which may still be blank 
                        if original_archive_date.startswith('2023-') or original_archive_date == "":
                            logger.info(f'date is {original_archive_date}')
                            logger.info(f'*** NEW FB {row} ***')
                            logger.info(f"the standard archiver has resorted to wayback, so lets run the specialised FB archiver {status=} {row=}")
                            logger.info("Setting columns to blank so new archiver can write into them")
                            # Archive status is set further down
                            cell_updates = []
                            cell_updates.append((row, 'archive', ''))
                            cell_updates.append((row, 'date', ''))
                            cell_updates.append((row, 'screenshot', ''))
                            cell_updates.append((row, 'hash', ''))
                            cell_updates.append((row, 'thumbnail', ''))
                            cell_updates.append((row, 'thumbnail_index', ''))
                            cell_updates.append((row, 'title', ''))
                            cell_updates.append((row, 'timestamp', ''))
                            cell_updates.append((row, 'duration', ''))
                            gw.batch_set_cell(cell_updates)
                            # keep going below
                        else:
                            # logger.info(f'fb link is not a 2023 date so do nothing')
                            continue # the for loop
                    else:
                            # logger.info(f"fb status is not wayback: so FB archiver has probably has done it already so do nothing {status=}")
                            continue # the for loop
                else:
                    logger.info(f"fb link Nothing in status, so main archiver not found it yet, so wait")
                    continue # the for loop
            else:
                # a non fb link has been found so ignore it
                continue # the for loop

            # DM - don't need this for facebook 
            # elif url == '' or status not in ['', None]:
            #     # normal control flow if nothing to do ie it has been archived already
            #     is_retry = Archiver.should_retry_from_status(status)
            #     if not is_retry: continue # the for row loop

            # archiver proceeds

            # not good to have in here - miguel
            # if using folder based storage make sure an folder/Entry Number is specified eg MW0001
            if c.storage == 'gd':
                entry_number = gw.get_cell(row, 'folder')
                # Some Slack integration writes a URL before an Entry Number is written so just leave for next time
                if entry_number == "": 
                    logger.warning(f'Missing entry number - waiting for next run')
                    continue

            # All checks done - archival process starts here
            try: 
                # gw.set_cell(row, 'status', 'Archive in progress')
                gw.set_cell(row, 'status', 'FB Archiver in progress')
                url = expand_url(url)
                # if no folder eg TH054 then use Google Worksheet/Title
                c.set_folder(gw.get_cell_or_default(row, 'folder', default_folder, when_empty_use_default=True))

                # make a new driver so each spreadsheet row is idempotent
                c.recreate_webdriver()

                # order matters, first to succeed excludes remaining
                active_archivers = [
                    # TelethonArchiver(storage, c.webdriver, c.telegram_config, c.hash_algorithm),
                    # TiktokArchiver(storage, c.webdriver, c.hash_algorithm),
                    # TwitterApiArchiver(storage, c.webdriver, c.twitter_config, c.hash_algorithm),
                    # YoutubeDLArchiver(storage, c.webdriver, c.facebook_cookie,c.hash_algorithm),
                    # TelegramArchiver(storage, c.webdriver, c.hash_algorithm),
                    # TwitterArchiver(storage, c.webdriver, c.hash_algorithm),
                    # VkArchiver(storage,  c.webdriver, c.vk_config, c.hash_algorithm),
                    FacebookArchiver(storage, c.webdriver, c.brightdata_proxy_secret, c.hash_algorithm)
                    # WaybackArchiver(storage, c.webdriver, c.wayback_config, c.hash_algorithm)
                ]

                for archiver in active_archivers:
                    logger.debug(f'{entry_number} Trying {archiver} on {row=}')

                    try:
                        result = archiver.download(url, check_if_exists=c.check_if_exists)
                    except KeyboardInterrupt as e: raise e  # so the higher level catch can catch it
                    except Exception as e:
                        result = False
                        logger.error(f'Got unexpected error in row {row} with {archiver.name} for {url=}: {e}\n{traceback.format_exc()}')

                    if result:
                        success = result.status in ['success', 'already archived']
                        result.status = f"{archiver.name}: {result.status}"
                        if success:
                            logger.success(f'{entry_number} {archiver.name} succeeded on {row=}, {url=}')
                            break
                        # only 1 retry possible for now
                        if is_retry and Archiver.is_retry(result.status):
                            result.status = Archiver.remove_retry(result.status)
                        logger.warning(f'{archiver.name} did not succeed on {row=}, final status: {result.status}')

                if result:
                    # do an auto tweet (well, write to the db hash queue)
                    # as twitter is limiting the number of tweets we have to queue them up

                    # credentials are in cred_mssql.py which is copied (not in source control)
                
                    # simple retry from https://stackoverflow.com/a/41480876/26086
                    # exponential backoff would be better like polly
                    if (result.hash == None):
                        logger.debug("Result is fine, no hash, probable wayback, so write to spreadsheet and continue")
                    else:
                        retry_flag = True
                        retry_count = 0
                        while retry_flag and retry_count < 5:
                            try:
                                cnxn = pyodbc.connect('DRIVER={ODBC Driver 18 for SQL Server};SERVER='+cred_mssql.server+';DATABASE='+cred_mssql.database+';ENCRYPT=yes;UID='+cred_mssql.username+';PWD='+ cred_mssql.password)
                                cursor = cnxn.cursor()

                                cursor.execute(
                                    'INSERT INTO Hash (HashText, HasBeenTweeted) VALUES (?,?)',
                                    result.hash, '0')
                                cnxn.commit()

                                retry_flag = False
                            except Exception as e:
                                logger.error(f'Hash problem is {result.hash}')
                                logger.error(f"DB Retry after 30 secs as {e}")
                                retry_count = retry_count + 1
                                time.sleep(30)
                            
                        if (retry_flag):
                            # insert failed into db so alert on sheet
                            result.status = result.status + " TWEET FAILED"
                        else:
                            logger.success(f"Inserted hash into db {result.hash}")

                    update_sheet(gw, row, result)
                else:
                    gw.set_cell(row, 'status', 'FB failed: no archiver')
            except KeyboardInterrupt:
                # catches keyboard interruptions to do a clean exit
                logger.warning(f"caught interrupt on {row=}, {url=}")
                gw.set_cell(row, 'status', '')
                c.destroy_webdriver()
                exit()
            except Exception as e:
                logger.error(f'Got unexpected error in row {row} for {url=}: {e}\n{traceback.format_exc()}')
                gw.set_cell(row, 'status', 'failed: unexpected error (see logs)')
        logger.success(f'Finished worksheet {c.sheet} - {wks.title}')


@logger.catch
def main():
    c = Config()
    c.parse()
    logger.info(f'Opening document {c.sheet} for header {c.header}')
    with tempfile.TemporaryDirectory(dir="./") as tmpdir:
        Storage.TMP_FOLDER = tmpdir
        process_sheet(c)
        c.destroy_webdriver()


if __name__ == '__main__':
    main()
