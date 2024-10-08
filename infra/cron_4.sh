#!/bin/bash

# run cron_fb.sh every minute as user dave

cd /home/dave/auto-archiver
# cd /mnt/c/dev/v6-auto-archiver

PATH=/usr/local/bin:$PATH

run-one pipenv run python -m src.auto_archiver --config secrets/orchestration-pluro-demo.yaml                

