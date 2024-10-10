from loguru import logger
import time, requests

from . import Enricher
from ..archivers import Archiver
from ..utils import UrlUtil
from ..core import Metadata

class WaybackArchiverEnricher(Enricher, Archiver):
    """
    Submits the current URL to the webarchive and returns a job_id or completed archive.

    The Wayback machine will rate-limit IP heavy usage. 
    """
    name = "wayback_archiver_enricher"

    def __init__(self, config: dict) -> None:
        # without this STEP.__init__ is not called
        super().__init__(config)
        assert type(self.secret) == str and len(self.secret) > 0, "please provide a value for the wayback_enricher API key"
        assert type(self.secret) == str and len(self.secret) > 0, "please provide a value for the wayback_enricher API secret"

    @staticmethod
    def configs() -> dict:
        return {
            "timeout": {"default": 15, "help": "seconds to wait for successful archive confirmation from wayback, if more than this passes the result contains the job_id so the status can later be checked manually."},
            "if_not_archived_within": {"default": None, "help": "only tell wayback to archive if no archive is available before the number of seconds specified, use None to ignore this option. For more information: https://docs.google.com/document/d/1Nsv52MvSjbLb2PCpHlat0gkzw0EvtSgpKHu4mk0MnrA"},
            "key": {"default": None, "help": "wayback API key. to get credentials visit https://archive.org/account/s3.php"},
            "secret": {"default": None, "help": "wayback API secret. to get credentials visit https://archive.org/account/s3.php"},
            "proxy_http": {"default": None, "help": "http proxy to use for wayback requests, eg http://proxy-user:password@proxy-ip:port"},
            "proxy_https": {"default": None, "help": "https proxy to use for wayback requests, eg https://proxy-user:password@proxy-ip:port"},
        }

    def download(self, item: Metadata) -> Metadata:
        # this new Metadata object is required to avoid duplication
        result = Metadata()
        result.merge(item)
        if self.enrich(result):
            return result.success("wayback")

    def enrich(self, to_enrich: Metadata) -> bool:
        proxies = {}
        if self.proxy_http: proxies["http"] = self.proxy_http
        if self.proxy_https: proxies["https"] = self.proxy_https

        # DM 2nd OCt 24 - adding new column in spreadseheet, and add to bottom on html metadata the wayback status
        wayback_status_from_enricher = ""
        url = to_enrich.get_url()

        if UrlUtil.is_auth_wall(url):
            message = f"[SKIP] WAYBACK since url is behind AUTH WALL: {url=}"
            logger.debug(message)
            wayback_status_from_enricher = message
            to_enrich.set("wayback_status_from_enricher", wayback_status_from_enricher)
            return

        logger.debug(f"calling wayback for {url=}")

        if to_enrich.get("wayback"):
            message = f"[SKIP] WAYBACK since already enriched: {to_enrich.get('wayback')}"
            logger.info(message)
            wayback_status_from_enricher = message
            to_enrich.set("wayback_status_from_enricher", wayback_status_from_enricher)
            return True

        ia_headers = {
            "Accept": "application/json",
            "Authorization": f"LOW {self.key}:{self.secret}"
        }
        post_data = {'url': url}
        if self.if_not_archived_within:
            post_data["if_not_archived_within"] = self.if_not_archived_within
        # see https://docs.google.com/document/d/1Nsv52MvSjbLb2PCpHlat0gkzw0EvtSgpKHu4mk0MnrA for more options
        # get Max retries exceeded with url: when too much

        try_again = True
        i = 1
        while try_again:
            try:
                r = requests.post('https://web.archive.org/save/', headers=ia_headers, data=post_data, proxies=proxies,  timeout=30)
                try_again = False
            except Exception as e:
                if i == 2:
                    message = f"couldnt contact wayback after {i} tries"
                    logger.info(message)
                    wayback_status_from_enricher = message
                    to_enrich.set("wayback_status_from_enricher", wayback_status_from_enricher)
                    return False
                else:
                    logger.debug(f"wayback post error trying again {e}")        
                    time.sleep(30)
                    i = i + 1

        if r.status_code != 200:
            message = f"Internet archive failed with status of {r.status_code}: {r.json()}"
            logger.error(message)
            to_enrich.set("wayback", message)
            wayback_status_from_enricher = message 
            to_enrich.set("wayback_status_from_enricher", wayback_status_from_enricher)
            return False

        # check job status
        job_id = r.json().get('job_id')
        if not job_id:
            message = f"Wayback failed with {r.json()}"
            logger.info(message)
            wayback_status_from_enricher = message

            # ******TODO - fix facebook logic here **************

            # Only seen on 1st Feb 24.
            # Response from wayback: This host has been already captured 50,093.0 times today. Please try again tomorrow.
            
            # 19th Apr - wayback throwing job failed error.. so lets just force all facebook links to succeed as the fb archiver will pick them up.
            # if 'This host has been already captured' in r.text:
            if 'facebook.com' in url:
                logger.debug("Swallowing error so that fb archiver picks up properly")
                # swallow the error (wayback: success will show) so that
                # the fb archiver will pickup properly 
                to_enrich.set("wayback_status_from_enricher", wayback_status_from_enricher)
                return True
            to_enrich.set("wayback_status_from_enricher", wayback_status_from_enricher)
            return False

        # waits at most timeout seconds until job is completed, otherwise only enriches the job_id information
        start_time = time.time()
        wayback_url = False
        attempt = 1
        keep_going = True
        # while not wayback_url and time.time() - start_time <= self.timeout:
        while keep_going:
            # if time.time() - start_time <= self.timeout:
            #     logger.debug(f"Timeout reached")
            #     break
            if attempt > 3:
                messageb = f"Wayback get status failed after 3 attempts - last attempt {r_status.json()}"
                logger.info(messageb)
                to_enrich.set("wayback_status_from_enricher", messageb)
                keep_going = False

            try:
                logger.debug(f"GETting status for {job_id=} on {url=} ({attempt=})")
                r_status = requests.get(f'https://web.archive.org/save/status/{job_id}', headers=ia_headers, proxies=proxies)
                r_json = r_status.json()

                # happy path
                if r_status.status_code == 200 and r_json['status'] == 'success':
                    logger.success(f"Wayback get success for {r_json['original_url']} at {r_json['timestamp']}")
                    wayback_url = f"https://web.archive.org/web/{r_json['timestamp']}/{r_json['original_url']}"
                    wayback_status_from_enricher = "success"
                    keep_going = False

                # pending so try again
                elif r_json['status'] == 'pending':
                    message = f"Wayback get is pending {r_json}"
                    logger.debug(message)
                    wayback_status_from_enricher = message
                    to_enrich.set("wayback_status_from_enricher", wayback_status_from_enricher)


                # non 200 on the get status - try again
                elif r_status.status_code != 200:
                    message = f"Wayback get failed with non 200 {r_json}"
                    logger.info(message)
                    wayback_status_from_enricher = message
                    to_enrich.set("wayback_status_from_enricher", wayback_status_from_enricher)

                # not happy path and not pending so try again
                elif r_json['status'] != 'pending':
                    message = f"Non pending status {r_json}"
                    logger.info(message)
                    wayback_status_from_enricher = message
                    to_enrich.set("wayback_status_from_enricher", message)

            except Exception as e:
                message = f"Error getting wayback job status for {url=} due to: {e}"
                logger.debug(message)
                wayback_status_from_enricher = message
                to_enrich.set("wayback_status_from_enricher", message)
                keep_going = False

            if keep_going:
                attempt += 1
                time.sleep(3)  # TODO: can be improved with exponential backoff

        if wayback_url:
            to_enrich.set("wayback", wayback_url)
        else:
            to_enrich.set("wayback", {"job_id": job_id, "check_status": f'https://web.archive.org/save/status/{job_id}'})
        to_enrich.set("check wayback", f"https://web.archive.org/web/*/{url}")
        to_enrich.set("wayback_status_from_enricher", wayback_status_from_enricher)
        return True
