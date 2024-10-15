#!/bin/sh

# Script to configure production server
# 1.cir-auto-archiver - 
# 2.osr4rightstools. poll-for-file - don't run the cron job. Wire up systemctl infra/python-poll-service

# run ./infra.azcli from bash to create the VM
# that script will call this file
# if [ $# -eq 0 ]
#   then
#     echo "Deploying the PROXMOX - should ssh in first, then run"
#     echo "git clone https://github.com/djhmateer/auto-archiver ;  sudo chmod +x ~/auto-archiver/infra/server-build.sh ; ./auto-archiver/infra/server-build.sh"
# 	else


    echo "Deploying to Azure - infra.azcli should call this script using az vm run-command invoke with an argument. "
    echo "It copies the script to the VM from the local machine"
		cd /home/dave
		git clone -b v6-test --single-branch https://github.com/djhmateer/auto-archiver
    mkdir /home/dave/auto-archiver/secrets
    sudo chown -R dave /home/dave/auto-archiver

# to stop the pink pop up (may be okay when no terminal attached, but useful if doing all these commands manually)

# HERE****
# https://askubuntu.com/a/1421221
# sudo sed -i 's/#$nrconf{restart} = '"'"'i'"'"';/$nrconf{restart} = '"'"'a'"'"';/g' /etc/needrestart/needrestart.conf

## ODBC for MSSQL (pyodbc installed via pipenv)
# https://learn.microsoft.com/en-us/sql/connect/odbc/linux-mac/installing-the-microsoft-odbc-driver-for-sql-server?view=sql-server-ver16&tabs=ubuntu18-install%2Calpine17-install%2Cdebian8-install%2Credhat7-13-install%2Crhel7-offline


# add ms prod repo
# https://learn.microsoft.com/en-us/windows-server/administration/linux-package-repository-for-microsoft-software

curl -sSL https://packages.microsoft.com/keys/microsoft.asc | sudo tee /etc/apt/trusted.gpg.d/microsoft.asc
sudo apt-add-repository --yes https://packages.microsoft.com/ubuntu/22.04/prod
sudo apt-get update

# add odbc
sudo ACCEPT_EULA=Y apt-get install -y msodbcsql18

# sudo su
# curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add -

# curl https://packages.microsoft.com/config/ubuntu/$(lsb_release -rs)/prod.list > /etc/apt/sources.list.d/mssql-release.list

# exit
# sudo apt-get update
# sudo ACCEPT_EULA=Y apt-get install -y msodbcsql18


## Python
sudo apt update -y
sudo apt upgrade -y

# sudo add-apt-repository ppa:deadsnakes/ppa -y

# sudo apt update -y

# 3.9.12
# sudo apt install python3.9 -y
# sudo apt install python3.10 -y

# 3.10.6 already installed in Ubun 22.04

# need this for pip upgrade to work
export PATH=/home/dave/.local/bin:$PATH

sudo apt install python3-pip -y

# update pip to 23.3.1
pip install --upgrade pip

# We are calling pipenv from cron so need to install this way
# https://stackoverflow.com/questions/46391721/pipenv-command-not-found
# pip install --user pipenv

# installing this to avoid error in pipenv install below
# **NOT FULLY TESTED YET
sudo apt install python3-testresources -y

sudo -H pip install -U pipenv

cd auto-archiver

# get all the pip packages using pipenv
# pipenv install
sudo -H -u dave pipenv install

# FFMpeg
# 4.4.1
sudo add-apt-repository ppa:savoury1/ffmpeg4 -y
sudo apt update -y
sudo apt upgrade -y
sudo apt install ffmpeg -y

## Firefox
# for Ubuntu 22.04 this will come up with an error (**17th Nov 23 - seems to work now)
# failed to get new webdriver, possibly due to insufficient system resources or timeout settings: Message: Failed to read marionette port
# https://stackoverflow.com/questions/72374955/failed-to-read-marionette-port-when-running-selenium-geckodriver-firefox-a
# sudo apt install firefox -y

# to solve use these commands
# https://www.omgubuntu.co.uk/2022/04/how-to-install-firefox-deb-apt-ubuntu-22-04

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

# got issue with telethon archiver
# Please enter your phone (or bot token):
# then failing after that (even aftrer manually giving access)

# RUN MANUALLY
# cd ~/auto-archiver
# pipenv run python auto_archive.py --sheet "Test Hashing"

## CRON RUN EVERY MINUTE

# so the cron job can execute the shell script (running as user dave)
sudo chmod +x /home/dave/auto-archiver/infra/cron.sh
sudo chmod +x /home/dave/auto-archiver/infra/cron_fb.sh
sudo chmod +x /home/dave/auto-archiver/infra/cron_pluro.sh


# install fonts eg burmese, chinese for rendering in selenium firefox
# https://stackoverflow.com/questions/72015245/firefox-unicode-boxes-in-selenium-screenshot-instead-of-characters/72015719#72015719
sudo apt install fonts-noto -y

sudo apt install libimage-exiftool-perl -y

# don't want service to run until a reboot
# otherwise problems with Gecko driver
sudo service cron stop

##
# DM TODO UNCOMMENT!!!!!!!!!!!!!!!!!!!!!!!!
##
#
# runs the script every minute
# notice put in a # to disable so will have to manually start it.
cat <<EOT >> run-auto-archive
*/2 * * * * dave /home/dave/auto-archiver/infra/cron.sh
EOT


sudo mv run-auto-archive /etc/cron.d

sudo chown root /etc/cron.d/run-auto-archive
sudo chmod 600 /etc/cron.d/run-auto-archive

## DM JULY comment back in
## Comment out for FB or Pluro
##
## don't need these bits for main aa
sudo reboot now




##
## PLURO from here down!!!!
##

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

