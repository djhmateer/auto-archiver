from loguru import logger
from abc import ABC, abstractmethod


class Storage(ABC):
    @abstractmethod
    def __init__(self, config): pass

    @abstractmethod
    def get_cdn_url(self, key): pass

    @abstractmethod
    def exists(self, key): pass

    @abstractmethod
    def uploadf(self, file, key, **kwargs): pass

    def upload(self, filename: str, key: str, **kwargs):
        logger.debug(f'[{self.__class__.__name__}] uploading file {filename} with key {key}')
        # S3 requires an open file, GD only the filename
        storage = type(self).__name__
        if storage == "GDStorage":
            self.uploadf(filename, key, **kwargs)
        elif storage == "S3Storage":
            with open(filename, 'rb') as f:
                self.uploadf(f, key, **kwargs)
        else:
            raise ValueError('Cant get storage thrown from base_storage.py')