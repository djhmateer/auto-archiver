import os
import ffmpeg
import datetime
import shutil
from dataclasses import dataclass
from abc import ABC, abstractmethod
from urllib.parse import urlparse
import hashlib
import time
import requests
from loguru import logger
from selenium.common.exceptions import TimeoutException

from storages import Storage
from utils import mkdir_if_not_exists


@dataclass
class ArchiveResult:
    status: str
    cdn_url: str = None
    thumbnail: str = None
    thumbnail_index: str = None
    duration: float = None
    title: str = None
    timestamp: datetime.datetime = None
    screenshot: str = None
    hash: str = None


class Archiver(ABC):
    name = "default"

    def __init__(self, storage: Storage, driver):
        self.storage = storage
        self.driver = driver

    def __str__(self):
        return self.__class__.__name__

    @abstractmethod
    def download(self, url, check_if_exists=False): pass

    def get_netloc(self, url):
        return urlparse(url).netloc

    def get_html_key(self, url):
        return self.get_key(urlparse(url).path.replace("/", "_") + ".html")

    def generate_media_page_html(self, url, urls_info: dict, object, thumbnail=None):
        page = f'''<html><head><title>{url}</title></head>
            <body>
            <h2>Archived media from {self.name}</h2>
            <h3><a href="{url}">{url}</a></h3><ul>'''

        for url_info in urls_info:
            page += f'''<li><a href="{url_info['cdn_url']}">{url_info['key']}</a>: {url_info['hash']}</li>'''

        # TODO/ISSUE: character encoding is incorrect for Cyrillic, produces garbled text
        page += f"</ul><h2>{self.name} object data:</h2><code>{object}</code>"
        page += f"</body></html>"

        page_key = self.get_key(urlparse(url).path.replace("/", "_") + ".html")
        page_filename = 'tmp/' + page_key
        page_cdn = self.storage.get_cdn_url(page_key)

        with open(page_filename, "w") as f:
            f.write(page)

        page_hash = self.get_hash(page_filename)

        self.storage.upload(page_filename, page_key, extra_args={
            'ACL': 'public-read', 'ContentType': 'text/html'})
        return (page_cdn, page_hash, thumbnail)

    def generate_media_page(self, urls, url, object):
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/81.0.4044.138 Safari/537.36'
        }

        thumbnail = None
        uploaded_media = []
        for media_url in urls:
            path = urlparse(media_url).path
            key = self.get_key(path.replace("/", "_"))
            if '.' not in path:
                key += '.jpg'

            filename = 'tmp/' + key

            d = requests.get(media_url, headers=headers)
            with open(filename, 'wb') as f:
                f.write(d.content)

            self.storage.upload(filename, key)
            hash = self.get_hash(filename)
            cdn_url = self.storage.get_cdn_url(key)

            if thumbnail is None:
                thumbnail = cdn_url
            uploaded_media.append({'cdn_url': cdn_url, 'key': key, 'hash': hash})

        return self.generate_media_page_html(url, uploaded_media, object, thumbnail=thumbnail)
    def get_key(self, filename):
        """
        returns a key in the format "[archiverName]_[filename]" includes extension
        """
        tail = os.path.split(filename)[1]  # returns filename.ext from full path
        _id, extension = os.path.splitext(tail)  # returns [filename, .ext]
        if 'unknown_video' in _id:
            _id = _id.replace('unknown_video', 'jpg')

        # long filenames can cause problems, so trim them if necessary
        if len(_id) > 128:
            _id = _id[-128:]

        return f'{self.name}_{_id}{extension}'

    def get_hash(self, filename):
        f = open(filename, "rb")
        bytes = f.read()  # read entire file as bytes
        hash = hashlib.sha256(bytes)
        f.close()
        return hash.hexdigest()

    def get_screenshot(self, url):
        key = self.get_key(urlparse(url).path.replace(
            "/", "_") + datetime.datetime.utcnow().isoformat().replace(" ", "_") + ".png")
        filename = 'tmp/' + key

        try:
            self.driver.get(url)
            time.sleep(6)
        except TimeoutException:
            logger.info("TimeoutException loading page for screenshot")

        self.driver.save_screenshot(filename)
        self.storage.upload(filename, key, extra_args={
                            'ACL': 'public-read', 'ContentType': 'image/png'})
        return self.storage.get_cdn_url(key)

    def get_thumbnails(self, filename, key, duration=None):
        thumbnails_folder = filename.split('.')[0] + '/'
        key_folder = key.split('.')[0] + '/'

        mkdir_if_not_exists(thumbnails_folder)

        fps = 0.5
        if duration is not None:
            duration = float(duration)

            if duration < 60:
                fps = 10.0 / duration
            elif duration < 120:
                fps = 20.0 / duration
            else:
                fps = 40.0 / duration

        stream = ffmpeg.input(filename)
        stream = ffmpeg.filter(stream, 'fps', fps=fps).filter('scale', 512, -1)
        stream.output(thumbnails_folder + 'out%d.jpg').run()

        thumbnails = os.listdir(thumbnails_folder)
        cdn_urls = []
        for fname in thumbnails:
            if fname[-3:] == 'jpg':
                thumbnail_filename = thumbnails_folder + fname
                key = key_folder + fname

                cdn_url = self.storage.get_cdn_url(key)

                self.storage.upload(thumbnail_filename, key)

                cdn_urls.append(cdn_url)

        if len(cdn_urls) == 0:
            return ('None', 'None')

        key_thumb = cdn_urls[int(len(cdn_urls) * 0.1)]

        index_page = f'''<html><head><title>{filename}</title></head>
            <body>'''

        for t in cdn_urls:
            index_page += f'<img src="{t}" />'

        index_page += f"</body></html>"
        index_fname = thumbnails_folder + 'index.html'

        with open(index_fname, 'w') as f:
            f.write(index_page)

        thumb_index = key_folder + 'index.html'

        self.storage.upload(index_fname, thumb_index, extra_args={
                            'ACL': 'public-read', 'ContentType': 'text/html'})
        shutil.rmtree(thumbnails_folder)

        thumb_index_cdn_url = self.storage.get_cdn_url(thumb_index)

        return (key_thumb, thumb_index_cdn_url)
