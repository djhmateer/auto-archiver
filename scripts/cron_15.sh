#!/bin/bash
# called every minute from /etc/cron.d/run-auto-archive

cd /home/dave/auto-archiver

export PATH="$HOME/.local/bin:$PATH" # poetry
export PATH="$HOME/.deno/bin:$PATH"  # deno, needed by yt-dlp

# only one instance at a time - a run can last much longer than a minute
# counts processes named after this script (so start it as ./cron_15.sh, not bash cron_15.sh)
# https://askubuntu.com/a/915731/677298
if [ "$(pgrep -c "${0##*/}")" -gt 1 ]; then
     echo "Another instance of the script is running. Aborting this run of cron_15.sh"
     exit
fi

# DM 2nd Oct 26 - daily reboot at the first idle minute after REBOOT_AT (UK time), at most once a day.
# Safe here: the pgrep check above means no other instance is archiving, and this one hasn't started.
REBOOT_AT="06:30"
reboot_target=$(TZ=Europe/London date -d "today $REBOOT_AT" +%s)
booted_at=$(awk '/^btime/ {print $2}' /proc/stat) # epoch, so no timezone ambiguity
if (( $(date +%s) >= reboot_target && booted_at < reboot_target )); then
     echo "$(date) daily reboot (up since $(date -d @$booted_at))" >> /home/dave/auto-archiver/logs/reboot.log
     # -n: fail rather than wait for a password; needs a NOPASSWD sudoers rule for /sbin/reboot
     if sudo -n /sbin/reboot; then
          exit
     fi
     echo "$(date) daily reboot FAILED (sudo) - archiving instead" >> /home/dave/auto-archiver/logs/reboot.log
fi

TIME=5

cd /home/dave/auto-archiver

# poetry run python src/auto_archiver --config secrets/orchestration-aa-demo-main.yaml

# poetry run python src/auto_archiver --config secrets/orchestration-auto-archiver-features.yaml

# poetry run python src/auto_archiver --config secrets/orchestration-glan-sheet2.yaml

# poetry run python src/auto_archiver --config secrets/orchestration-glan-sheet1.yaml

# poetry run python src/auto_archiver --config secrets/orchestration-glan-sheet3.yaml

poetry run python src/auto_archiver --config secrets/orchestration-glan-sheet4.yaml
sleep $TIME


