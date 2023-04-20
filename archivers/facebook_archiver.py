import os, re
from loguru import logger
from .base_archiver import Archiver, ArchiveResult
from storages import Storage
from warcio.archiveiterator import ArchiveIterator
from io import BytesIO
from urllib.parse import urlparse
import os
import os.path
import shlex
import subprocess
import re
import time
from loguru import logger
import glob
import shutil
import time

class FacebookArchiver(Archiver):
    """
    This alpha Facebook Archiver uses different strategies depending on the target page within Facebook

      Part 1
        curl the mobile version of the page to get a fb_id and photo_id
         **update 18th Apr 23, mobile version now giving 302 to login
         **revert back to www version 
      Part 2
        use browsertrix which saves webpages as a warc file
        the binary images are all saved too in the warc file
        we parse them out of the file and upload to our storage
      screenshot
        uses playwright_screenshot.py

      needs
         brightdata_proxy_secret in facebook config
         docker installed
    """

    name = "facebook"

    def __init__(self, storage: Storage, driver, brightdata_proxy_secret, hash_algorithm):
        super().__init__(storage, driver, hash_algorithm)
        self.brightdata_proxy_secret = brightdata_proxy_secret

    def download(self, url, check_if_exists=False):
        logger.info(f"inbound {url=}")

        netloc = self.get_netloc(url) # eg www.facebook.com

        if 'facebook.com' not in netloc:
            return False

        if 'facebook.com/watch/?v=' in url:
            message = "Video watch so use youtubedlp?"
            logger.warning(message)
            return ArchiveResult(status=message)

        if '/videos/' in url:
            message = "videos in url so ignoring"
            logger.warning(message)
            return ArchiveResult(status=message)

        if 'facebook.com/groups/' in url:
            message = "Group should link to an actual post as this will change"
            logger.info(message)
            return ArchiveResult(status=message)

        if 'facebook.com/photo/?fbid=' in url:
           logger.info("strategy 0 - direct link to a single photo so just download it")
           self.save_jpegs_to_temp_folder_from_url_using_browsertrix(url)
           return self.upload_all_jpegs_from_temp_folder_and_generate_screenshot_and_html(url)

        if 'facebook.com/photo?fbid=' in url:
           logger.info("strategy 0 - direct link to a single photo so just download it")
           self.save_jpegs_to_temp_folder_from_url_using_browsertrix(url)
           return self.upload_all_jpegs_from_temp_folder_and_generate_screenshot_and_html(url)

        if '/photos/a.' in url:
           logger.info("strategy 0b - direct link to what is probably meant to be a single photo so just download it")
           self.save_jpegs_to_temp_folder_from_url_using_browsertrix(url)
           return self.upload_all_jpegs_from_temp_folder_and_generate_screenshot_and_html(url)

        if '/photos/pcb.' in url:
           logger.info("strategy 0c - direct link to what is probably meant to be a single photo so just download it")
           self.save_jpegs_to_temp_folder_from_url_using_browsertrix(url)
           return self.upload_all_jpegs_from_temp_folder_and_generate_screenshot_and_html(url)

        # mobile version of the url for strategy 1 curl
        if url.startswith("https://www."):
            logger.debug('www normal code path')
            # m_url = url.replace("https://www.", "https://mobile.")
        # web. which we want to convert to www
        elif url.startswith("https://web."):
            url = url.replace("https://web.", "https://www.")
            # m_url = url.replace("https://www.", "https://mobile.")
        else:
            logger.warning(f'unusual starting url {url}')
            # m_url = url

        # logger.info(f"{m_url=}")

        def chop_below_recent_post_by_page(response):
            logger.info(f'Response length is {len(response)}')

            splits = response.split('Recent post by Page')
            top_bit = splits[0]
            logger.info(f'top_bit length = {len(top_bit)}')
            
            if len(splits) > 1:
                bottom_bit = splits[1]
                logger.info(f'bottom_bit length = {len(bottom_bit)}')
            else:
                logger.info('Split failed ie - Recent post by Page - not there')

            return top_bit

        def get_html_from_curl(url, force_proxy=False):
            # logger.info(f"curl on url: {m_url}")
            logger.info(f"curl on www url: {url}")

            # 19th Apr - turning off local curl in favour of proxy as more reliable
            # nope still need local curl for AA009 https://www.facebook.com/permalink.php?story_fbid=pfbid0BqNZHQaQfqTAKzVaaeeYNuyPXFJhkPmzwWT7mZPZJLFnHNEvsdbnLJRPkHJDMcqFl&id=100082135548177
            # but then fails for FM002 https://web.facebook.com/shannewsburmese/posts/pfbid02ovzrfRaA6JPPL73QiA3uzvBSbY8yodiWnWbXNxoXB2GZe2T3yEMzyphNPmFDZgSNl

            # was getting spurious results ie now correct html from www rather than mobile

            # Silent mode (--silent) activated otherwise we receive progress data inside err message later
            # triple quotes are multi line string literal
            # cURL = f"""curl --silent --http1.0 '{m_url}' """
            cURL = f"""curl --silent --http1.0 '{url}' """
            logger.info(cURL)

            lCmd = shlex.split(cURL) # Splits cURL into an array
            p = subprocess.Popen(lCmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            out, err = p.communicate() # Get the output and the err message

            should_do_proxy_run = False

            if out == b'':
                should_do_proxy_run = True
            if force_proxy == True:
                should_do_proxy_run = True

            # if 1 ==1:
            if should_do_proxy_run:
                retry_counter = 0
                # logger.debug("No response from curl (probably 302). Could do request again to get status code")

                # rather than retyring we could write to the spreadsheet then come back on the next run
                # this does seem to work manually
                max_num_of_retries_to_do = 5
                pause = 5 
                while (retry_counter < max_num_of_retries_to_do):
                    logger.debug(f"Trying proxy attempt {retry_counter}")

                    # Datacentre proxy on https://brightdata.com/cp/dashboard
                    # cURL = f"""curl --proxy zproxy.lum-superproxy.io:22225 --proxy-user {self.brightdata_proxy_secret} --silent --http1.0 "{m_url}" """
                    cURL = f"""curl --proxy zproxy.lum-superproxy.io:22225 --proxy-user {self.brightdata_proxy_secret} --silent --http1.0 "{url}" """

                    logger.info(cURL)

                    lCmd = shlex.split(cURL) # Splits cURL into an array
                    p = subprocess.Popen(lCmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    out, err = p.communicate() # Get the output and the err message

                    if out == b'':
                        logger.info(f"Proxy didn't work - pausing for {pause} secs then trying again.")
                        time.sleep(pause)
                        retry_counter += 1
                    else:
                        break # out of while loop
                if retry_counter == max_num_of_retries_to_do:
                    # logger.error(f'Proxy didnt work after {max_num_of_retries_to_do} retries on {m_url}')
                    logger.error(f'Proxy didnt work after {max_num_of_retries_to_do} retries on {url}')

                    # logger.error(f'Try checking for a 301 Perm Redirect eg:   curl -s -D - -o /dev/null {m_url}')
                    logger.error(f'Try checking for a 301 Perm Redirect or 302 to login eg:   curl -s -D - -o /dev/null {url}')

                    return 'proxy failed'
                else:
                    logger.debug(f'curl with proxy worked on attempt {retry_counter}')
            else:
                logger.debug('curl with no proxy worked')

            response = out.decode("utf-8")
            with open('response-get-html-from-curl.html', 'w') as file:
                file.write(response)
            return response
     
        def do_browsertrix_call_to_www(url):
            logger.info(f'Calling browsertrix with {url=} using a logged in FB profile')
            with open('url.txt', 'w') as file:
                file.write(url)
            collection_name = str(int(time.time()))
            command = f"docker run -v {os.getcwd()}/crawls:/crawls/ -v {os.getcwd()}/url.txt:/app/url.txt --rm webrecorder/browsertrix-crawler crawl --urlFile /app/url.txt --scopeType page --combineWarc --timeout 10 --profile /crawls/profiles/facebook-logged-in.tar.gz --collection {collection_name}"

            logger.info(command)

            lCmd = shlex.split(command) # Splits command into an array
            p = subprocess.Popen(lCmd, user="dave", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            out, err = p.communicate() # Get the output and the err message

            warc_file_name = f"{os.getcwd()}/crawls/collections/{collection_name}/{collection_name}_0.warc.gz"
            logger.info(f'{warc_file_name=}')

            count_of_bulk_route_definitions = 0
            fb_id = None
            count2_style = None
            with open(warc_file_name, 'rb') as stream:
                for record in ArchiveIterator(stream):
                    if record.rec_type == 'request':
                        uri = record.rec_headers.get_header('WARC-Target-URI')
                        if "bulk-route-definitions/" in uri:
                            content = record.content_stream().read()
                            foo = str(content)

                            # dump out to a file to make easier to see?

                            # strategy 1 and 3

                            # FB005 initial page - strategy 1
                            # =%2Fdeltanewsagency%2Fphotos%2Fpcb.1560914197619606%2F1560914044286288%2F%3F__cft__[0]%3DAZU2Wt2Q4WTrfBwGPj8Bkd-MU9pcBVHgyfuuDWFLOhCGI9c1MJisnN8_WQ2fgdR4FeGdgeHN82e9UNOEFH-iHks-FL0DWWIiUeuDcmB4VRCG188is4IphI1STT8o5Dv8lciwkgQoNs0C9ysYUZygddSj%26__tn__%3D*bH-R
                            dot_string_start_pos = foo.find(f'%2Fphotos%2Fpcb.',0)

                            if (dot_string_start_pos > 0):
                                # fb_id_end_pos = foo.find(f'%2F', dot_string_start_pos)
                                # **HERE** does this work on FB005?
                                dot_start_pos = dot_string_start_pos + 16

                                # a %2F
                                middle_2f_start_pos = foo.find(f'%2F', dot_start_pos)
    
                                fb_id = foo[dot_start_pos:middle_2f_start_pos]
                            
                                # the next %2F%3F
                                route_url_end_pos = foo.find(f'%2F%3F', dot_start_pos)

                                # next photo_id
                                next_photo_id = foo[middle_2f_start_pos+3:route_url_end_pos]

                                logger.debug(f"Strategy 1 and 3 {fb_id=} and {next_photo_id=}")
                                count_of_bulk_route_definitions += 1
                            else:
                                # strategy 2
                                # similar to count2 down below
                                # photo urls like: https://www.facebook.com/photo/?fbid=158716316690227&set=pcb.158716626690196

                                # route_urls[0]=%2Fphoto%2F%3Ffbid%3D10159120790245976%26set%3Da.10151172526425976%26__tn__%3D%252CO*F&route_urls[1]=%2Flogin%2F%3F__tn__%3D*F&

    # route_urls[0]=%2Fphoto%2F%3Ffbid%3D198298719404512%26set%3Da.165296439371407%26__cft__[0]%3DAZUbz8tPakf3gHpEGOLK7sSoJu_b_5_tb4eBNdC-hBbM0XSnR6q9fONtJEyUetnELkEOm4FoyPN1fmq0TXGermISsz5kVresAhOAxf7OesXea0oRN5pF4EFs-14ekn7bpj4Ymy17H1aR3nIxKCfrZhNxPdsS1XZ2l07_mxiV3TK5G-gZfx4QAKhEmalg_bLhKLQ%26__tn__%3DEH-R&

                                # eg 3rd Jan 2023 https://www.facebook.com/101135589114967/posts/pfbid0Aw47PUc6Bm1GaciuwYWSwu5n7wvUBsFRqGkty3KAhpk61EtRq27gVyLXrgTUo3mEl/

                                logger.warning(f"warc code path not working yet")
                                # thing_start_pos = foo.find(f'%2Fphoto%2F%3Ffbid%3D',0)

                                # if (thing_start_pos > 0):
                                #     equals_start_pos = thing_start_pos+21
                                
                                #     # first number
                                #     photo_id_end_pos = foo.find(f'%26', equals_start_pos)

                                #     next_photo_id = foo[equals_start_pos:photo_id_end_pos]

                                #     logger.debug(f"Strategy 2 Next photo id {next_photo_id}")

                                #     # second number
                                #     set_id_start_pos = foo.find("set=pcb.", photo_id_end_pos)
                                #     set_id_start_posa = foo.find("set%3Dpcb.", photo_id_end_pos)
                                #     set_id_start_posb = foo.find("set%3Da.", photo_id_end_pos)
                                #     if set_id_start_pos > 0:
                                #         logger.error("not tested code path - maybe search for set%3Dpcb.")
                                #         # logger.debug("set=pcb.")
                                #         # count2_style = "pcb"
                                #         # set_id_end_pos = response.find("&amp", set_id_start_pos)
                                #         # set_id = response[set_id_start_pos+8:set_id_end_pos]
                                #     elif set_id_start_posa > 0:
                                #         logger.debug("set%3Dpcb.")
                                #         count2_style = "pcb"
                                #         set_id_end_pos = foo.find("&amp", set_id_start_pos)
                                #         set_id = foo[set_id_start_pos+8:set_id_end_pos]
                                #         logger.debug(f'{set_id=}')
                                #     elif set_id_start_posb > 0:
                                #         # logger.debug("set%3Da.")
                                #         count2_style = "a"
                                #         set_id_start_pos = foo.find("set%3Da.", photo_id_end_pos)
                                #         set_id_end_pos = foo.find("&route_urls[2]", set_id_start_pos)
                                #         set_id = foo[set_id_start_pos+8:set_id_end_pos]
                                #         if len(set_id) > 16:
                                #             # try the next way
                                #             set_id_end_pos = foo.find("%26", set_id_start_pos)
                                #             set_id = foo[set_id_start_pos+8:set_id_end_pos]
                                #         logger.debug(f'{set_id=}')
                                #     else:
                                #         logger.info("problem - can't find set_id")

                                #     count_of_bulk_route_definitions += 1

            # https://www.facebook.com/385165108587508/posts/1437227363381272/?d=n  this worked by chance (last of 2 was the correct one)
            if count_of_bulk_route_definitions > 1:
                logger.warning(f'{count_of_bulk_route_definitions=} dont know which one to take - so do both? currently taking last one')
            # https://www.facebook.com/deltanewsagency/photos/pcb.1560914197619606/1560914044286288/

            # if count2_style is not None: # is something
            #     return count2_style, next_photo_id, set_id

            if fb_id is None:
                logger.warning(f'couldnt find an image in warc file - try recreating profile again. {url=}')
                return None

            return fb_id, next_photo_id

        # Part 1
        # we need a photo_id as will be calling that page via browsertrix on the www side to get full size image
        def foo(force_proxy=False):
            # hack - globals to give scope. this function maybe calls itself further down
            # need to refactor
            global response
            response = get_html_from_curl(url, force_proxy)
            if response == 'proxy failed':
                return ArchiveResult(status="problem ** - failure on curl nothing worked. time pause????")

            response = chop_below_recent_post_by_page(response)

            logger.info("Trying strategy 1 - /photos/pcb.")
            global search
            search = "/photos/pcb."
            global count1, count2, count3, warc_result
            count1 = response.count(search)
            count2 = 0
            count3 = 0
            warc_result = None
            logger.info(f"Count of all images found on strategy1: {count1}")

            if count1 == 0:
                # logger.info("Trying strategy 2 - /photo.php")
                # # https://www.facebook.com/photo.php?fbid=10159120790245976&amp;set=pcb.10159120790695976
                # # not searching for ?fbid= section as it is a regex
                # search = "/photo.php"
                # count2 = response.count(search)
                # logger.info(f"Count of all images found on strategy2: {count2}")

                logger.info("Trying strategy 3 - /photos/a.")
                # https://www.facebook.com/khitthitnews/photos/a.386800725090613/1425302087907133/
                search = "/photos/a."
                count3 = response.count(search)
                logger.info(f"Count of all images found on strategy3: {count3}")

                if count3 == 0:
                    # logger.info("Trying strategy 3 - /photos/a.")
                    # # https://www.facebook.com/khitthitnews/photos/a.386800725090613/1425302087907133/
                    # search = "/photos/a."
                    # count3 = response.count(search)
                    # logger.info(f"Count of all images found on strategy3: {count3}")


                    logger.info("Trying strategy 2 - /photo.php")
                    # https://www.facebook.com/photo.php?fbid=10159120790245976&amp;set=pcb.10159120790695976
                    # not searching for ?fbid= section as it is a regex
                    search = "/photo.php"
                    count2 = response.count(search)
                    logger.info(f"Count of all images found on strategy2: {count2}")

                    if count2 == 0:
                        logger.info(f'No results from curl - trying warc - could be a sensitive photo which requires a login to FB')

                        warc_result = do_browsertrix_call_to_www(url)

                        if warc_result is None:
                            message = "Potential problem? No results from any curl strategy nor warc. This could be a page that is not available anymore. Could be an embedded video which youtubedlp should get"
                            logger.warning(message)

                            # we've already called force proxy below, so fail
                            if force_proxy == True:
                                message = "Potential problem? No results from any curl strategy nor warc. This could be a page that is not available anymore. Could be an embedded video which youtubedlp should get"
                                logger.error(message)

                                return ArchiveResult(status=message)
                            # try all this again but force the proxy
                            # edge case where local curl worked but gave back bad results
                            # eg FM002 https://www.facebook.com/shannewsburmese/posts/pfbid02ovzrfRaA6JPPL73QiA3uzvBSbY8yodiWnWbXNxoXB2GZe2T3yEMzyphNPmFDZgSNl?_rdc=1&_rdr
                            message = "Edge case - local curl gave 200 but no images, so trying proxy"
                            logger.success(message)
                            foo(force_proxy = True)

        foo()

        # o = urlparse(m_url)
        o = urlparse(url)
        # /shannewsburmese/posts/pfbid02ovzrfRaA6JPPL73QiA3uzvBSbY8yodiWnWbXNxoXB2GZe2T3yEMzyphNPmFDZgSNl
        path = o.path
        path_second_slash_pos= path.find("/", 1)
        user_name = path[1:path_second_slash_pos]
        logger.info(f'{user_name=}')

        if warc_result is not None:
            # list_length = len(warc_result)
            # if list_length == 3:
            #     logger.debug("count2 strategy")
            # else:
            logger.info(f'warc has a result so using that to get fb_id and photo_id')
            fb_id = warc_result[0]
            photo_id = warc_result[1]
            logger.info(f'{fb_id=} {photo_id=}')
        else:
            # Only want the first image found
            for match in re.finditer(search, response):
                if count1 > 0:
                    # strategy1 - /photos/pcb.
                    # post: https://www.facebook.com/shannewsburmese/photos/pcb.5639524319473298/5639523349473395/

                    end_pos_of_dot_after_pcb = match.end()

                    # 1. the first number
                    fb_id_end_pos=response.find("/", end_pos_of_dot_after_pcb+1)
                    fb_id = response[end_pos_of_dot_after_pcb:fb_id_end_pos]

                    # 2. the second number
                    photo_id_end_pos=response.find("/", fb_id_end_pos+1)
                    photo_id = response[fb_id_end_pos+1:photo_id_end_pos]

                    break # out of for loop as only want the first image

                if count2 > 0:
                    # strategy2 - photo.php
                    # ?fbid= is actually the photo_id 
                    # https://www.facebook.com/photo.php?fbid=10159120790245976&amp;set=pcb.10159120790695976&amp;type=3

                    #                         /photo.php?fbid=10167143299580331&amp;id=811340330&amp;set=a.10150539384490331&amp;__tn__=EH-

                    # https://www.facebook.com/photo?fbid=261548379516999&set=pcb.111058327899339

                    end_pos_of_equals = match.end() + 6

                    # first number
                    photo_id_end_pos=response.find("&", end_pos_of_equals+1)
                    photo_id = response[end_pos_of_equals:photo_id_end_pos]

                    # second number - set=pcb. or set=a. etc.. 
                    set_id_start_pos = response.find("set=pcb.", photo_id_end_pos)
                    set_id_start_posb = response.find("set=a.", photo_id_end_pos)
                    # set_id_start_posc = response.find("set=p.", photo_id_end_pos)
                    if set_id_start_pos > 0:
                        logger.debug("set=pcb.")

                        # the first number
                        # DM 19th Apr
                        fb_id_end_pos=response.find("&set=", end_pos_of_equals+1)
                        fb_id = response[end_pos_of_equals:fb_id_end_pos]

                        count2_style = "pcb"
                        set_id_end_pos = response.find("&amp", set_id_start_pos)
                        set_id = response[set_id_start_pos+8:set_id_end_pos]

                    elif set_id_start_posb > 0:
                        logger.debug("set=a.")
                        count2_style = "a"
                        set_id_start_pos = response.find("set=a.", photo_id_end_pos)
                        set_id_end_pos = response.find("&amp", set_id_start_pos)
                        set_id = response[set_id_start_pos+6:set_id_end_pos]

                    break # out of loop as only want the first image

                    # elif set_id_start_posc > 0:
                    #     logger.debug("set=p.")
                    #     count2_style = "p"
                    #     set_id_start_pos = response.find("set=p.", photo_id_end_pos)
                    #     set_id_end_pos = response.find("&amp", set_id_start_pos)
                    #     set_id = response[set_id_start_pos+6:set_id_end_pos]


                if count3 > 0:
                    # strategy3 - /photos/a.
                    # /khitthitnews/photos/a.386800725090613/1425302087907133/?type=3&amp;__tn__=EH-R"><img src="https://scontent.ffab1-2.fna.fbcdn.net/v/t39.30808-6/273884377_1425302081240467_2868312596092196193_n.jpg?stp=cp0_dst-jpg_e15_q65_s

                    # position of . after a
                    end_pos_of_dot_after_a = match.end()

                    # the first number
                    fb_id_end_pos=response.find("/", end_pos_of_dot_after_a+1)
                    fb_id = response[end_pos_of_dot_after_a:fb_id_end_pos]

                    # the second number
                    photo_id_end_pos=response.find("/", fb_id_end_pos+1)
                    photo_id = response[fb_id_end_pos+1:photo_id_end_pos]

                    break # out of for loop as only want the first image

        ## Part 2
        # logger.info(f"Part2 - we now have a starting url for warc - {fb_id=} and {photo_id=}")
        photo_ids_requested = []
        photo_ids_to_request = []
        
        while (True):
            if count2 > 0:
                logger.info("strategy 2 which is a different url for the photos")
                builder_url = f"https://www.facebook.com/photo?fbid={photo_id}&set=pcb.{set_id}"
                # special case eg DMFIRE030 
                # https://www.facebook.com/permalink.php?story_fbid=261548436183660&id=100069855172938
                # I can't tell when to stop
                if len(photo_ids_requested) > 20:
                    logger.warning(f'More than 20 in {photo_ids_requested=} and {photo_ids_to_request=}')
                    break
            else:
                # builder_url = f"https://www.facebook.com/{user_name}/photos/pcb.{fb_id}/{photo_id}"
                builder_url = f"https://www.facebook.com/photo?fbid={photo_id}&set=pcb.{fb_id}"

            photo_ids_requested.append(photo_id)
            logger.debug(f"trying url {builder_url}")
            next_photo_ids = self.save_jpegs_to_temp_folder_from_url_using_browsertrix(builder_url)


            if next_photo_ids == -1:
                message = f"Warc file should have contained images and didn't. Possible fb block? {builder_url=}"
                logger.error(message)
                return ArchiveResult(status=message)

            if next_photo_ids == 0:
                logger.debug("no next photo found. Normal control flow")
                break

            if count3 == 1:
                logger.info("special case single image - don't get next photo_id")
                break 


            for p in next_photo_ids:
                if p in photo_ids_requested:
                    logger.info(f'already requested')
                else:
                    if p in photo_ids_to_request:
                        logger.info(f'already in photo_ids_to_request')
                    else:
                        photo_ids_to_request.append(p)

            # if next_photo_ids in photo_ids_requested:
            #     logger.info(f'Breaking out as next_photo_id is in photo_ids_requested: {next_photo_id=}')
            #     break # out of while
            if not photo_ids_to_request:
                logger.info(f'Nothing in photo_ids_to_request so breaking out')
                break

            if len(photo_ids_requested) > 50:
                logger.warning(f'More than 50 in {photo_ids_requested=} and {photo_ids_to_request=}')
                break
 
            next_photo_id = photo_ids_to_request[0]
            photo_ids_to_request.remove(next_photo_id)

            if len(next_photo_id) > 20:
                logger.error(f"Photo ID not correct! Edge case possibly like FB006 where string is not ax expected. continuing on to get as much as possible. probably only 3")

            logger.info(f'{next_photo_id=}')
            photo_id = next_photo_id

        foo = self.upload_all_jpegs_from_temp_folder_and_generate_screenshot_and_html(url)
        return foo
      
   
    def save_jpegs_to_temp_folder_from_url_using_browsertrix(self, url_build):
        with open('url.txt', 'w') as file:
            file.write(url_build)

        # call browsertrix and get a warc file using a logged in facebook profile
        # this will get full resolution image which we can then save as a jpg
        logger.info(f"Stage 2 - calling Browsertrix in Docker {url_build}")

        collection_name = str(int(time.time()))

        # have seen the warc not being created at timeout 5 secs
        # docker needs to be setup to run as non root (eg dave)
        # see server-build.sh
        # --it for local debugging (interactive terminal)
        command = f"docker run -v {os.getcwd()}/crawls:/crawls/ -v {os.getcwd()}/url.txt:/app/url.txt --rm webrecorder/browsertrix-crawler crawl --urlFile /app/url.txt --scopeType page --combineWarc --timeout 20 --profile /crawls/profiles/facebook-logged-in.tar.gz --collection {collection_name}"
        logger.info(command)

        lCmd = shlex.split(command) # Splits command into an array
        p = subprocess.Popen(lCmd, user="dave", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = p.communicate() # Get the output and the err message

        # foo = out.decode("utf-8")
        # logger.info(foo)
        # catch docker err here?
            
        # for prod - docker runs as non-root (dave) but write files as root. So change perms.
        # we're running nopasswd for sudo 
        if os.getcwd() == "/mnt/c/dev/test/auto-archiver":
            logger.debug('Dev env found so not updating permissions of /crawls/collections')
        else:
            logger.info("Updating permissions for crawls/collections")
            command = f"sudo chmod -R 777 {os.getcwd()}/crawls/collections/"
            lCmd = shlex.split(command) # Splits command into an array
            p = subprocess.Popen(lCmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            out, err = p.communicate() # Get the output and the err message
            # logger.info(f'output of chmod is {out}')
            # logger.error(f'error of chmod is {err}')

        warc_file_name = f"{os.getcwd()}/crawls/collections/{collection_name}/{collection_name}_0.warc.gz"
        does_file_exist = os.path.exists(warc_file_name)
        if does_file_exist == False:
            logger.exception(f"Warc file doesn't exist {warc_file_name} for {url_build=}")
            return -1

        logger.info(f'Parsing warc file from browsertrix {warc_file_name=} for {url_build=}')

        next_photo_id = 0
        next_photo_ids = []
        try:
            logger.info(f'Parsing warc for route_urls that will tell us the next photo_id')
            with open(warc_file_name, 'rb') as stream:
               for record in ArchiveIterator(stream):
                   if record.rec_type == 'request':
                     uri = record.rec_headers.get_header('WARC-Target-URI')
                     if "bulk-route-definitions/" in uri:
                        content = record.content_stream().read()
                        foo = str(content)

                        # strategy 1 and 3
                        # route_urls[0]=%2Fdeltanewsagency%2Fphotos%2Fpcb.1560914197619606%2F1560914174286275&route_urls[1]=%2Fdeltanewsagency%2Fphotos%2Fpcb.1560914197619606%2F1560914074286285&

                        # FB changed and is now putting the current page first
                        # 19th Apr 2023
                        # route_urls[0]=%2Fshannewsburmese%2Fphotos%2Fpcb.5639524319473298%2F5639523349473395&route_urls[1]=%2Fshannewsburmese%2Fphotos%2Fpcb.5639524319473298%2F5639523779473352&route_urls[2]

                        # route_urls[0]=%2Fdeltanewsagency%2Fphotos%2Fpcb.1560914197619606%2F1560914044286288&route_urls[1]=%2Fphoto%2F%3Ffbid%3D1560914174286275%26set%3Dpcb.1560914197619606&
                        dot_start_posX = foo.find(f'%2Fphotos%2Fpcb.',0)

                        if (dot_start_posX > 0):
                            # 19th Apr 2023 update
                            # find the next %2Fphotos%2Fpcb. after the first one!
                            dot_start_pos = foo.find(f'%2Fphotos%2Fpcb.',dot_start_posX+16)
                            # dot_start_pos = dot_start_posX 

                            # the middle %2F
                            middle_2f_start_pos = foo.find(f'%2F', dot_start_pos+16)
                            
                            # the next &
                            route_url_end_pos = foo.find(f'&', dot_start_pos)

                            # next photo_id
                            next_photo_id = foo[middle_2f_start_pos+3:route_url_end_pos]

                            logger.debug(f"warc parse Strategy 1 Next photo id {next_photo_id}")
                            
                            if next_photo_id not in next_photo_ids:
                                next_photo_ids.append(next_photo_id)
                        else:
                            # strategy 2
                            # route_urls[0]=%2Fphoto%2F%3Ffbid%3D10159120790245976%26set%3Da.10151172526425976%26__tn__%3D%252CO*F&route_urls[1]=%2Flogin%2F%3F__tn__%3D*F&

                            thing_start_pos = foo.find(f'%2Fphoto%2F%3Ffbid%3D',0)

                            if (thing_start_pos > 0):
                                equals_start_pos = thing_start_pos+21
                            
                                photo_id_end_pos = foo.find(f'%26', equals_start_pos)

                                next_photo_id = foo[equals_start_pos:photo_id_end_pos]

                                logger.debug(f"warc parse Strategy 2 Next photo id {next_photo_id}")
                                
                                if next_photo_id not in next_photo_ids:
                                    next_photo_ids.append(next_photo_id)

            logger.debug(f'Parsing warc for jpeg images on the page')
            with open(warc_file_name, 'rb') as stream:
                count_of_images_found = 0
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

                                filename_save_and_path = Storage.TMP_FOLDER + "/" + filename

                                count_of_images_found += 1
                                if os.path.isfile(filename_save_and_path):
                                    logger.debug(f"already saved {filename} so ignoring")
                                else:
                                    logger.debug(f"found new image and saving {filename}")
                                # write original bytes to file in binary mode
                                    with open(filename_save_and_path, "wb") as f:
                                        f.write(img_bytes_io.getbuffer())
                                    filesize = os.path.getsize(filename_save_and_path)
                                    logger.debug(f'Filesize is {round(filesize/1000, 1)}kb')
                                    if filesize < 2000:
                                        logger.debug(f'Filesize is less the 2k, so deleting image')
                                        count_of_images_found -= 1
                                        os.remove(filename_save_and_path)
                                    # else:
                                    #     logger.warning("trying special case of only saving the first image")
                                        # raise StopIteration
                        if ct == 'image/png':
                            status = record.http_headers.statusline
                            if status=='200 OK':
                                o = urlparse(uri)
                                # eg 314610756_1646726005764739_1320718433281872139_n.jpg from the uri
                                filename = os.path.basename(o.path)

                                content = record.content_stream().read()

                                img_bytes_io = BytesIO()
                                img_bytes_io.write(content)

                                filename_save_and_path = Storage.TMP_FOLDER + "/" + filename

                                count_of_images_found += 1
                                if os.path.isfile(filename_save_and_path):
                                    logger.debug(f"png already saved {filename} so ignoring")
                                else:
                                    logger.debug(f"png found new image and saving {filename}")
                                # write original bytes to file in binary mode
                                    with open(filename_save_and_path, "wb") as f:
                                        f.write(img_bytes_io.getbuffer())
                                    filesize = os.path.getsize(filename_save_and_path)
                                    logger.debug(f'png Filesize is {round(filesize/1000, 1)}kb')
                                    if filesize < 30000:
                                        logger.debug(f'png Filesize is less the 30k, so deleting image')
                                        count_of_images_found -= 1
                                        os.remove(filename_save_and_path)
            if count_of_images_found == 0:
                with open(warc_file_name, 'rb') as stream:
                    for record in ArchiveIterator(stream):
                         if record.rec_type == 'request':
                            uri = record.rec_headers.get_header('Referer:')
                            if "https://www.facebook.com/login/?" in uri:
                                content = record.content_stream().read()
                                foo = str(content)
                                logger.error(f"Catch for /login redirect and no images!")

                logger.error(f"No images found in {url_build=} - possible temporarily blocked by fb. Maybe redirected to login page. see warc file to see if I can get a temporarily blocked catch. see dave-now.warc")
                now = int(time.time())
                shutil.copyfile(warc_file_name, f"./dave-{now}.warc")
                return -1

    # except StopIteration: pass
        except Exception as ex:
            logger.error(f"Can't open {warc_file_name} exception is {ex}")
        logger.info("Finished parsing warc file")

        # return next_photo_id
        return next_photo_ids

    def upload_all_jpegs_from_temp_folder_and_generate_screenshot_and_html(self, url):
        thumbnail, thumbnail_index = None, None
        uploaded_media = []
        filenames = glob.glob(Storage.TMP_FOLDER + "/*.jpg") + glob.glob(Storage.TMP_FOLDER + "/*.png")

        # uploads when I iterate
        for filename in filenames:
            key = self.get_key(filename)
            self.storage.upload(filename, key)
            hash = self.get_hash(filename)
            cdn_url = self.storage.get_cdn_url(key)
            uploaded_media.append({'cdn_url': cdn_url, 'key': key, 'hash': hash})

        textual_output = ""
        page_cdn, page_hash, thumbnail = self.generate_media_page_html(url, uploaded_media, textual_output, thumbnail=thumbnail)
        key = "screenshot.png"

        logger.info('Getting screenshot')
        command = f"xvfb-run python3 playwright_screenshot.py {url}"
        lCmd = shlex.split(command) # Splits command into an array
        p = subprocess.Popen(lCmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = p.communicate() # Get the output and the err message
        std_out = out.decode("utf-8")
        logger.info(f"stdout from xvfb {std_out}")
        if 'Failed on fb accept cookies for url' in std_out:
            logger.error(f'Failed of fb accept cookies {url=} and {key=}')
        if err != b'':
            logger.error(err)

        filename = "./" + key
        if os.path.isfile(filename):
            self.storage.upload(filename, key, extra_args={'ACL': 'public-read', 'ContentType': 'image/png'})
            os.remove(filename)
        else:
            logger.error("Screenshot failed")

        screenshot_url = self.storage.get_cdn_url(key)

        title = ""
        datetime = ""

        # remove files from temp 
        files = glob.glob(Storage.TMP_FOLDER + '/*')
        for f in files:
            os.remove(f)

        # remove all crawls from collections directory
        mydir = os.getcwd() + f"/crawls/collections/"
        try:
            shutil.rmtree(mydir)
        except OSError as e:
            logger.error("Error: %s - %s." % (e.filename, e.strerror))

        return ArchiveResult(status="success", cdn_url=page_cdn, screenshot=screenshot_url, hash=page_hash, thumbnail=thumbnail, thumbnail_index=thumbnail_index, timestamp=datetime, title=title)
