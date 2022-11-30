#!/bin/bash

# chmod +x cron.sh

# run cron.sh every minute as user dave
# cd /etc/cron.d
# sudo vim TestHashing

# * * * * * dave /home/dave/auto-archiver/infra/cron.sh

# on first build of a machine, check to make sure all secret environment files are there
# anon.session is the last to be copied
FILE=/home/dave/auto-archiver/anon.session
if test -f "$FILE"; then
    echo "$FILE exists."
else
     echo "secrets not all there yet, waiting for next cron run in 1 minute" >> /home/dave/log.txt 2>&1
     exit
fi

# so only 1 instance of this will run if job lasts longer than 1 minute
# https://askubuntu.com/a/915731/677298
if [ $(pgrep -c "${0##*/}") -gt 1 ]; then
     echo "Another instance of the script is running. Aborting." >> /home/dave/log.txt 2>&1
     exit
fi

cd /home/dave/auto-archiver
# do I need this?
PATH=/usr/local/bin:$PATH


# test sheets, saving to google drives with davemateer@gmail.com token
# pipenv run python auto_archive.py --config config-test-hashing.yaml
# pipenv run python auto_archive.py --config config-test-hashing-2.yaml


# DM turn these on for PROD
#pipenv run python auto_archive.py --config config-aw.yaml
#pipenv run python auto_archive.py --config config-mw.yaml
#pipenv run python auto_archive.py --config config-eor.yaml
#pipenv run python auto_archive.py --config config-ukraine-environment.yaml
#pipenv run python auto_archive.py --config config-cir-projects.yaml

#pipenv run python auto_archive.py --config config-osr-demo.yaml

#pipenv run python auto_archive.py --config config-airwars.yaml

#pipenv run python auto_archive.py --config config-france24.yaml

#pipenv run python auto_archive.py --config config-un-demo.yaml

#pipenv run python auto_archive.py --config config-wagner-demo.yaml

#pipenv run python auto_archive.py --config config-amnesty-demo.yaml

pipenv run python auto_archive.py --config config-fb-test.yaml






## cron job output is in 
## vim /home/dave/log.txt

# ps -ef | grep auto
# kill -9 to stop job

# to stop cron job comment out in /etc/cron.d
# then reload
# sudo service cron reload