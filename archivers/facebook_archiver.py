import os, re
# from bs4 import BeautifulSoup
from loguru import logger
from .base_archiver import Archiver, ArchiveResult
from storages import Storage
from warcio.archiveiterator import ArchiveIterator
# from PIL import Image
from io import BytesIO
from urllib.parse import urlparse
import os
import os.path
import shlex
import subprocess
import re
import time
from loguru import logger
import sys
import glob

class FacebookArchiver(Archiver):
    name = "facebook"

    def __init__(self, storage: Storage, driver, brightdata_proxy_secret, hash_algorithm):
        super().__init__(storage, driver, hash_algorithm)
        self.brightdata_proxy_secret = brightdata_proxy_secret

    def download(self, url, check_if_exists=False):
        # detect URLs that we definitely cannot handle
        if 'facebook.com' not in self.get_netloc(url):
            return False

        logger.info(f"{url=}")

        if url.startswith("https://www."):
            m_url = url
        elif url.startswith("https://web."):
            m_url = url.replace("https://web.", "https://www.")

        # url = 'https://www.facebook.com/photo/?fbid=1329142910787472&set=a.132433247125117'
        if m_url.startswith('https://www.facebook.com/photo/?fbid='):
            logger.info("strategy 0 - direct link to a photo so skip to download it")
            result = self.run_bt(m_url)
            if result == False:
                logger.error(f"fb - no warc file.. timeout? {m_url}")
                return ArchiveResult(status="fb - no warc file.. timeout?")
            return self.final_bit(m_url)

        logger.info(f"Stage 1 curl mobile version of public page using http1.0 to get a hrefs numbers")

        # Silent mode (--silent) activated otherwise we receive progress data inside err message later
        # triple quotes are multi line string literal
        cURL = f"""curl --silent --http1.0 '{m_url}' """
        logger.info(cURL)

        lCmd = shlex.split(cURL) # Splits cURL into an array
        p = subprocess.Popen(lCmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = p.communicate() # Get the output and the err message

        if out == b'':
            retry_counter = 0
            logger.error("No response from curl (probably 302). Could do request again to get status code")
            while (retry_counter < 3):
                logger.info(f"Trying proxy attempt {retry_counter}")

                # Datacentre proxy on https://brightdata.com/cp/dashboard
                cURL = f"""curl --proxy zproxy.lum-superproxy.io:22225 --proxy-user {self.brightdata_proxy_secret} --silent --http1.0 "{m_url}" """
                logger.info(cURL)

                lCmd = shlex.split(cURL) # Splits cURL into an array
                p = subprocess.Popen(lCmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                out, err = p.communicate() # Get the output and the err message

                if out == b'':
                    logger.info(f"Proxy didn't work - pausing for 3 secs then trying again.")
                    time.sleep(3)
                    retry_counter += 1
                else:
                    break # out of while loop
            if retry_counter == 3:
                logger.error('Proxy didnt work after 3 retries')
                return ArchiveResult(status="proxy failed to get page after 3 retries")
            else:
                logger.info(f'curl with proxy worked on attempt {retry_counter}')
        else:
            logger.info('curl with no proxy worked')

        response = out.decode("utf-8")
        with open('response-debug.txt', 'w') as file:
            file.write(response)

        logger.info("Trying strategy 1 - /photos/pcb.")
        search = "/photos/pcb."
        count1 = response.count(search)
        logger.info(f"Count of all images found on strategy1: {count1}")

        if count1 == 0:
            logger.info("Trying strategy 2 - /photo.php")
            # https://www.facebook.com/photo.php?fbid=10159120790245976&amp;set=pcb.10159120790695976
            search = "/photo.php"
            count2 = response.count(search)
            logger.info(f"Count of all images found on strategy2: {count2}")

            if count2 == 0:
                logger.info("Trying strategy 3 - /photos/a.")
                # https://www.facebook.com/khitthitnews/photos/a.386800725090613/1425302087907133/

                search = "/photos/a."
                count3 = response.count(search)
                logger.info(f"Count of all images found on strategy3: {count3}")

                if count3 == 0:
                    logger.error("Problem. No results from any strategy")
                    return ArchiveResult(status="fb - no results from amy strategy")

        stage1counter = 0

        o = urlparse(m_url)
        # /shannewsburmese/posts/pfbid02ovzrfRaA6JPPL73QiA3uzvBSbY8yodiWnWbXNxoXB2GZe2T3yEMzyphNPmFDZgSNl
        path = o.path
        for match in re.finditer(search, response):
            if count1 != 0:
                # strategy1
                # position of / after user_name
                start_pos = match.start()
                # position of . after pcb
                end_pos = match.end()

                # post: https://www.facebook.com/shannewsburmese/photos/pcb.5639524319473298/5639523349473395/
                # permalink: https://www.facebook.com/102940249227144/photos/pcb.109374295250406/109374168583752/?type=3&theater
                fb_id_end_pos=response.find("/", end_pos+1)
                # eg '109374295250406'
                fb_id = response[end_pos:fb_id_end_pos]

                photo_id_end_pos=response.find("/", fb_id_end_pos+1)
                # eg '109374168583752'
                photo_id = response[fb_id_end_pos+1:photo_id_end_pos]

                # eg shannewsburmese (posts) or 102940249227144 (permalink)
                path_second_slash_pos= path.find("/", 1)
                user_name = path[1:path_second_slash_pos]

                url_build = f"https://www.facebook.com/{user_name}/photos/pcb.{fb_id}/{photo_id}"
            elif count2 != 0:
                # strategy2
                # https://www.facebook.com/photo.php?fbid=10159120790245976&amp;set=pcb.10159120790695976&amp;type=3
                # /photo.php\?fbid="
                # start_pos = match.start()
                # position of ? after php
                end_pos = match.end()

                fb_id_end_pos=response.find("&", end_pos+1)
                # fb_id = response[end_pos:fb_id_end_pos]

                photo_id_end_pos=response.find("&", fb_id_end_pos+1)
                foo = response[match.start():photo_id_end_pos]
                # photo_id = response[fb_id_end_pos+1:photo_id_end_pos]

                url_build = f"https://www.facebook.com{foo}"
            elif count3 != 0:
                # strategy3 - /photos/a.
                # https://www.facebook.com/khitthitnews/photos/a.386800725090613/1425302087907133/
                # position of / after user_name
                start_pos = match.start()
                # position of . after a
                end_pos = match.end()

                # https://www.facebook.com/shannewsburmese/photos/a.5639524319473298/5639523349473395/
                fb_id_end_pos=response.find("/", end_pos+1)
                fb_id = response[end_pos:fb_id_end_pos]

                # shannewsburmese
                path_second_slash_pos= path.find("/", 1)
                user_name = path[1:path_second_slash_pos]

                photo_id_end_pos=response.find("/", fb_id_end_pos+1)
                photo_id = response[fb_id_end_pos+1:photo_id_end_pos]

                url_build = f"https://www.facebook.com/{user_name}/photos/a.{fb_id}/{photo_id}"

            result = self.run_bt(url_build)

            # print("end of processing the warc file for a single request to FB. Check other images as may only get 3 at a time. /photos/pcb. match loop - going on to next image")
            stage1counter += 1

        return self.final_bit(m_url)
      
   
    def run_bt(self, url_build):
        with open('url.txt', 'w') as file:
            file.write(url_build)

        # call browsertrix and get a warc file using a logged into facebook profile
        # this will get full resolution image which we can then save as a jpg
        logger.info(f"Stage 2 - calling Browsertrix in Docker {url_build}")


        collection_name = str(int(time.time()))

        # have seen the warc not being created at timeout 5 secs
        command = f"docker run -v {os.getcwd()}/crawls:/crawls/ -v {os.getcwd()}/url.txt:/app/url.txt --rm -it webrecorder/browsertrix-crawler crawl --urlFile /app/url.txt --scopeType page --combineWarc --timeout 10 --profile /crawls/profiles/facebook-logged-in.tar.gz --collection {collection_name}"
        logger.info(command)

        lCmd = shlex.split(command) # Splits command into an array
        p = subprocess.Popen(lCmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = p.communicate() # Get the output and the err message
        # foo = out.decode("utf-8")
        # catch docker err here?

        warc_file_name = f"{os.getcwd()}/crawls/collections/{collection_name}/{collection_name}_0.warc.gz"
        does_file_exist = os.path.exists(warc_file_name)
        if does_file_exist == False:
            logger.exception(f"Warc file doesn't exist {warc_file_name}")
            return False

        logger.info(f'Parsing warc file from browsertrix {warc_file_name=}')
    
        with open(warc_file_name, 'rb') as stream:
            for record in ArchiveIterator(stream):
                if record.rec_type == 'response':
                    # eg http://brokenlinkcheckerchecker.com/img/flower2.jpg
                    uri = record.rec_headers.get_header('WARC-Target-URI')

                    ct = record.http_headers.get_header('Content-Type')

                    if ct == 'image/jpeg':
                        status = record.http_headers.statusline
                        if status=='200 OK':
                            o = urlparse(uri)
                            # eg 314610756_1646726005764739_1320718433281872139_n.jpg from the uri
                            filename = os.path.basename(o.path)

                            content = record.content_stream().read()

                            # load content into in memory bytes buffer
                            # from io import BytesIO
                            img_bytes_io = BytesIO()
                            img_bytes_io.write(content)

                            # write out to aa TMP_FOLDER
                            # filename_save_and_path = os.getcwd() + "/out/" + filename
                            filename_save_and_path = Storage.TMP_FOLDER + "/" + filename

                            if os.path.isfile(filename_save_and_path):
                                logger.info(f"already saved {filename} so ignoring")
                            else:
                                logger.info(f"found new image and saving {filename}")
                            # write original bytes to file in binary mode
                                with open(filename_save_and_path, "wb") as f:
                                    f.write(img_bytes_io.getbuffer())
                                filesize = os.path.getsize(filename_save_and_path)
                                logger.info(f'Filesize is {round(filesize/1000, 1)}kb')
                                if filesize < 2000:
                                    logger.info(f'Filesize is less the 2k, so deleting image')
                                    os.remove(filename_save_and_path)
        return True

    def final_bit(self, url):
        # we don't call generate_media_page as we have downloaded urls above
        # treat like vk_archiver
        thumbnail, thumbnail_index = None, None
        uploaded_media = []
        # filenames = self.vks.download_media(results, Storage.TMP_FOLDER)
        filenames = glob.glob(Storage.TMP_FOLDER + "/*.jpg")

        # uploads when I iterate
        for filename in filenames:
            key = self.get_key(filename)
            self.storage.upload(filename, key)
            hash = self.get_hash(filename)
            cdn_url = self.storage.get_cdn_url(key)
            # try:
            #     _type = mimetypes.guess_type(filename)[-1].split("/")[0]
            #     if _type == "image" and thumbnail is None:
            #         thumbnail = cdn_url
            #     if _type == "video" and (thumbnail is None or thumbnail_index is None):
            #         thumbnail, thumbnail_index = self.get_thumbnails(filename, key)
            # except Exception as e:
            #     logger.warning(f"failed to get thumb for {filename=} with {e=}")
            uploaded_media.append({'cdn_url': cdn_url, 'key': key, 'hash': hash})

        textual_output = ""
        page_cdn, page_hash, thumbnail = self.generate_media_page_html(url, uploaded_media, textual_output, thumbnail=thumbnail)

        # this screenshotter often gets a facebook login page.. todo put in urlbox with proxy
        screenshot = self.get_screenshot(url)

        title = ""
        datetime = ""

        # remove files from temp 
        files = glob.glob(Storage.TMP_FOLDER + '/*')
        for f in files:
            os.remove(f)
        return ArchiveResult(status="success", cdn_url=page_cdn, screenshot=screenshot, hash=page_hash, thumbnail=thumbnail, thumbnail_index=thumbnail_index, timestamp=datetime, title=title)



    

