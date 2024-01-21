#!/usr/bin/bash
#
# This file is part of the Pandora-box distribution
# Copyright (c) 2022 Didier Barzin.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

set -e # stop on error
set -x # echo on

#================================
# Install script for Pandora-Box
#================================

cd /home/$SUDO_USER

# remove need restart
apt remove -y needrestart

#---------------------
# Python
#---------------------
apt update && apt upgrade -y
apt install -y python-is-python3 python3-pip
apt install -y libssl-dev

#---------------------
# Peotry
#---------------------
su - $SUDO_USER -c "curl -sSL https://install.python-poetry.org | python3 -"
su - $SUDO_USER -c "poetry --version"

#---------------------
# REDIS
#---------------------
apt install -y build-essential tcl

if [ -d "redis" ];
    then
        cd redis
    else
        git clone https://github.com/redis/redis.git
        cd redis
        git checkout 6.2
fi
make
# Optionally, you can run the tests:
# make test
cd ..

chown -R $SUDO_USER redis

#---------------------
# Kvrocks
#---------------------
apt-get update
apt install -y git gcc g++ make cmake autoconf automake libtool python3 libssl-dev

if [ -d "kvrocks" ];
    then
        cd kvrocks
    else
        git clone --recursive https://github.com/apache/incubator-kvrocks.git kvrocks
        cd kvrocks
        git checkout 2.6
fi
./x.py build
cd ..

chown -R $SUDO_USER kvrocks

#---------------------
# Pandora
#---------------------
if [ ! -d "pandora" ];
    then
        git clone https://github.com/pandora-analysis/pandora.git
        chown -R $SUDO_USER pandora
fi

# fix broken packages
apt-get install --fix-broken -y

# install packages
apt install -y python3-dev  # for compiling things
apt install -y libpango-1.0-0 libharfbuzz0b libpangoft2-1.0-0  # For HTML -> PDF
apt install -y libreoffice-nogui # For Office -> PDF
apt install -y exiftool  # for extracting exif information
apt install -y unrar  # for extracting rar files
apt install -y libxml2-dev libxslt1-dev antiword unrtf poppler-utils pstotext tesseract-ocr flac ffmpeg lame libmad0 libsox-fmt-mp3 sox libjpeg-dev swig  # for textract
apt install -y libssl-dev  # seems required for yara-python
apt install -y libcairo2-dev  # Required by reportlab

apt install -y rsyslog cron # log logging

# autoremove old stuff
apt autoremove -y

# set .env
cd pandora
echo PANDORA_HOME="`pwd`" >> .env

su - $SUDO_USER -c "cd ~/pandora; poetry install"
su - $SUDO_USER -c "cd ~/pandora; cp config/generic.json.sample config/generic.json"

# Copy default config file
su - $SUDO_USER -c "cp ~/pandora/config/logging.json.sample ~/pandora/config/logging.json"

# install yara-python
su - $SUDO_USER -c "pip install yara-python"

# ClamAV
apt-get install -y hdparm clamav-daemon
# In order for the module to work, you need the signatures.
# Running the command "freshclam" will do it but if the script is already running
# (it is started by the systemd service clamav-freshclam)
# You might want to run the commands below:
systemctl stop clamav-freshclam.service  # Stop the service
freshclam  # Run the signatures update
systemctl start clamav-freshclam.service # Start the service so we keep getting the updates

service clamav-daemon start

# Comodo
wget https://download.comodo.com/cis/download/installs/linux/cav-linux_x64.deb
dpkg --ignore-depends=libssl0.9.8 -i cav-linux_x64.deb

wget http://cdn.download.comodo.com/av/updates58/sigs/bases/bases.cav -O /opt/COMODO/scanners/bases.cav

# Update Pandora
su - $SUDO_USER -c "cd ~/pandora; poetry run update --yes"

# Remove unused workers
su - $SUDO_USER -c "rm ~/pandora/pandora/workers/blocklists.*"
su - $SUDO_USER -c "rm ~/pandora/pandora/workers/hybridanalysis.*"
su - $SUDO_USER -c "rm ~/pandora/pandora/workers/joesandbox.*"
su - $SUDO_USER -c "rm ~/pandora/pandora/workers/lookyloo.*"
su - $SUDO_USER -c "rm ~/pandora/pandora/workers/malwarebazaar.*"
su - $SUDO_USER -c "rm ~/pandora/pandora/workers/mwdb.*"
su - $SUDO_USER -c "rm ~/pandora/pandora/workers/ole.*"
su - $SUDO_USER -c "rm ~/pandora/pandora/workers/preview.*"
su - $SUDO_USER -c "rm ~/pandora/pandora/workers/virustotal.*"
su - $SUDO_USER -c "rm ~/pandora/pandora/workers/xml*"
su - $SUDO_USER -c 'rm ~/pandora/pandora/workers/msodde*'
su - $SUDO_USER -c 'rm ~/pandora/pandora/workers/odf*'
su - $SUDO_USER -c 'rm ~/pandora/pandora/workers/qrcode*'

# Remove files from quarantine after 180 days
{ crontab -l -u $SUDO_USER; echo '0 * * * * find /var/quarantine/* -type f -mtime +180 -delete '; } | crontab -u $SUDO_USER -
{ crontab -l -u $SUDO_USER; echo '5 * * * * find /var/quarantine/* -type d -empty -mtime +180 -delete '; } | crontab -u $SUDO_USER -

# Remove old Pandora task files every hour
{ crontab -l -u $SUDO_USER; echo '30 * * * * find ~/pandora/tasks/* -type f -mtime +1 -delete '; } | crontab -u $SUDO_USER -
{ crontab -l -u $SUDO_USER; echo '35 * * * * find ~/pandora/tasks/* -type d -empty -mtime +1 -delete'; } | crontab -u $SUDO_USER -

# Poweroff at 20:00 (green energy)
echo '0 20 * * * /sbin/poweroff' >> /etc/crontab

#---------------------
# Pandora-box
#---------------------
cd /home/$SUDO_USER/pandora-box

# Python libraries
su - $SUDO_USER -c "pip install pypandora psutil pyudev"

# create /media/box folder
if [ ! -d "/media/box" ];
    then
        echo "Create /media/box folder."
        mkdir /media/box
    else
        echo "No /media/box folder needed."
fi

# Quarantine folder
mkdir -p /var/quarantine
chown $SUDO_USER /var/quarantine

# FIM, pmount, psmisc (for killall) and vim
apt --fix-broken install -y
apt install -y fim pmount psmisc vim

# Suppress all messages from the kernel (and its drivers) except panic messages from appearing on the console.
echo "kernel.printk = 3 4 1 3" | tee -a /etc/sysctl.conf
# Set Permanently ulimit -n / open files in ubuntu
echo "fs.file-max = 65535" | tee -a /etc/sysctl.conf

# allow write to /dev/fb0
usermod -a -G video $SUDO_USER

# allow read mouse input
usermod -a -G input $SUDO_USER

# allow read mouse input
usermod -a -G tty $SUDO_USER

# allow write to /var/log
usermod -a -G syslog $SUDO_USER

# logrotate
apt install logrotate
echo "/var/log/pandora-box.log {" > /etc/logrotate.d/pandora-box
echo "   rotate 12" >> /etc/logrotate.d/pandora-box
echo "   monthly" >> /etc/logrotate.d/pandora-box
echo "   compress" >> /etc/logrotate.d/pandora-box
echo "   missingok" >> /etc/logrotate.d/pandora-box
echo "   notifempty" >> /etc/logrotate.d/pandora-box
echo "}" >> /etc/logrotate.d/pandora-box

echo "/var/log/pandora_message.log {" > /etc/logrotate.d/pandora_message
echo "   rotate 12" >> /etc/logrotate.d/pandora_message
echo "   monthly" >> /etc/logrotate.d/pandora_message
echo "   compress" >> /etc/logrotate.d/pandora_message
echo "   missingok" >> /etc/logrotate.d/pandora_message
echo "   notifempty" >> /etc/logrotate.d/pandora_message
echo "}" >> /etc/logrotate.d/pandora_message

echo "/var/log/pandora_error.log {" > /etc/logrotate.d/pandora_error
echo "   rotate 12" >> /etc/logrotate.d/pandora_error
echo "   monthly" >> /etc/logrotate.d/pandora_error
echo "   compress" >> /etc/logrotate.d/pandora_error
echo "   missingok" >> /etc/logrotate.d/pandora_error
echo "   notifempty" >> /etc/logrotate.d/pandora_error
echo "}" >> /etc/logrotate.d/pandora_error

# Start Pandora at boot
cp pandora.service /etc/systemd/system/pandora.service
sed -i "s/_USER_/$SUDO_USER/g" /etc/systemd/system/pandora.service
systemctl daemon-reload
systemctl enable pandora

# Do not print messages on console
echo "mesg n" >> /home/$SUDO_USER/.bashrc

# Start Pandora-box on getty1 at boot
mkdir -p /etc/systemd/system/getty@tty1.service.d
echo "[Service]" > /etc/systemd/system/getty@tty1.service.d/override.conf
echo "ExecStart=" >> /etc/systemd/system/getty@tty1.service.d/override.conf
echo "ExecStart=-/sbin/agetty --autologin pandora --noclear %I $TERM" >> /etc/systemd/system/getty@tty1.service.d/override.conf
# echo "ExecStart=-su - pandora -c ./pandora-box/pandora-box.py" >> /etc/systemd/system/getty@tty1.service.d/override.conf

# Start pandora from bashrc
echo "export PATH=\"\$HOME/.local/bin:{\$PATH}\"" >> /home/$SUDO_USER/.bashrc
echo "exec pandora-box/pandora-box.py" >> /home/$SUDO_USER/.bashrc

# Copy ini file
su - $SUDO_USER -c "cp ~/pandora-box/pandora-box.ini.curses ~/pandora-box/pandora-box.ini"

# Reboot
echo "You may reboot the server."
