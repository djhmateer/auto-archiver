import mimetypes
import os, shutil, subprocess, uuid
from zipfile import ZipFile
from loguru import logger
from warcio.archiveiterator import ArchiveIterator

from ..core import Media, Metadata, ArchivingContext
from . import Enricher
from ..archivers import Archiver
from ..utils import UrlUtil


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
            "browsertrix_home": {"default": None, "help": "Path to use with the custom browsertrix file locations, useful together with docker_commands"},
            "timeout": {"default": 120, "help": "timeout for WACZ generation in seconds"},
            "extract_media": {"default": True, "help": "If enabled all the images/videos/audio present in the WACZ archive will be extracted into separate Media. The .wacz file will be kept untouched."}
        }

    def download(self, item: Metadata) -> Metadata:
        # this new Metadata object is required to avoid duplication
        result = Metadata()
        result.merge(item)
        if self.enrich(result):
            return result.success("wacz")

    def enrich(self, to_enrich: Metadata) -> bool:
        if to_enrich.get_media_by_id("browsertrix"):
            logger.info(f"WACZ enricher had already been executed: {to_enrich.get_media_by_id('browsertrix')}")
            return True

        url = to_enrich.get_url()

        collection = str(uuid.uuid4())[0:8]

        browsertrix_home = self.browsertrix_home or os.path.abspath(ArchivingContext.get_tmp_dir())

        if os.environ.get('RUNNING_IN_DOCKER', 0) == '1':
            logger.debug(f"generating WACZ without Docker for {url=}")

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
                "--timeout", str(self.timeout)]

            if self.profile:
                cmd.extend(["--profile", os.path.join("/app", str(self.profile))])
        else:
            logger.debug(f"generating WACZ in Docker for {url=}")
            if not self.docker_commands:
                self.docker_commands = ["docker", "run", "--rm", "-v", f"{browsertrix_home}:/crawls/", "webrecorder/browsertrix-crawler"]
            cmd = self.docker_commands + [
                "crawl",
                "--url", url,
                "--scopeType", "page",
                "--generateWACZ",
                "--text",
                "--screenshot", "fullPage",
                "--collection", collection,
                "--behaviors", "autoscroll,autoplay,autofetch,siteSpecific",
                "--behaviorTimeout", str(self.timeout),
                "--timeout", str(self.timeout)
            ]

            if self.profile:
                profile_fn = os.path.join(browsertrix_home, "profile.tar.gz")
                logger.debug(f"copying {self.profile} to {profile_fn}")
                shutil.copyfile(self.profile, profile_fn)
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
            filename = os.path.join(browsertrix_home, "collections", collection, f"{collection}.wacz")

        if not os.path.exists(filename):
            logger.warning(f"Unable to locate and upload WACZ  {filename=}")
            return False

        to_enrich.add_media(Media(filename), "browsertrix")
        if self.extract_media:
            self.extract_media_from_wacz(to_enrich, filename)
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


        # if strategy 0 - ie we're on js viewer page already
        # **************** need ro find the url of the page

        url = to_enrich.get_url()

        # if this first root page is strategy 0 then get the image as will be full resolution 
        if "facebook.com/photo" in url:
            # https://www.facebook.com/photo/?fbid=1646726009098072&set=pcb.1646726145764725
            crawl_and_get_media_from_sub_page = False
        else:
             # 'https://www.facebook.com/khitthitnews/posts/pfbid0PTvT6iAccWqatvbDQNuqpFwL5WKzHuLK4QjP97Fwut637CV3XXQU53z1s2bJMAKwl'
            crawl_and_get_media_from_sub_page = True

        # DM facbook archiving
        # as will want to crawl based on the first image found
        # and click on to get to the viewer which gives high res images
        # and access to more than 5 images
        if "facebook.com" in to_enrich.netloc:
            logger.warning("special facebook codepath which will crawl to to js photo viewer")
            with open(warc_filename, 'rb') as warc_stream:
                for record in ArchiveIterator(warc_stream):
                    # only include fetched resources
                    if record.rec_type == "resource":  # browsertrix screenshots
                        fn = os.path.join(tmp_dir, f"warc-file-{counter}.png")
                        with open(fn, "wb") as outf: outf.write(record.raw_stream.read())
                        m = Media(filename=fn)
                        to_enrich.add_media(m, "browsertrix-screenshot")
                        counter += 1
                        # DM added as want to go to next record from here
                        continue

                    # DM catch for strategy 1 - Part 1
                    if record.rec_type == 'request' and crawl_and_get_media_from_sub_page == True:
                        uri = record.rec_headers.get_header('WARC-Target-URI')
                        if "bulk-route-definitions/" in uri:
                            content = record.content_stream().read()
                            foo = str(content)

                            # the url which we want to build is
                            # https://www.facebook.com/photo/?fbid=1646726009098072&set=pcb.1646726145764725

                            # in this record we can find it in this format

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

                                logger.warning(f"Strategy 1 and 3 {fb_id=} and {set_id=}")
                                bar = f'https://www.facebook.com/photo/?fbid={fb_id}&set=pcb.{set_id}'
                                logger.warning(f'url to go to next with full res image is {bar}')

                                # so now we have a starting point for a crawl
                                # Part 2
                                fb_ids_to_request = [] 
                                fb_ids_requested = []
                                while (True):
                                    builder_url = f"https://www.facebook.com/photo?fbid={fb_id}&set=pcb.{set_id}"

                                    fb_ids_requested.append(fb_id)

                                    logger.warning(f"Part 2 trying url {builder_url}")

                                    # this saves to the temp directory
                                    # but we need to patch into our object somehow
                                    # next_photo_ids = self.save_jpegs_to_temp_folder_from_url_using_browsertrix(builder_url)
                                    next_photo_ids = self.save_jpegs_to_temp_folder_from_url_using_browsertrix(builder_url, to_enrich)

                                    # DM hacking out to see if the temp files are uploaded
                                    # need to continue the crawl here
                                    break
                                
                                # DM - this worked sometimes when 104.jpg was present

                                # # add in the image to see if can get it upload
                                # # FileNotFoundError: [Errno 2] No such file or directory: './tmpl_0ta1jlwarc-file-100.jpg'
                                # fn = tmp_dir + "/warc-file-104.jpg"
                                # record_url = "https://www.facebook.com/photo?fbid=1646726009098072&set=pcb.1646726145764725"
                                # m = Media(filename=fn)
                                # # this is not right - but?
                                # m.set("src", record_url)

                                # # './tmp9i2uauur/warc-file-3.jpg'
                                # # m.filename = self.download_from_url(record_url_best_qual, warc_fn, to_enrich)
                                # # **********
                                # # m.filename = tmp_dir + fn
                                # # m.set("src", record_url_best_qual)
                                # m.set("src_alternative", record_url)
                                # warc_fn = "warc-file-104.jpg"
                                # to_enrich.add_media(m, warc_fn)
                                
                                # # to_enrich contains the wacz and 4 images
                                # # to_enrich.add_media(m, warc_fn)
                                # counter += 1
                                # seen_urls.add(record_url)

                        # end of special case for when found bulk route definition for the js viewoer
                        # maybe we don't need any of below for FB archiving
                        # as always need full res images
                        continue

                    # DM - only want full res images from detailed js viewer if available
                    # strategy 0 would use this as is a full res image already

                    # strat 0
                    # not working - getting cannot add 2 pieces of media with same id 100
                    if crawl_and_get_media_from_sub_page == False:
                        logger.debug('Facebook probably strategy 1 ie no sub pages and just get the full res image from this page')

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

                    # DM if size of media file is <30k discard
                    # if os.path.getsize(m.filename) < 30000: continue
                
                    to_enrich.add_media(m, warc_fn)
                    counter += 1
                    seen_urls.add(record_url)
            logger.info(f"WACZ extract_media finished, found {counter} relevant media file(s)")


    def save_jpegs_to_temp_folder_from_url_using_browsertrix(self, url_build, to_enrich: Metadata):
            logger.debug(f'{url_build=}')
            with open('url.txt', 'w') as file:
                file.write(url_build)

            tmp_dir = ArchivingContext.get_tmp_dir()
            # eg ./tmpehz86le1
            logger.warning(f'temp dir is {tmp_dir=}')

            # call browsertrix and get a warc file using a logged in facebook profile
            # this will get full resolution image which we can then save as a jpg
            logger.info(f"Part 2 - calling Browsertrix in Docker {url_build}")

            collection = str(uuid.uuid4())[0:8]
            # browsertrix_home = self.browsertrix_home or os.path.abspath(ArchivingContext.get_tmp_dir())

            # TODO for some reason this fails and doesn't above so lets hardcode
            # browsertrix_home = os.path.abspath(tmp_dir)
            # ./tmproglny_g
            foo = tmp_dir[1:]
            browsertrix_home = f'/mnt/c/dev/v6-auto-archiver{foo}'

            docker_commands = ["docker", "run", "--rm", "-v", f"{browsertrix_home}:/crawls/", "webrecorder/browsertrix-crawler"]
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

            try:
                logger.info(f"Running browsertrix-crawler: {' '.join(cmd)}")
                subprocess.run(cmd, check=True)
            except Exception as e:
                logger.error(f"WACZ generation failed: {e}")
                return False

            if os.getenv('RUNNING_IN_DOCKER'):
                filename = os.path.join("collections", collection, f"{collection}.wacz")
            else:
                # filename = os.path.join(browsertrix_home, "collections", collection, f"{collection}.wacz")
                # if we use --combineWarc then can reference this directly without doing a merge
                filename = os.path.join(browsertrix_home, "collections", collection, f"{collection}_0.warc.gz")

            if not os.path.exists(filename):
                logger.warning(f"Unable to locate and upload WACZ  {filename=}")
                return False
            logger.warning(f'Part 2 wacz to look at {filename=}')

            # '/mnt/c/dev/v6-auto-archiver/tmpprkee43l/collections/a1d9c3fb/a1d9c3fb.wacz'
            # wacz_filename = filename
            # a copy of extract_media_from_wacz
            # make it work first then refactor
            # unzipping the .wacz
            # tmp_dir = ArchivingContext.get_tmp_dir()
            # unzipped_dir = os.path.join(tmp_dir, "unzipped")
            # with ZipFile(wacz_filename, 'r') as z_obj:
            #     z_obj.extractall(path=unzipped_dir)

            # # if warc is split into multiple gzip chunks, merge those
            # warc_dir = os.path.join(unzipped_dir, "archive")

            # wfn = f'{collection}.warc'
            # warc_filename = os.path.join(tmp_dir, wfn)
            # with open(warc_filename, 'wb') as outfile:
            #     for filename in sorted(os.listdir(warc_dir)):
            #         if filename.endswith('.gz'):
            #             chunk_file = os.path.join(warc_dir, filename)
            #             with open(chunk_file, 'rb') as infile:
            #                 shutil.copyfileobj(infile, outfile)

            warc_filename = filename
            counter = 100
            seen_urls = set()
            with open(warc_filename, 'rb') as warc_stream:
                for record in ArchiveIterator(warc_stream):
                    # only include fetched resources
                    # if record.rec_type == "resource":  # browsertrix screenshots
                    #     fn = os.path.join(tmp_dir, f"warc-file-{counter}.png")
                    #     with open(fn, "wb") as outf: outf.write(record.raw_stream.read())
                    #     m = Media(filename=fn)
                    #     to_enrich.add_media(m, "browsertrix-screenshot")
                    #     counter += 1
                    #     # DM added as want to go to next record from here
                    #     continue

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

                    # DM if size of media file is <30k discard
                    fs = os.path.getsize(fn)
                    if fs < 30000: 
                        os.remove(fn)
                        continue

                    # add in the image to see if can get it upload
                    # fn = tmp_dir + "/warc-file-104.jpg"
                    # record_url = "https://www.facebook.com/photo?fbid=1646726009098072&set=pcb.1646726145764725"
                    m = Media(filename=fn)
                    # this is not right - but?
                    m.set("src", record_url)

                    # './tmp9i2uauur/warc-file-3.jpg'
                    # m.filename = self.download_from_url(record_url_best_qual, warc_fn, to_enrich)
                    # **********
                    # m.filename = tmp_dir + fn
                    # m.set("src", record_url_best_qual)
                    m.set("src_alternative", record_url)
                    # warc_fn = "warc-file-104.jpg"
                    to_enrich.add_media(m, warc_fn)
                    logger.info(f'Adding {fn=} which is {fs} bytes {record_url=} ')
                                
                    # to_enrich contains the wacz and 4 images
                    # to_enrich.add_media(m, warc_fn)
                    counter += 1
                    seen_urls.add(record_url)

                    # DM hack out
                    # m = Media(filename=fn)
                    # m.set("src", record_url)
                    # # if a link with better quality exists, try to download that
                    # if record_url_best_qual != record_url:
                    #     try:
                    #         m.filename = self.download_from_url(record_url_best_qual, warc_fn, to_enrich)
                    #         m.set("src", record_url_best_qual)
                    #         m.set("src_alternative", record_url)
                    #     except Exception as e: logger.warning(f"Unable to download best quality URL for {record_url=} got error {e}, using original in WARC.")

                    # # remove bad videos
                    # if m.is_video() and not m.is_valid_video(): continue

                    # # DM if size of media file is <30k discard
                    # if os.path.getsize(m.filename) < 30000: continue
                
                    # # to_enrich contains the wacz and 4 images
                    # to_enrich.add_media(m, warc_fn)
                    # counter += 1
                    # seen_urls.add(record_url)

            next_photo_id = 0
            next_photo_ids = []
            return next_photo_ids





            collection_name = str(int(time.time()))

            # have seen the warc not being created at timeout 5 secs
            # docker needs to be setup to run as non root (eg dave)
            # see server-build.sh
            # --it for local debugging (interactive terminal)
            command = f"docker run -v {os.getcwd()}/crawls:/crawls/ -v {os.getcwd()}/url.txt:/app/url.txt --rm webrecorder/browsertrix-crawler crawl --urlFile /app/url.txt --scopeType page --combineWarc --timeout 10 --profile /crawls/profiles/facebook-logged-in.tar.gz --collection {collection_name}"
            logger.info(command)

            lCmd = shlex.split(command) # Splits command into an array
            p = subprocess.Popen(lCmd, user="dave", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            out, err = p.communicate() # Get the output and the err message

            # foo = out.decode("utf-8")
            # logger.info(foo)
            # catch docker err here?
                
            # for prod - docker runs as non-root (dave) but write files as root. So change perms.
            # we're running nopasswd for sudo 
            if os.getcwd() == "/mnt/c/dev/auto-archiver":
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