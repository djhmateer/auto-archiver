# Installation

```{toctree}
:maxdepth: 1

upgrading.md
```

There are 3 main ways to use the auto-archiver. We recommend the 'docker' method for most uses. This installs all the requirements in one command.

1. Easiest (recommended): [via docker](#installing-with-docker)
2. Local Install: [using pip](#installing-locally-with-pip)
3. Developer Install: [see the developer guidelines](../development/developer_guidelines)

## 1. Installing with Docker

[![dockeri.co](https://dockerico.blankenship.io/image/bellingcat/auto-archiver)](https://hub.docker.com/r/bellingcat/auto-archiver)

Docker works like a virtual machine running inside your computer, making installation simple. You'll need to first set up Docker, and then download the Auto Archiver 'image':


**a) Download and install docker**

Go to the [Docker website](https://docs.docker.com/get-docker/) and download right version for your operating system. 

**b) Pull the Auto Archiver docker image**

Open your command line terminal, and copy-paste / type:

```bash
docker pull bellingcat/auto-archiver
```

This will download the docker image, which may take a while.

That's it, all done! You're now ready to set up [your configuration file](configurations.md). Or, if you want to use the recommended defaults, then you can [run Auto Archiver immediately](setup.md#running-a-docker-install).

------------

## 2. Installing Locally with Pip

1. Make sure you have python 3.10 or higher installed
2. Install the package with your preferred package manager: `pip/pipenv/conda install auto-archiver` or `poetry add auto-archiver`
3. Test it's installed with `auto-archiver --help`
4. Install other local dependency requirements (for example `ffmpeg`, `firefox`)

After this, you're ready to set up your [your configuration file](configurations.md), or if you want to use the recommended defaults, then you can [run Auto Archiver immediately](setup.md#running-a-local-install).

### Installing Local Requirements

If using the local installation method, you will also need to install the following dependencies locally:

1.[ffmpeg](https://www.ffmpeg.org/) - for handling of downloaded videos
2. [firefox](https://www.mozilla.org/en-US/firefox/new/) and [geckodriver](https://github.com/mozilla/geckodriver/releases) on a path folder like `/usr/local/bin` - for taking webpage screenshots with the screenshot enricher
3. (optional) [fonts-noto](https://fonts.google.com/noto) to deal with multiple unicode characters during selenium/geckodriver's screenshots: `sudo apt install fonts-noto -y`.
4. [Browsertrix Crawler docker image](https://hub.docker.com/r/webrecorder/browsertrix-crawler) for the WACZ enricher/archiver

### Bash script for Ubuntu Server install

This acts as a handy guide on all requirements. This is built and tested on the 29th of May 2025 on Ubuntu Server 24.04.2 LTS (which is the current latest LTS)

```bash
#!/bin/sh

# I usually run steps manually as logged in with the user: dave
# which the application runs under which makes debugging easier

cd ~

# Clone only my latest branch
git clone -b v1-test --single-branch https://github.com/djhmateer/auto-archiver

mkdir ~/auto-archiver/secrets
sudo chown -R dave ~/auto-archiver

sudo apt update -y
sudo apt upgrade -y

## Python 3.12.3 comes with Ubuntu 24.04.2

# Poetry install 2.1.3 on 2nd June 25
curl -sSL https://install.python-poetry.org | python3 -

# had to restart shell here.. neither of below worked 
# source ~/.bashrc
# exec bash

cd auto-archiver

# C++ compiler so pdqhash will install next
sudo apt install build-essential python3-dev

poetry install

# FFMpeg
# 6.1.1-3ubuntu5 on 2nd June 25
sudo apt install ffmpeg -y

## Firefox
# 139.0+build2-0ubuntu0.24.04.1~mt1 on 2nd Jun 25
cd ~
sudo add-apt-repository ppa:mozillateam/ppa -y

echo '
Package: *
Pin: release o=LP-PPA-mozillateam
Pin-Priority: 1001
' | sudo tee /etc/apt/preferences.d/mozilla-firefox

echo 'Unattended-Upgrade::Allowed-Origins:: "LP-PPA-mozillateam:${distro_codename}";' | sudo tee /etc/apt/apt.conf.d/51unattended-upgrades-firefox

sudo apt install firefox -y

## Gecko driver
# check version numbers for new ones
# https://github.com/mozilla/geckodriver/releases/
cd ~
wget https://github.com/mozilla/geckodriver/releases/download/v0.35.0/geckodriver-v0.35.0-linux64.tar.gz
tar -xvzf geckodriver*
chmod +x geckodriver
sudo mv geckodriver /usr/local/bin/
rm geckodriver*

# Fonts
sudo apt install fonts-noto -y

# Docker
# Add Docker's official GPG key:
sudo apt-get update -y
sudo apt-get install ca-certificates curl -y
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

# Add the repository to Apt sources:
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update -y

sudo apt-get install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin -y

# add dave user to docker group 
sudo usermod -aG docker $USER

# restart shell
# TODO try: source ~/.bashrc
# exec bash

docker pull webrecorder/browsertrix-crawler:latest

# exif
sudo apt install libimage-exiftool-perl -y


## CRON run every minute
# the cron job running as user dave will execute the shell script
sudo chmod +x ~/auto-archiver/scripts/cron_1.sh

# don't want service to run until a reboot otherwise problems with Gecko driver
sudo service cron stop

# runs the script every minute
# notice put in a # to disable so will have to manually start it.
cat <<EOT >> run-auto-archive
#*/1 * * * * dave /home/dave/auto-archiver/scripts/cron_1.sh
EOT

sudo mv run-auto-archive /etc/cron.d
sudo chown root /etc/cron.d/run-auto-archive
sudo chmod 600 /etc/cron.d/run-auto-archive

# Helper alias 'c' to open the above file
echo "alias c='sudo vim /etc/cron.d/run-auto-archive'" >> ~/.bashrc


# secrets folder copy
# I run dev from:
# \\wsl.localhost\Ubuntu-24.04\home\dave\code\auto-archiver\secrets\

# orchestration.yaml - for aa config
# service_account - for google spreadsheet
# anon.session - for telethon so don't have to type in phone number
# vk_config.v2.json - so don't have to login to vk again
# profile.tar.gz - for wacz to have a logged in profile for facebook, x.com and instagram to get data


# Youtube - POT Tokens
# https://github.com/Brainicism/bgutil-ytdlp-pot-provider
docker run --name bgutil-provider --restart unless-stopped -d -p 4416:4416 brainicism/bgutil-ytdlp-pot-provider


# test run
cd ~/auto-archiver

poetry run python src/auto_archiver --config secrets/orchestration-aa-demo-main.yaml






## HERE

## OLD
sudo pip install pytest-playwright

# x virtual frame buffer
# for playwright (screenshotter) to run in headed mode
sudo apt install xvfb -y

sudo playwright install-deps

sudo apt-get install libvpx7 -y

TARGET_USER="dave"
sudo -i -u $TARGET_USER bash << EOF
playwright install 
EOF

#sudo apt-get install libgbm1

cat <<EOT >> run-auto-archive
*/2 * * * * dave /home/dave/auto-archiver/infra/cron_pluro.sh
EOT

sudo mv run-auto-archive /etc/cron.d

sudo chown root /etc/cron.d/run-auto-archive
sudo chmod 600 /etc/cron.d/run-auto-archive


sudo reboot now


## DM 16th Oct 2024
# am using playwright as a general screenshotter
# so need to install the dependencies for that

sudo pip install pytest-playwright

sudo apt install xvfb -y

# playwright install








##
## FB Archiver from here down!!!!
##

# specialised version of the archiver which runs on proxmox currently only
cat <<EOT >> fb-run-auto-archive
#* * * * * dave /home/dave/auto-archiver/infra/cron_fb.sh
EOT

# docker
# https://docs.docker.com/engine/install/ubuntu/
sudo apt-get update -y
sudo apt-get install ca-certificates curl gnupg lsb-release -y

sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update

sudo chmod a+r /etc/apt/keyrings/docker.gpg
sudo apt-get update


sudo apt-get install docker-ce docker-ce-cli containerd.io docker-compose-plugin -y

# docker as non sudo https://docs.docker.com/engine/install/linux-postinstall/#manage-docker-as-a-non-root-user
# cron runs as user dave
sudo usermod -aG docker dave

sudo pip install pytest-playwright

# x virtual frame buffer
# for playwright (screenshotter) to run in headed mode
sudo apt install xvfb -y

# **need to run playwright install to download chrome**
# **NOT TESTED**
##sudo playwright install-deps
#sudo apt-get install libgbm1


sudo reboot now




# MONITORING
# syslog in /var/log/syslog
# cron output is in /home/dave/log.txt

# sudo service cron restart


```



## Developer Install

[See the developer guidelines](../development/developer_guidelines)