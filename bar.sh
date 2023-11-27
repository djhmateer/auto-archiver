#!/bin/bash


export PATH="/home/dave/.pyenv/bin:$PATH"

eval "$(pyenv init -)"

cd /home/dave/cir-deduplication

pyenv activate cir_deduplication


python /home/dave/cir-deduplication/tag_duplicates.py --single-sheet --worksheet-name Sheet1 --header-row 1 --case-id-col Entry\ Number --url-col Link --status-col Archive\ status --perceptual-hash-col Perceptual\ Hashes > /home/dave/foo.log 2>&1


