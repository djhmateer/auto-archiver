import datetime, os, yt_dlp, pysubs2
from loguru import logger

from . import Archiver
from ..core import Metadata, Media, ArchivingContext

# from playwright.sync_api import sync_playwright
import subprocess
import os


class YoutubeDLArchiver(Archiver):
    name = "youtubedl_archiver"

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.subtitles = bool(self.subtitles)
        self.comments = bool(self.comments)
        self.livestreams = bool(self.livestreams)
        self.live_from_start = bool(self.live_from_start)
        self.end_means_success = bool(self.end_means_success)
        self.allow_playlist = bool(self.allow_playlist)
        self.max_downloads = self.max_downloads

    @staticmethod
    def configs() -> dict:
        return {
            "facebook_cookie": {"default": None, "help": "optional facebook cookie to have more access to content, from browser, looks like 'cookie: datr= xxxx'"},
            "subtitles": {"default": False, "help": "download subtitles if available"},
            "comments": {"default": True, "help": "download all comments if available, may lead to large metadata"},
            "livestreams": {"default": False, "help": "if set, will download live streams, otherwise will skip them; see --max-filesize for more control"},
            "live_from_start": {"default": False, "help": "if set, will download live streams from their earliest available moment, otherwise starts now."},
            "proxy": {"default": "", "help": "http/socks (https seems to not work atm) proxy to use for the webdriver, eg https://proxy-user:password@proxy-ip:port"},
            "end_means_success": {"default": True, "help": "if True, any archived content will mean a 'success', if False this archiver will not return a 'success' stage; this is useful for cases when the yt-dlp will archive a video but ignore other types of content like images or text only pages that the subsequent archivers can retrieve."},
            'allow_playlist': {"default": False, "help": "If True will also download playlists, set to False if the expectation is to download a single video."},
            "max_downloads": {"default": "inf", "help": "Use to limit the number of videos to download when a channel or long page is being extracted. 'inf' means no limit."},
        }

    def download(self, item: Metadata) -> Metadata:
        url = item.get_url()

        if item.netloc in ['facebook.com', 'www.facebook.com'] and self.facebook_cookie:
            logger.debug('Using Facebook cookie')
            yt_dlp.utils.std_headers['cookie'] = self.facebook_cookie

        # ydl_options = {'outtmpl': os.path.join(ArchivingContext.get_tmp_dir(), f'%(id)s.%(ext)s'), 'quiet': False, 'noplaylist': not self.allow_playlist , 'writesubtitles': self.subtitles, 'writeautomaticsub': self.subtitles, "live_from_start": self.live_from_start, "proxy": self.proxy, "max_downloads": self.max_downloads, "playlistend": self.max_downloads}

        # DM Aug 24 Oauth plugin
        # ydl_options = {'outtmpl': os.path.join(ArchivingContext.get_tmp_dir(), f'%(id)s.%(ext)s'), 'quiet': False, 'noplaylist': not self.allow_playlist , 'writesubtitles': self.subtitles, 'writeautomaticsub': self.subtitles, "live_from_start": self.live_from_start, "proxy": self.proxy, "max_downloads": self.max_downloads, "playlistend": self.max_downloads, "username": "oauth2", "password": ""}

        # DM Oct 24 with new version of yt-dlp ie 2024.10.22 with in built OAuth2 plugin
        # pipenv shell
        # yt-dlp --username oauth --password "" https://www.youtube.com/watch?v=C0DPdy98e4c
        # logn as youtube use with flow
        ydl_options = {'outtmpl': os.path.join(ArchivingContext.get_tmp_dir(), f'%(id)s.%(ext)s'), 'quiet': False, 'noplaylist': not self.allow_playlist , 'writesubtitles': self.subtitles, 'writeautomaticsub': self.subtitles, "live_from_start": self.live_from_start, "proxy": self.proxy, "max_downloads": self.max_downloads, "playlistend": self.max_downloads, "username": "oauth", "password": ""}


        ydl = yt_dlp.YoutubeDL(ydl_options) # allsubtitles and subtitleslangs not working as expected, so default lang is always "en"

        try:
            # don't download since it can be a live stream
            info = ydl.extract_info(url, download=False)
            if info.get('is_live', False) and not self.livestreams:
                logger.warning("Livestream detected, skipping due to 'livestreams' configuration setting")
                return False
        except yt_dlp.utils.DownloadError as e:
            # DM Aug 24 - this error is caught.
            # Sign in to confirm you’re not a bot. This helps protect our community. Learn more
            logger.debug(f'No video - Youtube normal control flow: {e}')
            # DM added this to track
            if "Sign in to confirm" in str(e):
                logger.error("Sign in to confirm you’re not a bot. This helps protect our community. Learn more")
            return False
        except Exception as e:
            logger.debug(f'ytdlp exception which is normal for example a facebook page with images only will cause a IndexError: list index out of range. Exception is: \n  {e}')
            return False

        # this time download
        ydl = yt_dlp.YoutubeDL({**ydl_options, "getcomments": self.comments})
        #TODO: for playlist or long lists of videos, how to download one at a time so they can be stored before the next one is downloaded?

        # DM July - special feature to allow for not downloading the file if the column is set to n
        # if column not there then download as normal
        should_download = item.get("should_download", "").lower()
        if should_download in ["n", "no"]:
            info = ydl.extract_info(url, download=False)
        else:
            info = ydl.extract_info(url, download=True)

        if "entries" in info:
            entries = info.get("entries", [])
            if not len(entries):
                logger.warning('YoutubeDLArchiver could not find any video')
                return False
        else: entries = [info]

        result = Metadata()
        result.set_title(info.get("title"))
        if "description" in info: result.set_content(info["description"])

        # DM July.. assume all videos have a view_count
        view_count = info.get("view_count")
        result.set_view_count(view_count)

        location = info.get("location", "")
        result.set_location(location)

        comment_count = info.get("comment_count")
        result.set_comment_count(comment_count)

        like_count = info.get("like_count")
        result.set_like_count(like_count)

        channel = info.get("channel")
        result.set_channel(channel) 

        channel_follower_count = info.get("channel_follower_count")
        result.set_channel_follower_count(channel_follower_count)


        for entry in entries:
            try:
                filename = ydl.prepare_filename(entry)
                if not os.path.exists(filename):
                    filename = filename.split('.')[0] + '.mkv'

                new_media = Media(filename)
                for x in ["duration", "original_url", "fulltitle", "description", "upload_date"]:
                    if x in entry: new_media.set(x, entry[x])

                # read text from subtitles if enabled
                if self.subtitles:
                    for lang, val in (info.get('requested_subtitles') or {}).items():
                        try:    
                            subs = pysubs2.load(val.get('filepath'), encoding="utf-8")
                            text = " ".join([line.text for line in subs])
                            new_media.set(f"subtitles_{lang}", text)
                        except Exception as e:
                            logger.info(f"Error loading subtitle file {val.get('filepath')}: {e}")
                            logger.info(f"Normal code path if should_download is n")
                result.add_media(new_media)
            except Exception as e:
                logger.error(f"Error processing entry {entry}: {e}")

        # extract comments if enabled
        if self.comments:
            result.set("comments", [{
                "text": c["text"],
                "author": c["author"], 
                "timestamp": datetime.datetime.utcfromtimestamp(c.get("timestamp")).replace(tzinfo=datetime.timezone.utc)
            } for c in info.get("comments", [])])

        if (timestamp := info.get("timestamp")):
            timestamp = datetime.datetime.utcfromtimestamp(timestamp).replace(tzinfo=datetime.timezone.utc).isoformat()
            result.set_timestamp(timestamp)
        if (upload_date := info.get("upload_date")):
            upload_date = datetime.datetime.strptime(upload_date, '%Y%m%d').replace(tzinfo=datetime.timezone.utc)
            result.set("upload_date", upload_date)

        # DM July
        # run external tool to get screenshots
        # featured off the should_download flag.. so has to be set to y
        # if should_download == "y":
        # asdf = item.get("screen1_column_present")
        screen1 = item.screen1_column_present
        if screen1 == "y":
            logger.info("Found screen1 column so Running c31playwright which gets many screenshots on ads")

            # '/mnt/c/dev/v6-auto-archiver' - where the c31 file is called
            working_directory = os.getcwd()

            # where 1.png etc are saved
            tmp_dir = ArchivingContext.get_tmp_dir()
            command = ["pipenv", "run", "xvfb-run", "python3", "c31playwright_proxy_fire_env.py", url, tmp_dir]
            
            # Use subprocess.run to execute the command with the specified working directory
            sub_result = subprocess.run(command, cwd=working_directory, capture_output=True, text=True)

            # Print the output and error (if any)
            logger.debug(f"Playwright Output: {sub_result.stdout}")

            stderr_output = sub_result.stderr.strip()  # Remove leading and trailing whitespace

            # Check if there is something in the stderr_output
            if stderr_output:
                logger.error(f"Playwright Error: {stderr_output}")
            else:
                logger.debug("No playwright stderr output.")

            # make sure file is saved as 1.png  in the temp directory
            # filename = '1.png'

            contents = os.listdir(tmp_dir)

            png_files = [file for file in contents if file.endswith('.png')]

            def extract_number(file_name):
                # Extract the number from the filename
                return int(file_name.split('.')[0])

            # Make sure files are sorted by filename but I want 1,2,3,4 etc...
            png_files.sort(key=extract_number)

            logger.debug(f'contents of temp directory sorted: {contents}')

            for file_name in png_files: 
                # Process the PNG file
                foo = tmp_dir + '/' + file_name

                # why is file_name being stored in the root and not in dmtest004/1.png
                # don't know how to get it to save as 1.png as it just saves in the root on s3.
                new_media = Media(filename=foo)
                # id is useful as that is the orig filename
                result.add_media(new_media, id=file_name)


        if self.end_means_success: result.success("yt-dlp")
        else: result.status = "yt-dlp"
        return result
