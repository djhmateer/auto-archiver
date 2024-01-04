#!/bin/bash

# to debug this go to the project in ~/cir-deduplication

# this script is called from cron_fb.sh

# sudo service cron stop

export PATH="/home/dave/.pyenv/bin:$PATH"

eval "$(pyenv init -)"

cd /home/dave/cir-deduplication

pyenv activate cir_deduplication

# it logs to /home/dave/cir-deduplication/
# cir-deduplication.log
python /home/dave/cir-deduplication/tag_duplicates.py --single-sheet --worksheet-name Sheet1 --header-row 1 --case-id-col Entry\ Number --url-col Link --status-col Archive\ status --perceptual-hash-col Perceptual\ Hashes > /home/dave/cir-deduplication-shell.log 2>&1


