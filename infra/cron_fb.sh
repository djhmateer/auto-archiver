#!/bin/bash

# chmod +x cron.sh

# run cron_fb.sh every minute as user dave
# cd /etc/cron.d
# sudo vim run-auto-archive

# * * * * * dave /home/dave/auto-archiver/infra/cron_fb.sh

cd /home/dave/auto-archiver
PATH=/usr/local/bin:$PATH

# only used in deployment
# FILE=/home/dave/auto-archiver/anon.session
# if test -f "$FILE"; then
#     echo "$FILE exists."
# else
#      echo "secrets not all there yet, waiting for next cron run in 1 minute" >> /home/dave/log.txt 2>&1
#      exit
# fi

# only 1 instance of this will run if job lasts longer than 1 minute
# https://askubuntu.com/a/915731/677298
if [ $(pgrep -c "${0##*/}") -gt 1 ]; then
     now_human=$(date)
     printf "\ndate: $now_human \n" >> /home/dave/log.txt 2>&1
     # get last modified date of log file
     # y is human readable, Y is unix epoch
     last_modified_time=$(stat -c %Y logs/1trace.log)
     #echo "last_modified: $last_modified_time" >> /home/dave/time_log.txt 2>&1

     now=$(date +%s)
     #echo "now: $now" >> /home/dave/time_log.txt 2>&1

     difference=$(($now-last_modified_time))
     echo "difference between 1trace.log file last modified and now: $difference seconds" >> /home/dave/log.txt 2>&1


     TIMETOWAIT=3600
     if (($difference > $TIMETOWAIT)); then
          printf "\ndate: $now_human \n" >> /home/dave/kill_log.txt 2>&1
          echo "time diff greater then $TIMETOWAIT seconds - kill the process as cron_fb.sh has produced no stdout!" >> /home/dave/log.txt 2>&1
          echo "time diff greater then $TIMETOWAIT seconds - kill the process as cron_fb.sh has produced no stdout!" >> /home/dave/kill_log.txt 2>&1
          pid=$(pgrep -f auto_archive_fb)
          echo "pid is $pid" >> /home/dave/log.txt 2>&1
          kill -9 $pid
          echo "killed" >> /home/dave/log.txt 2>&1
          echo "killed" >> /home/dave/kill_log.txt 2>&1
     else
          echo "time diff less then $TIMETOWAIT - normal control flow when the FB archiver is running" >> /home/dave/log.txt 2>&1
     fi

     echo "Another instance of the script is running. Aborting this run of cron_fb.sh " >> /home/dave/log.txt 2>&1
     exit
fi


# test sheets, saving to google drives with davemateer@gmail.com token
#pipenv run python auto_archive_fb.py --config config-test-hashing.yaml
# pipenv run python auto_archive.py --config config-test-hashing-2.yaml

TIME=0
pipenv run python auto_archive_fb.py --config config-aw.yaml
sleep $TIME

# DM turn these on for FB
pipenv run python auto_archive_fb.py --config config-mw.yaml
sleep $TIME
#pipenv run python auto_archive_fb.py --config config-cmu-demo.yaml

pipenv run python auto_archive_fb.py --config config-eor.yaml
sleep $TIME

pipenv run python auto_archive_fb.py --config config-ukraine-environment.yaml
sleep $TIME

#pipenv run python auto_archive_fb.py --config config-cir-projects.yaml

#pipenv run python auto_archive_fb.py --config config-osr-demo.yaml

#pipenv run python auto_archive.py --config config-airwars.yaml

#pipenv run python auto_archive.py --config config-france24.yaml

#pipenv run python auto_archive.py --config config-un-demo.yaml

#pipenv run python auto_archive.py --config config-wagner-demo.yaml

#pipenv run python auto_archive.py --config config-amnesty-demo.yaml

## pipenv run python auto_archive.py --config config-fb-test.yaml



pipenv run python auto_archive_fb.py --config config-aa-demo-main.yaml
sleep $TIME

pipenv run python auto_archive_fb.py --config config-rmit-demo.yaml
sleep $TIME

pipenv run python auto_archive_fb.py --config config-bellingcat-demo.yaml
## cron job output is in 
## vim /home/dave/log.txt

# ps -ef | grep auto
# kill -9 to stop job

# to stop cron job comment out in /etc/cron.d
# then reload
# sudo service cron reload