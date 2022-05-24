import os
import datetime
import argparse
import requests
import shutil
import gspread
from loguru import logger
from dotenv import load_dotenv
from selenium import webdriver
import traceback

import archivers
from storages import S3Storage, S3Config
from storages.gd_storage import GDConfig, GDStorage
from utils import GWorksheet, mkdir_if_not_exists
import sys

import fnmatch
import os
from loguru import logger
import time
from pathlib import Path
import json

# pyton file shoudl be run from systemctl
# which will restrat it on reboot, and crash (just like Prepared Hatespeech)

# Looks for any guid.json files in input directory 
# which contains json of: "url":"https://twitter.com/dave_mateer/status/1524341442738638848"
# it does the archiving
# saving to guid.json in output directory

# user then can click on the storage link to see their files

logger.add("logs/1trace.log", level="TRACE")
logger.add("logs/2info.log", level="INFO")
logger.add("logs/3success.log", level="SUCCESS")
logger.add("logs/4warning.log", level="WARNING")
logger.add("logs/5error.log", level="ERROR")

load_dotenv()

def update_sheet(gw, row, result: archivers.ArchiveResult):
    cell_updates = []
    row_values = gw.get_row(row)

    def batch_if_valid(col, val, final_value=None):
        final_value = final_value or val
        if val and gw.col_exists(col) and gw.get_cell(row_values, col) == '':
            cell_updates.append((row, col, final_value))

    cell_updates.append((row, 'status', result.status))

    batch_if_valid('archive', result.cdn_url)
    batch_if_valid('date', True, datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc).isoformat())
    
    batch_if_valid('thumbnail', result.thumbnail,
                   f'=IMAGE("{result.thumbnail}")')
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


def expand_url(url):
    # expand short URL links
    if 'https://t.co/' in url:
        try:
            r = requests.get(url)
            url = r.url
        except:
            logger.error(f'Failed to expand url {url}')
    return url


def process_sheet(sheet, usefilenumber=False, storage="s3", header=1, columns=GWorksheet.COLUMN_NAMES):
    gc = gspread.service_account(filename='service_account.json')
    sh = gc.open(sheet)

    s3_config = S3Config(
        bucket=os.getenv('DO_BUCKET'),
        region=os.getenv('DO_SPACES_REGION'),
        key=os.getenv('DO_SPACES_KEY'),
        secret=os.getenv('DO_SPACES_SECRET')
    )
    gd_config = GDConfig(
        root_folder_id=os.getenv('GD_ROOT_FOLDER_ID'),
    )
    telegram_config = archivers.TelegramConfig(
        api_id=os.getenv('TELEGRAM_API_ID'),
        api_hash=os.getenv('TELEGRAM_API_HASH')
    )



    # loop through worksheets to check
    for ii, wks in enumerate(sh.worksheets()):
        logger.info(f'Opening worksheet {ii=}: {wks.title=} {header=}')
        gw = GWorksheet(wks, header_row=header, columns=columns)

        if not gw.col_exists('url'):
            logger.info(
                f'No "{columns["url"]}" column found, skipping worksheet {wks.title}')
            continue

        if not gw.col_exists('status'):
            logger.info(
                f'No "{columns["status"]}" column found, skipping worksheet {wks.title}')
            continue

        # archives will be in a folder 'doc_name/worksheet_name'
        s3_config.folder = f'{sheet.replace(" ", "_")}/{wks.title.replace(" ", "_")}/'
        s3_client = S3Storage(s3_config)

        gd_config.folder = f'{sheet.replace(" ", "_")}/{wks.title.replace(" ", "_")}/'
        gd_client = GDStorage(gd_config)

        # loop through rows in worksheet
        for row in range(1 + header, gw.count_rows() + 1):
            url = gw.get_cell(row, 'url')
            original_status = gw.get_cell(row, 'status')
            status = gw.get_cell(row, 'status', fresh=original_status in ['', None] and url != '')

            if url != '' and status in ['', None]:
                gw.set_cell(row, 'status', 'Archive in progress')

                url = expand_url(url)

                if usefilenumber:
                    filenumber = gw.get_cell(row, 'filenumber')
                    logger.debug(f'filenumber is {filenumber}')
                    if filenumber == "":
                        logger.warning(f'Logic error on row {row} with url {url} - the feature flag for usefilenumber is True, yet cant find a corresponding filenumber')
                        gw.set_cell(row, 'status', 'Missing filenumber')
                        continue
                else:
                    # We will use this through the app to differentiate between where to save
                    filenumber = None

                # make a new driver so each spreadsheet row is idempotent
                options = webdriver.FirefoxOptions()
                options.headless = True
                options.set_preference('network.protocol-handler.external.tg', False)

                driver = webdriver.Firefox(options=options)
                driver.set_window_size(1400, 2000)
                 # in seconds, telegram screenshots catch which don't come back
                driver.set_page_load_timeout(120)
        
                # client
                storage_client = None
                if storage == "s3":
                    storage_client = s3_client
                elif storage == "gd":
                    storage_client = gd_client
                else:
                    raise ValueError(f'Cant get storage_client {storage_client}')

                # order matters, first to succeed excludes remaining
                active_archivers = [
                    archivers.TelethonArchiver(storage_client, driver, telegram_config),
                    archivers.TelegramArchiver(storage_client, driver),
                    archivers.TiktokArchiver(storage_client, driver),
                    archivers.YoutubeDLArchiver(storage_client, driver, os.getenv('FACEBOOK_COOKIE')),
                    archivers.TwitterArchiver(storage_client, driver),
                    archivers.WaybackArchiver(storage_client, driver)
                ]
                for archiver in active_archivers:
                    logger.debug(f'Trying {archiver} on row {row}')

                    try:
                        if usefilenumber:
                            # using filenumber to store in folders so not checking for existence of that url
                            result = archiver.download(url, check_if_exists=False, filenumber=filenumber)
                        else:
                            result = archiver.download(url, check_if_exists=True)

                    except Exception as e:
                        result = False
                        logger.error(f'Got unexpected error in row {row} with archiver {archiver} for url {url}: {e}\n{traceback.format_exc()}')

                    if result:
                        # IA is a Success I believe - or do we want to display a logger warning for it?

                        if result.status in ['success', 'already archived', 'Internet Archive fallback']:
                            result.status = archiver.name + \
                                ": " + str(result.status)
                            logger.success(
                                f'{archiver} succeeded on row {row}, url {url}')
                            break

                          # wayback has seen this url before so keep existing status
                        if "wayback: Internet Archive fallback" in result.status:
                            logger.success(
                                f'wayback has seen this url before so keep existing status on row {row}')
                            result.status = result.status.replace(' (duplicate)', '')
                            result.status = str(result.status) + " (duplicate)"
                            break

                        logger.warning(
                             f'{archiver} did not succeed on {row=}, final status: {result.status}')
                        result.status = archiver.name + \
                            ": " + str(result.status)
                # get rid of driver so can reload on next row
                driver.quit()
                if result:
                    update_sheet(gw, row, result)
                else:
                    gw.set_cell(row, 'status', 'failed: no archiver')
                    logger.success(f'Finshed worksheet {wks.title}')

@logger.catch
def main():
    # polling of /poll-input/guid.in files
    while True:
        fileFound = False

        inputPath = 'poll-input/'
        # make sure exists
        os.makedirs(os.path.dirname(inputPath), exist_ok=True)

        outputPath = 'poll-output/'
        os.makedirs(os.path.dirname(outputPath), exist_ok=True)

        for file in os.listdir(inputPath):
            if fnmatch.fnmatch(file, '*.json'):
                logger.info(f'Found {file=}')
                # get guid from filename
                guid = Path(file).stem
                inputFile = inputPath + file
                outputFile = outputPath + file
                fileFound = True
                break # out of for loop

        if fileFound == False:
           logger.info('No input files found, sleeping')
           time.sleep(2) #seconds
        else:
            logger.info(f'processing {inputFile=} file')
            mkdir_if_not_exists('tmp')

            s3_config = S3Config(
                bucket=os.getenv('DO_BUCKET'),
                region=os.getenv('DO_SPACES_REGION'),
                key=os.getenv('DO_SPACES_KEY'),
                secret=os.getenv('DO_SPACES_SECRET')
            )
            telegram_config = archivers.TelegramConfig(
                api_id=os.getenv('TELEGRAM_API_ID'),
                api_hash=os.getenv('TELEGRAM_API_HASH')
            )

            # archives will be in a folder 'doc_name/worksheet_name'
            # s3_config.folder = f'{sheet.replace(" ", "_")}/{wks.title.replace(" ", "_")}/'
            s3_config.folder = f'foo'
            s3_client = S3Storage(s3_config)

            # extract url from json
            with open(inputFile) as f:
                d = json.load(f)

            # url = "https://twitter.com/dave_mateer/status/1524341442738638848"
            url = d['url']
            logger.info(f'url from file {url=} ')

            url = expand_url(url)
            filenumber = guid
            logger.info(f'filenumber {guid=} ')

            options = webdriver.FirefoxOptions()
            options.headless = True
            options.set_preference('network.protocol-handler.external.tg', False)

            driver = webdriver.Firefox(options=options)
            driver.set_window_size(1400, 2000)
            # in seconds, telegram screenshots catch which don't come back
            driver.set_page_load_timeout(120)

            active_archivers = [
                    archivers.TelethonArchiver(s3_client, driver, telegram_config),
                    archivers.TelegramArchiver(s3_client, driver),
                    archivers.TiktokArchiver(s3_client, driver),
                    archivers.YoutubeDLArchiver(s3_client, driver, os.getenv('FACEBOOK_COOKIE')),
                    archivers.TwitterArchiver(s3_client, driver),
                    archivers.WaybackArchiver(s3_client, driver)
            ]

            for archiver in active_archivers:
                    logger.debug(f'Trying {archiver} on {url=}')

                    try:
                       result = archiver.download(url, check_if_exists=False, filenumber=filenumber)

                    except Exception as e:
                        result = False
                        logger.error(f'Got unexpected error with {archiver=} for {url=}: {e}\n{traceback.format_exc()}')

                    if result:
                        if result.status in ['success', 'Internet Archive fallback']:
                            result.status = archiver.name + ": " + str(result.status)
                            logger.success(f'{archiver} succeeded on url {url}')

                            data = {}
                            data['cdn_url'] = result.cdn_url
                            data['screenshot'] = result.screenshot
                            data['status'] = result.status
                            data['thumbnail'] = result.thumbnail

                            # write output file
                            with open(outputFile, 'w', encoding='utf-8') as f:
                                json.dump(data, f, ensure_ascii=False, indent=4)

                            break # out of for loop

                        logger.warning(f'{archiver} did not succeed on {url=}, final status: {result.status}')
                        result.status = archiver.name + ": " + str(result.status)

            # cleaning up
            driver.quit()
            os.remove(inputFile)
            shutil.rmtree('tmp')
            logger.info(f'done')


if __name__ == '__main__':
    main()
