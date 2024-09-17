import jsonlines
import mimetypes
import os, shutil, subprocess
from zipfile import ZipFile
from loguru import logger
from warcio.archiveiterator import ArchiveIterator

from ..core import Media, Metadata, ArchivingContext
from . import Enricher
from ..archivers import Archiver
from ..utils import UrlUtil, random_str


class WaczArchiverEnricher(Enricher, Archiver):
    """
    Uses https://github.com/webrecorder/browsertrix-crawler to generate a .WACZ archive of the URL
    If used with [profiles](https://github.com/webrecorder/browsertrix-crawler#creating-and-using-browser-profiles)
    it can become quite powerful for archiving private content.
    When used as an archiver it will extract the media from the .WACZ archive so it can be enriched.
    """
    name = "wacz_archiver_enricher"

    def __init__(self, config: dict) -> None:
        # without this STEP.__init__ is not called
        super().__init__(config)

    @staticmethod
    def configs() -> dict:
        return {
            "profile": {"default": None, "help": "browsertrix-profile (for profile generation see https://github.com/webrecorder/browsertrix-crawler#creating-and-using-browser-profiles)."},
            "docker_commands": {"default": None, "help":"if a custom docker invocation is needed"},
            # "timeout": {"default": 120, "help": "timeout for WACZ generation in seconds"},
            "timeout": {"default": 200, "help": "timeout for WACZ generation in seconds"},
            "extract_media": {"default": True, "help": "If enabled all the images/videos/audio present in the WACZ archive will be extracted into separate Media. The .wacz file will be kept untouched."}
        }
    
	# DM setup and clenup are new functions
	# which I'm commenting out for now to make sure everything still works
    def setup(self) -> None:
        #self.use_docker = os.environ.get('WACZ_ENABLE_DOCKER') or not os.environ.get('RUNNING_IN_DOCKER')
        #self.docker_in_docker = os.environ.get('WACZ_ENABLE_DOCKER') and os.environ.get('RUNNING_IN_DOCKER')

        #self.cwd_dind = f"/crawls/crawls{random_str(8)}"
        #self.browsertrix_home_host = os.environ.get('BROWSERTRIX_HOME_HOST')
        #self.browsertrix_home_container = os.environ.get('BROWSERTRIX_HOME_CONTAINER') or self.browsertrix_home_host
        # create crawls folder if not exists, so it can be safely removed in cleanup
        #if self.docker_in_docker:
         #   os.makedirs(self.cwd_dind, exist_ok=True)
        foo = 1

    def cleanup(self) -> None:
        #if self.docker_in_docker:
         #   logger.debug(f"Removing {self.cwd_dind=}")
          #  shutil.rmtree(self.cwd_dind, ignore_errors=True)
          foo = 1

    def download(self, item: Metadata) -> Metadata:
        # this new Metadata object is required to avoid duplication
        result = Metadata()
        result.merge(item)
        if self.enrich(result):
            return result.success("wacz")

    # On WSL2 in Dev I've seen spurious :ERR_NETWORK_CHANGED at 
    # errors from browsertrix which fails out of the crawl
    # It seems to be more solid on Linux production
    def enrich(self, to_enrich: Metadata) -> bool:
        if to_enrich.get_media_by_id("browsertrix"):
            logger.info(f"WACZ enricher had already been executed: {to_enrich.get_media_by_id('browsertrix')}")
            return True

        url = to_enrich.get_url()

        collection = random_str(8)

        # unknown why it fails on second time
        # only on WSL2 instance - Ubuntu prod is fine.

        # foo = ArchivingContext.get_tmp_dir()
        # this will fail as the call to os.getcwd() fails
        # https://stackoverflow.com/questions/3210902/python-why-does-os-getcwd-sometimes-crash-with-oserror
        # bar = os.path.abspath(foo)
        # but if we fix with using psutil way, then get errors further down with shutil copying

        hard_code_directory_for_wsl2 ='/mnt/c/dev/v6-auto-archiver' 
        try:
            browsertrix_home_host = os.environ.get('BROWSERTRIX_HOME_HOST') or os.path.abspath(ArchivingContext.get_tmp_dir())
        except FileNotFoundError as e:
            logger.debug('Dev environment found using ' + hard_code_directory_for_wsl2)
            browsertrix_home_host = hard_code_directory_for_wsl2 + ArchivingContext.get_tmp_dir()[1:]

        browsertrix_home_container = os.environ.get('BROWSERTRIX_HOME_CONTAINER') or browsertrix_home_host

        cmd = [
            "crawl",
            "--url", url,
            "--scopeType", "page",
            "--generateWACZ",
            "--text",
            "--screenshot", "fullPage",
            "--collection", collection,
            "--id", collection,
            "--saveState", "never",
            "--behaviors", "autoscroll,autoplay,autofetch,siteSpecific",
            "--behaviorTimeout", str(self.timeout),
            "--timeout", str(self.timeout),
            "--postLoadDelay", "160"]

        # call docker if explicitly enabled or we are running on the host (not in docker)
        use_docker = os.environ.get('WACZ_ENABLE_DOCKER') or not os.environ.get('RUNNING_IN_DOCKER')

#88 - generating WACZ in Docker for url='https://www.facebook.com/khitthitnews/posts/pfbid02tX6o4TcNykMYyH4Wjbz3ckq5bH5rRr7aqLFCymkWwhVzPJGwq2mSCnp9jYZ8CVdTl'
# 89 - browsertrix_home_host='/home/dave/auto-archiver/tmplwb1vufr' browsertrix_home_container='/home/dave/auto-archiver/tmplwb1vufr'
# 99 - copying secrets/profile.tar.gz to /home/dave/auto-archiver/tmplwb1vufr/profile.tar.gz
        if use_docker:
            logger.debug(f"generating WACZ in Docker for {url=}")
            logger.debug(f"{browsertrix_home_host=} {browsertrix_home_container=}")


            if self.docker_commands:
                cmd = self.docker_commands + cmd
            else:
                # 0.11.2 works - otherwise the test case OS4892 on AA Demo Main doesn't seem to crawl properly (it was multiple screenshots)
                # note there is another part further down the code which needs to be changed too.
                cmd = ["docker", "run", "--rm", "-v", f"{browsertrix_home_host}:/crawls/", "webrecorder/browsertrix-crawler"] + cmd
                # cmd = ["docker", "run", "--rm", "-v", f"{browsertrix_home_host}:/crawls/", "webrecorder/browsertrix-crawler:0.11.2"] + cmd

            if self.profile:
                profile_fn = os.path.join(browsertrix_home_container, "profile.tar.gz")
                logger.debug(f"copying {self.profile} to {profile_fn}")
                shutil.copyfile(self.profile, profile_fn)
                cmd.extend(["--profile", os.path.join("/crawls", "profile.tar.gz")])

        else:
            logger.debug(f"generating WACZ without Docker for {url=}")

            if self.profile:
                cmd.extend(["--profile", os.path.join("/app", str(self.profile))])

        try:
            logger.info(f"Running browsertrix-crawler: {' '.join(cmd)}")
            subprocess.run(cmd, check=True)
        except Exception as e:
            logger.error(f"WACZ generation failed: {e}")
            return False

        if use_docker:
            wacz_fn = os.path.join(browsertrix_home_container, "collections", collection, f"{collection}.wacz")
        else:
            wacz_fn = os.path.join("collections", collection, f"{collection}.wacz")

        if not os.path.exists(wacz_fn):
            logger.warning(f"Unable to locate and upload WACZ  {wacz_fn=}")
            return False

        to_enrich.add_media(Media(wacz_fn), "browsertrix")

        if self.extract_media:
            self.extract_media_from_wacz(to_enrich, wacz_fn)
        return True

    def extract_media_from_wacz(self, to_enrich: Metadata, wacz_filename: str) -> None:
        """
        Receives a .wacz archive, and extracts all relevant media from it, adding them to to_enrich.
        """
        logger.info(f"WACZ extract_media flag is set, extracting media from {wacz_filename=}")

        # unzipping the .wacz
        tmp_dir = ArchivingContext.get_tmp_dir()
        unzipped_dir = os.path.join(tmp_dir, "unzipped")
        with ZipFile(wacz_filename, 'r') as z_obj:
            z_obj.extractall(path=unzipped_dir)

        # DM - use --combineWarc  so don't have to do this?
        # if warc is split into multiple gzip chunks, merge those
        warc_dir = os.path.join(unzipped_dir, "archive")
        warc_filename = os.path.join(tmp_dir, "merged.warc")
        with open(warc_filename, 'wb') as outfile:
            for filename in sorted(os.listdir(warc_dir)):
                if filename.endswith('.gz'):
                    chunk_file = os.path.join(warc_dir, filename)
                    with open(chunk_file, 'rb') as infile:
                        shutil.copyfileobj(infile, outfile)

        # get media out of .warc
        counter = 0
        seen_urls = set()

        url = to_enrich.get_url()

        if "facebook.com" in to_enrich.netloc:
            logger.debug("special facebook codepath to extract media")

            # if this first root page is strategy 0 then get the images as will be full resolution already
            if "facebook.com/photo" in url:
                # strategy 0 eg https://www.facebook.com/photo/?fbid=1646726009098072&set=pcb.1646726145764725
                crawl_and_get_media_from_sub_page = False
            else:
                # strategy 1 eg https://www.facebook.com/khitthitnews/posts/pfbid0PTvT6iAccWqatvbDQNuqpFwL5WKzHuLK4QjP97Fwut637CV3XXQU53z1s2bJMAKwl
                crawl_and_get_media_from_sub_page = True

            with open(warc_filename, 'rb') as warc_stream:

                for record in ArchiveIterator(warc_stream):
                    # only include fetched resources
                    if record.rec_type == "resource":  # browsertrix screenshots
                        fn = os.path.join(tmp_dir, f"warc-file-{counter}.png")
                        with open(fn, "wb") as outf: outf.write(record.raw_stream.read())
                        m = Media(filename=fn)
                        # DM there are 2 screenshots
                        # the first one is bonkers and seems to be a png but isn't.
                        # ignore it as it is always there

                        # DMAug 18 2024
                        # testing screenshots
                        if (counter == 0):
                            logger.debug(f'ignoring the first screenshot as it is always a png but isn\'t')
                        else:
                            to_enrich.add_media(m, f"browsertrix-screenshot-{counter}")

                        counter += 1
                        # DM added as want to go to next record from here
                        continue

                    # Get fb_id and set_id logic
                    # DM catch for strategy 1 - Part 1
                    if record.rec_type == 'request' and crawl_and_get_media_from_sub_page == True:
                        uri = record.rec_headers.get_header('WARC-Target-URI')
                        if "bulk-route-definitions/" in uri:
                            content = record.content_stream().read()
                            foo = str(content)

                            # Strategy 1 test
                            # photo%2F%3Ffbid%3D1646726009098072%26set%3Dpcb.1646726145764725%26
                            # fbid = 1646726009098072
                            # set = pcb.1646726145764725
                            photo_string_start_pos = foo.find(f'photo%2F%3Ffbid%3D',0)

                            if (photo_string_start_pos > 0):
                                fbid_start_pos = photo_string_start_pos + 18

                                middle_26_start_pos = foo.find(f'%26', fbid_start_pos)
    
                                fb_id = foo[fbid_start_pos:middle_26_start_pos]
                            
                                # photo%2F%3Ffbid%3D1646726009098072%26set%3Dpcb.1646726145764725%26
                                set_end_pos = foo.find(f'%26', middle_26_start_pos+1)

                                set_id = foo[middle_26_start_pos+13:set_end_pos]

                                logger.info(f"  *** Part 1 - Strategy 1 {fb_id=} and {set_id=}")
                                bar = f'https://www.facebook.com/photo/?fbid={fb_id}&set=pcb.{set_id}'

                                logger.debug(f'starting url go to next full res image js viewer page, and start crawl is {bar}')
                                
                                # Part 2
                                # fb_ids_to_request = [] 
                                fb_ids_requested = []
                                while (True):
                                    builder_url = f"https://www.facebook.com/photo?fbid={fb_id}&set=pcb.{set_id}"

                                    fb_ids_requested.append(fb_id)

                                    logger.info(f"  *** Part 2 next trying url for js page {builder_url}")

                                    next_fb_id = self.save_images_to_enrich_object_from_url_using_browsertrix(builder_url, to_enrich, fb_id)

                                    total_images = len(to_enrich.media)
                                    if total_images > 70:
                                        logger.warning('Total images is > 70 so stopping crawl')
                                        break
                                    if next_fb_id in fb_ids_requested:
                                        logger.debug('have looped around all photos in js viewer so end')
                                        break
                                    else: 
                                        fb_id = next_fb_id

                            else:
                                logger.debug('photo string not found in bulk-route-definitions - this is normal. 1 out of 3 have seen work... ')
                                logger.debug('it also could be a single image which is different')

                                # Strategy x - single photo in js viewer
                                # photos%2Fa.386800725090613%2F1425302087907133%2F%3F
                                photos_string_start_pos = foo.find(f'photos%2Fa.',0)
                                if (photos_string_start_pos > 0):
                                    fbid_start_pos = photos_string_start_pos + 11

                                    middle_2F_start_pos = foo.find(f'%2F', fbid_start_pos)
    
                                    fb_id = foo[fbid_start_pos:middle_2F_start_pos]
                            
                                    set_end_pos = foo.find(f'%2F', middle_2F_start_pos+1)

                                    set_id = foo[middle_2F_start_pos+3:set_end_pos]

                                    logger.debug(f"Strategy x single photo {fb_id=} and {set_id=}")

                                    # route_urls[0]=%2Fkhitthitnews%3F
                                    name_thing_start_pos = foo.find(f'route_urls[0]=%2F', 0)

                                    name_thing_end_pos = foo.find(f'%3F',name_thing_start_pos)

                                    name_thing = foo[name_thing_start_pos + 17:name_thing_end_pos]

                                    # https://www.facebook.com/khitthitnews/photos/a.386800725090613/1425302087907133/

                                    builder_url = f'https://www.facebook.com/{name_thing}/photos/a.{fb_id}/{set_id}/'
                                    logger.debug('url to get next for the single photo is ' + builder_url)

                                    next_fb_id = self.save_images_to_enrich_object_from_url_using_browsertrix(builder_url, to_enrich, fb_id)

                                    # no crawl as a single photo only which has already been added

                                    # this is probably meant to be a single photo
                                    # however there may be many more via left and right arrows, but we don't want

                                
                        # end of strategy 1 
                        continue # to the next record

                    # only strategy 0
                    # save image logic
                    # as we don't want media from other strategies from this root page
                    if crawl_and_get_media_from_sub_page == False:
                        if record.rec_type != 'response': continue
                        record_url = record.rec_headers.get_header('WARC-Target-URI')
                        if not UrlUtil.is_relevant_url(record_url):
                            logger.debug(f"Skipping irrelevant URL {record_url} but it's still present in the WACZ.")
                            continue
                        if record_url in seen_urls:
                            logger.debug(f"Skipping already seen URL {record_url}.")
                            continue

                        # filter by media mimetypes
                        content_type = record.http_headers.get("Content-Type")
                        if not content_type: continue
                        if not any(x in content_type for x in ["video", "image", "audio"]): continue

                        # DM - ignore this specialised content type for facebook
                        if content_type == "image/x.fb.keyframes": continue

                        # create local file and add media
                        ext = mimetypes.guess_extension(content_type)
                        warc_fn = f"warc-file-{counter}{ext}"
                        fn = os.path.join(tmp_dir, warc_fn)

                        record_url_best_qual = UrlUtil.twitter_best_quality_url(record_url)
                        
                        with open(fn, "wb") as outf: outf.write(record.raw_stream.read())

                        # fn = './tmpil4kenvz/warc-file-0.jpg'
                        m = Media(filename=fn)
                        # record_url = 'https://scontent.ffab1-1.fna.fbcdn.net/v/t39.30808-1/340998090_786100926215087_3926180936792898436_n.jpg?stp=cp0_dst-jpg_p40x40&_nc_cat=1&ccb=1-7&_nc_sid=754033&_nc_ohc=8f70FA4fXssAX_OKDt0&_nc_oc=AQmMBocWCJEOrxM00aa52d3EcGEpbsCGKYMJSZcCgtOXrSnz66eWPGLgiuZ7GU3LiqM&_nc_ht=scontent.ffab1-1.fna&oh=00_AfC-Xg0lgD-HjujjdkUYrvwtgiFbq6tvuZJyu5Mfgnk24A&oe=650874A9'
                        m.set("src", record_url)
                        # if a link with better quality exists, try to download that
                        if record_url_best_qual != record_url:
                            try:
                                m.filename = self.download_from_url(record_url_best_qual, warc_fn, to_enrich)
                                m.set("src", record_url_best_qual)
                                m.set("src_alternative", record_url)
                            except Exception as e: logger.warning(f"Unable to download best quality URL for {record_url=} got error {e}, using original in WARC.")

                        # remove bad videos
                        if m.is_video() and not m.is_valid_video(): continue

                        # DM if size of media file is <30k discard
                        if os.path.getsize(m.filename) < 30000: continue
                    
                        logger.debug(f'Facebook strategy 0. Saving {m.filename}')
                        # to_enrich contains the wacz and 4 images
                        # warc_fn = 'warc-file-0.jpg'
                        to_enrich.add_media(m, warc_fn)
                        counter += 1
                        seen_urls.add(record_url)
            logger.info(f"special case FB WACZ extract_media finished, found {counter} relevant media file(s)")

        ## normal non FB media extraction
        else:
            with open(warc_filename, 'rb') as warc_stream:
                for record in ArchiveIterator(warc_stream):
                    # only include fetched resources
                    if record.rec_type == "resource":  # screenshots
                        fn = os.path.join(tmp_dir, f"warc-file-{counter}.png")
                        with open(fn, "wb") as outf: outf.write(record.raw_stream.read())
                        m = Media(filename=fn)
                        to_enrich.add_media(m, "browsertrix-screenshot")
                        counter += 1

                    if record.rec_type != 'response': continue
                    record_url = record.rec_headers.get_header('WARC-Target-URI')
                    if not UrlUtil.is_relevant_url(record_url):
                        logger.debug(f"Skipping irrelevant URL {record_url} but it's still present in the WACZ.")
                        continue
                    if record_url in seen_urls:
                        logger.debug(f"Skipping already seen URL {record_url}.")
                        continue

                    # filter by media mimetypes
                    content_type = record.http_headers.get("Content-Type")
                    if not content_type: continue
                    if not any(x in content_type for x in ["video", "image", "audio"]): continue

                    # DM - ignore this specialised content type for facebook
                    # if content_type == "image/x.fb.keyframes": continue

                    # create local file and add media
                    ext = mimetypes.guess_extension(content_type)
                    warc_fn = f"warc-file-{counter}{ext}"
                    fn = os.path.join(tmp_dir, warc_fn)

                    record_url_best_qual = UrlUtil.twitter_best_quality_url(record_url)
                    with open(fn, "wb") as outf: outf.write(record.raw_stream.read())

                    m = Media(filename=fn)
                    m.set("src", record_url)
                    # if a link with better quality exists, try to download that
                    if record_url_best_qual != record_url:
                        try:
                            m.filename = self.download_from_url(record_url_best_qual, warc_fn, to_enrich)
                            m.set("src", record_url_best_qual)
                            m.set("src_alternative", record_url)
                        except Exception as e: logger.warning(f"Unable to download best quality URL for {record_url=} got error {e}, using original in WARC.")

                    # remove bad videos
                    if m.is_video() and not m.is_valid_video(): continue

                    logger.debug(f'Normal strategy 0. Saving {m.filename}')

                    to_enrich.add_media(m, warc_fn)
                    counter += 1
                    seen_urls.add(record_url)
            logger.info(f"WACZ extract_media finished, found {counter} relevant media file(s)")


    # only used by FB codepath
    def save_images_to_enrich_object_from_url_using_browsertrix(self, url_build, to_enrich: Metadata, current_fb_id):
            logger.debug(f' Inside Part 2')
            # call browsertrix and get a warc file using a logged in facebook profile
            # this will get full resolution image which we can then save as a jpg

            with open('url.txt', 'w') as file:
                file.write(url_build)

            # collection = str(uuid.uuid4())[0:8]
            collection = random_str(8)

            hard_code_directory_for_wsl2 ='/mnt/c/dev/v6-auto-archiver' 
            browsertrix_home = ""
            tmp_dir = ArchivingContext.get_tmp_dir()
            try:
                # DM get strange AttributeError if include self.browsertrix_home - taken out for now 
                # browsertrix_home = self.browsertrix_home or os.path.abspath(ArchivingContext.get_tmp_dir())
                # browsertrix_home = os.path.abspath(ArchivingContext.get_tmp_dir())
                browsertrix_home = os.path.abspath(tmp_dir)
            except FileNotFoundError: 
                logger.debug(f'Dev found in function 2')
                # tmp_dir = ArchivingContext.get_tmp_dir()
                foo = tmp_dir[1:]
                browsertrix_home = f'{hard_code_directory_for_wsl2}{foo}'

            docker_commands = ["docker", "run", "--rm", "-v", f"{browsertrix_home}:/crawls/", "webrecorder/browsertrix-crawler"]
            # docker_commands = ["docker", "run", "--rm", "-v", f"{browsertrix_home}:/crawls/", "webrecorder/browsertrix-crawler:0.11.2"]
            cmd = docker_commands + [
                "crawl",
                "--url", url_build,
                "--scopeType", "page",
                "--generateWACZ",
                "--text",
                "--screenshot", "fullPage",
                "--collection", collection,
                "--behaviors", "autoscroll,autoplay,autofetch,siteSpecific",
                "--behaviorTimeout", str(self.timeout),
                "--timeout", str(self.timeout),
                "--combineWarc"
            ]

            if self.profile:
                # profile_fn = os.path.join(browsertrix_home_container, "profile.tar.gz")
                # logger.debug(f"copying {self.profile} to {profile_fn}")
                # shutil.copyfile(self.profile, profile_fn)
                # cmd.extend(["--profile", os.path.join("/crawls", "profile.tar.gz")])
                cmd.extend(["--profile", os.path.join("/crawls", "profile.tar.gz")])

            try:
                logger.info(f"Running browsertrix-crawler: {' '.join(cmd)}")
                subprocess.run(cmd, check=True)
            except Exception as e:
                logger.error(f"WACZ generation failed: {e}")
                return False

            if os.getenv('RUNNING_IN_DOCKER'):
                filename = os.path.join("collections", collection, f"{collection}.wacz")
            else:
                filename = os.path.join(browsertrix_home, "collections", collection, f"{collection}_0.warc.gz")

            if not os.path.exists(filename):
                logger.warning(f"Unable to locate and upload WACZ  {filename=}")
                return False

            warc_filename = filename
            counter = 100
            seen_urls = set()
            next_fb_id = 0
            with open(warc_filename, 'rb') as warc_stream:
                for record in ArchiveIterator(warc_stream):

                    # 1.Get next fb_id logic
                    if record.rec_type == 'request': 
                        uri = record.rec_headers.get_header('WARC-Target-URI')
                        if "bulk-route-definitions/" in uri:
                            content = record.content_stream().read()
                            foo = str(content)

                            # Strategy 1 test
                            # photo%2F%3Ffbid%3D1646726009098072%26set%3Dpcb.1646726145764725%26
                            # fbid = 1646726009098072
                            # set = pcb.1646726145764725
                            photo_string_start_pos = foo.find(f'photo%2F%3Ffbid%3D',0)
                            # photo_string_start_pos = foo.find(f'%2Fphotos%2Fpcb.',0)
  

                            if (photo_string_start_pos > 0):
                                fbid_start_pos = photo_string_start_pos + 18

                                middle_26_start_pos = foo.find(f'%26', fbid_start_pos)
    
                                # only need this!
                                next_fb_id_foo = foo[fbid_start_pos:middle_26_start_pos]

                                # check haven't found current page fb_id
                                if next_fb_id_foo == current_fb_id:
                                    logger.debug("found current fb_id so ignoring")
                                else:
                                    next_fb_id = next_fb_id_foo

                            else:
                                logger.debug('photo string not found in bulk-route-definitions - this is normal. 1 out of 3 have seen work')
                                
                        # end of strategy 1,2,3 
                        continue


                    if record.rec_type != 'response': continue
                    record_url = record.rec_headers.get_header('WARC-Target-URI')
                    
                    # 2.Save image logic
                    # THIS COULD BE A PROBLEM as each sub page will save 3 or 5 images probably
                    # filter by media mimetypes
                    content_type = record.http_headers.get("Content-Type")
                    if not content_type: continue
                    if not any(x in content_type for x in ["video", "image", "audio"]): continue

                    # DM - ignore this specialised content type for facebook
                    if content_type == "image/x.fb.keyframes": continue

                    # create local file and add media
                    ext = mimetypes.guess_extension(content_type)
                    warc_fn = f"warc-file-{collection}-{counter}{ext}"
                    fn = os.path.join(tmp_dir, warc_fn)

                    with open(fn, "wb") as outf: outf.write(record.raw_stream.read())

                    # FB serves many images in the page as helpers - we just want the main high res image
                    # many of the small images are from comments
                    # 1k jpg
                    # 22k png
                    # 35k png - mobile phone image
                    # 17k png
                    # gifs are common in comments

                    # DM if size of media file is < x discard
                    fs = os.path.getsize(fn)
                    if fs < 5000 and ext == ".jpg": continue
                    if fs < 37000 and ext == ".png": continue
                    if ext == ".gif": continue
                    if ext == ".ico": continue
                    if ext == None : continue

                    m = Media(filename=fn)
                    m.set("src", record_url)
                    m.set("src_alternative", record_url)
                    to_enrich.add_media(m, warc_fn)
                    logger.info(f'Adding {fn=} which is {fs} bytes {record_url=} ')
                                
                    counter += 1
                    seen_urls.add(record_url)

            return next_fb_id
