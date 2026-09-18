<h1>This Fork</h1>

<!-- Main branch represents what I (should) have in production. Currently 1.1.6+dm.7. As of 3rd Nov 25 -->
Main branch represents what I (should) have in production. Currently 1.2.9+dm.8. As of 16th Sept 26

This is essentially upstream with added:

- Facebook crawling. 
- Overwriting allowed on spreadsheets cells
- Updates turned off (as may affect prod - have seen unusual behaviour). 
- WACZ image sizes to prevent small gifs etc.. 
- metadata.json hardcoded filename to stay the same so easier to automate further analysis. 
- Log files hardcoded to logs/1debug.log etc.. as I prefer this.
- mhtml_enricher module to save an mhtml snapshot of the page.
- screenshot_enricher/webdriver using Firefox instead of Chrome - Chrome antibot measures were affecting the FB cookie and causing flagging issues. Firefox screenshots have also been very good.
- screenshot_enricher runs OCR (pytesseract) on every Facebook screenshot to detect if the FB cookie/sock puppet session has been tripped
- VPN addition for Twitter/X so that adult content is allowed - off by default. 17th Sept 26 found that expressvpn not working to Sydney properly anymore (too many timeouts)


<h1 align="center">Auto Archiver</h1>

[![Documentation Status](https://readthedocs.org/projects/auto-archiver/badge/?version=latest)](https://auto-archiver.readthedocs.io/en/latest/?badge=latest)
[![PyPI version](https://badge.fury.io/py/auto-archiver.svg)](https://badge.fury.io/py/auto-archiver)
[![Docker Image Version (latest by date)](https://img.shields.io/docker/v/bellingcat/auto-archiver?sort=semver&logo=docker&color=#69F0AE)](https://hub.docker.com/r/bellingcat/auto-archiver)
[![Core Test Status](https://github.com/bellingcat/auto-archiver/workflows/Core%20Tests/badge.svg)](https://github.com/bellingcat/auto-archiver/actions/workflows/tests-core.yaml)
<!-- [![Download Test Status](https://github.com/bellingcat/auto-archiver/workflows/Download%20Tests/badge.svg)](https://github.com/bellingcat/auto-archiver/actions/workflows/tests-download.yaml) -->

<!-- ![Docker Pulls](https://img.shields.io/docker/pulls/bellingcat/auto-archiver) -->
<!-- [![PyPI download month](https://img.shields.io/pypi/dm/auto-archiver.svg)](https://pypi.python.org/pypi/auto-archiver/) -->


Auto Archiver is a Python tool to automatically archive content on the web in a secure and verifiable way. It takes URLs from different sources (e.g. a CSV file, Google Sheets, command line etc.) and archives the content of each one. It can archive social media posts, videos, images and webpages. Content can be enriched, then saved either locally or remotely (S3 bucket, Google Drive). The status of the archiving process can be appended to a CSV report, or if using Google Sheets – back to the original sheet.

<div class="hidden_rtd">
  
**[See the Auto Archiver documentation for more information.](https://auto-archiver.readthedocs.io/en/latest/)**

</div>

Read the [article about Auto Archiver on bellingcat.com](https://www.bellingcat.com/resources/2022/09/22/preserve-vital-online-content-with-bellingcats-auto-archiver-tool/).


## Installation

View the [Installation Guide](https://auto-archiver.readthedocs.io/en/latest/installation/installation.html) for full instructions

**Advanced:**

To get started quickly using Docker:

`docker pull bellingcat/auto-archiver && docker run -it --rm -v secrets:/app/secrets bellingcat/auto-archiver --config secrets/orchestration.yaml`

Or pip:

`pip install auto-archiver && auto-archiver --help`

## Contributing

We welcome contributions to the Auto Archiver project! See the [Contributing Guide](https://auto-archiver.readthedocs.io/en/latest/contributing.html) for how to get involved!

