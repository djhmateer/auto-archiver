#!/bin/bash
# called every minute from /etc/cron.d/run-auto-archive

cd /home/dave/auto-archiver

export PATH="$HOME/.local/bin:$PATH" # poetry
export PATH="$HOME/.deno/bin:$PATH"  # deno, needed by yt-dlp

# DM 2nd Oct 26 - daily reboot at the first idle minute after REBOOT_AT (UK time), at most once a day.
REBOOT_AT="05:30"
reboot_target=$(TZ=Europe/London date -d "today $REBOOT_AT" +%s)
booted_at=$(awk '/^btime/ {print $2}' /proc/stat) # epoch, so no timezone ambiguity
reboot_due=0
if (( $(date +%s) >= reboot_target && booted_at < reboot_target )); then
     reboot_due=1
fi

# only one instance at a time - a run can last much longer than a minute
# counts processes named after this script (so start it as ./cron_13.sh, not bash cron_13.sh)
# https://askubuntu.com/a/915731/677298
if [ "$(pgrep -c "${0##*/}")" -gt 1 ]; then
     # DM 23rd Oct 26 - a hung run is never idle, so it blocked the daily reboot (server 12 hung for 13h).
     # An hour past REBOOT_AT, reboot anyway. This kills the run - its row is left as "Archive in progress"
     if (( reboot_due && $(date +%s) >= reboot_target + 3600 )); then
          echo "$(date) daily reboot FORCED, a run was still going an hour after $REBOOT_AT (up since $(date -d @$booted_at))" >> /home/dave/auto-archiver/logs/reboot.log
          if sudo -n /sbin/reboot; then
               exit
          fi
          echo "$(date) daily reboot FAILED (sudo)" >> /home/dave/auto-archiver/logs/reboot.log
     fi
     echo "Another instance of the script is running. Aborting this run of cron_13.sh"
     exit
fi

# Safe here: the pgrep check above means no other instance is archiving, and this one hasn't started.
if (( reboot_due )); then
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

# poetry run python src/auto_archiver --config secrets/orchestration-cir-domain-cir-sahel.yaml
# sleep $TIME

# poetry run python src/auto_archiver --config secrets/orchestration-cir-domain-cir-sudan.yaml
# sleep $TIME

# poetry run python src/auto_archiver --config secrets/orchestration-cir-domain-aw.yaml
# sleep $TIME

poetry run python src/auto_archiver --config secrets/orchestration-cir-domain-mw.yaml
sleep $TIME

