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


# application log files are in ~/auto_archive/logs
# pipenv run python auto_archive.py --sheet "Test Hashing" >> /home/dave/log.txt 2>&1

# this will default to s3
# pipenv run python auto_archive.py --sheet "Test Hashing" 

# make sure the correct gd storage is selected
pipenv run python auto_archive.py --sheet "Test Hashing" --use-filenumber-as-directory --storage=gd

# make sure the correct gd storage is selected
# pipenv run python auto_archive.py --sheet "Kayleigh - test" --header=3 --use-filenumber-as-directory --storage=gd


## cron job output is in 
## vim /home/dave/log.txt

# ps -ef | grep auto
# kill -9 to stop job

# to stop cron job comment out in /etc/cron.d
# then reload
# sudo service cron reload