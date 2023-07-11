#!/bin/bash

cd /home/dave/auto-archiver
PATH=/usr/local/bin:$PATH

# only 1 instance of this will run if job lasts longer than 1 minute
# https://askubuntu.com/a/915731/677298
if [ $(pgrep -c "${0##*/}") -gt 1 ]; then

     echo "Another instance of the script is running. Aborting this run of tweet.sh " >> /home/dave/log.txt 2>&1
     exit
fi

# query db to see if anything needing tweeted
pipenv run python tweet.py
