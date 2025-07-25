#!/bin/bash

# called from /etc/cron.d/run-auto-archive
# notice this script depends on the logs/1trace.log file being present to know if another instance of script is running

cd /home/dave/auto-archiver
# PATH=/usr/local/bin:$PATH

# so poetry can be used
export PATH="$HOME/.local/bin:$PATH"

# only 1 instance of this will run if job lasts longer than 1 minute
# https://askubuntu.com/a/915731/677298
if [ $(pgrep -c "${0##*/}") -gt 1 ]; then
     now_human=$(date)
     # printf "\ndate: $now_human \n" >> /home/dave/log.txt 2>&1
     # get last modified date of log file
     # y is human readable, Y is unix epoch
     last_modified_time=$(stat -c %Y logs/1trace.log)
     #echo "last_modified: $last_modified_time" >> /home/dave/time_log.txt 2>&1

     now=$(date +%s)
     #echo "now: $now" >> /home/dave/time_log.txt 2>&1

     difference=$(($now-last_modified_time))
     # echo "difference between 1trace.log file last modified and now: $difference seconds" >> /home/dave/log.txt 2>&1

     # NOTE HAVE DISABLED THE KILL FOR NOW
     TIMETOWAIT=3600
     if (($difference > $TIMETOWAIT)); then
          # printf "\ndate: $now_human \n" >> /home/dave/kill_log.txt 2>&1
          # echo "time diff greater then $TIMETOWAIT seconds - kill the process as cron.sh has produced no stdout!" >> /home/dave/log.txt 2>&1
          # echo "time diff greater then $TIMETOWAIT seconds - kill the process as cron.sh has produced no stdout!" >> /home/dave/kill_log.txt 2>&1
          # # pid=$(pgrep -f auto_archive_fb)
          # echo "pid is $pid" >> /home/dave/log.txt 2>&1
          # # kill -9 $pid
          # echo "killed" >> /home/dave/log.txt 2>&1
          # echo "killed" >> /home/dave/kill_log.txt 2>&1
          # # as there are firefox processes which need to be killed
          # sudo reboot
          echo "problem - probably a stuck process"
     else
          # echo "time diff less then $TIMETOWAIT - normal control flow when the archiver is running" >> /home/dave/log.txt 2>&1
          echo "normal control flow when archiver is running"
     fi

     # echo "Another instance of the script is running. Aborting this run of cron.sh " >> /home/dave/log.txt 2>&1
     echo "Another instance of the script is running. Aborting this run of cron.sh "
     exit
fi

TIME=5

cd /home/dave/auto-archiver

# poetry run python src/auto_archiver --config secrets/orchestration-aa-demo-main.yaml

# poetry run python src/auto_archiver --config secrets/orchestration-auto-archiver-features.yaml

poetry run python src/auto_archiver --config secrets/orchestration-glan-sheet2.yaml
sleep $TIME


