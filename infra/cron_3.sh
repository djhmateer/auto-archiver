#!/bin/bash

# chmod +x cron.sh

cd /home/dave/auto-archiver

PATH=/usr/local/bin:$PATH

TIME=5


# TEST - make sure that the FB profile is working
#pipenv run python -m src.auto_archiver --config secrets/orchestration-aa-demo-main-facebook.yaml
# sleep $TIME

# pipenv run python -m src.auto_archiver --config secrets/orchestration-aa-demo-main.yaml


run-one pipenv run python -m src.auto_archiver --config secrets/orchestration-glan.yaml                
sleep $TIME

run-one pipenv run python -m src.auto_archiver --config secrets/orchestration-glan-facebook.yaml
sleep $TIME

run-one pipenv run python -m src.auto_archiver --config secrets/orchestration-glan-ytbm.yaml                
sleep $TIME

run-one pipenv run python -m src.auto_archiver --config secrets/orchestration-glan-ytbm-facebook.yaml
sleep $TIME



