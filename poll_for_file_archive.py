import os
import datetime
import requests
import shutil
import gspread
from loguru import logger
from dotenv import load_dotenv
from selenium import webdriver
import traceback

import archivers
from storages import S3Storage, S3Config
from utils import mkdir_if_not_exists

import fnmatch
import os
from loguru import logger
import time
from pathlib import Path
import json

# pyton file should be run from systemctl
# which will restart on reboot and crash (just like Prepared Hatespeech)

# api-auto-archiver project sits in front of this acting as a control which the
# osr4rightstools website calls from home page

# Looks for any guid.json files in input directory 
# which contains json of: "url":"https://twitter.com/dave_mateer/status/1524341442738638848"
# it then does the archiving
# saving to guid.json in output directory


logger.add("logs/1trace.log", level="TRACE")
logger.add("logs/2info.log", level="INFO")
logger.add("logs/3success.log", level="SUCCESS")
logger.add("logs/4warning.log", level="WARNING")
logger.add("logs/5error.log", level="ERROR")


def expand_url(url):
    # expand short URL links
    if 'https://t.co/' in url:
        try:
            r = requests.get(url)
            url = r.url
        except:
            logger.error(f'Failed to expand url {url}')
    return url


@logger.catch
def main():
    load_dotenv()
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

            # name of s3 folder
            s3_config.folder = f'web'
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
