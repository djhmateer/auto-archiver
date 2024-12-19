#!/bin/bash


TIME=5

# pipenv run python -m src.auto_archiver --config secrets/orchestration-aa-demo-main.yaml

# TEST - make sure that the FB profile is working
#pipenv run python -m src.auto_archiver --config secrets/orchestration-aa-demo-main-facebook.yaml
# sleep $TIME

# PROD
pipenv run python -m src.auto_archiver --config secrets/orchestration-cir-domain-eor-facebook.yaml
sleep $TIME

pipenv run python -m src.auto_archiver --config secrets/orchestration-cir-domain-cir-sahel-facebook.yaml
sleep $TIME

pipenv run python -m src.auto_archiver --config secrets/orchestration-cir-domain-cir-sudan-facebook.yaml
sleep $TIME

pipenv run python -m src.auto_archiver --config secrets/orchestration-cir-domain-aw-facebook.yaml
sleep $TIME

pipenv run python -m src.auto_archiver --config secrets/orchestration-cir-domain-eor-grc-facebook.yaml
sleep $TIME

pipenv run python -m src.auto_archiver --config secrets/orchestration-cir-domain-mw-facebook.yaml
sleep $TIME


pipenv run python -m src.auto_archiver --config secrets/orchestration-cir-projects-facebook.yaml
sleep $TIME




pipenv run python -m src.auto_archiver --config secrets/orchestration-glan.yaml                
sleep $TIME
pipenv run python -m src.auto_archiver --config secrets/orchestration-glan-facebook.yaml
sleep $TIME

# pipenv run python -m src.auto_archiver --config secrets/orchestration-dr-demo.yaml                
# sleep $TIME
# pipenv run python -m src.auto_archiver --config secrets/orchestration-dr-demo-facebook.yaml
# sleep $TIME


# pipenv run python -m src.auto_archiver --config secrets/orchestration-pluro-demo.yaml                
# sleep $TIME






## cron job output is in 
## vim /home/dave/log.txt

# ps -ef | grep auto
# kill -9 to stop job

# to stop cron job comment out in /etc/cron.d
# then reload
# sudo service cron reload