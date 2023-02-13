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

#---------------------
# Pandora-box
#---------------------
pwd
ls
ls /home
ls /home/$SUDO_USER
cd /home/$SUDO_USER/pandora-box

# Python libraries
su - $SUDO_USER -c "pip install pypandora psutil pyudev"

# Quarantine folder
mkdir -p /var/quarantine
chown $SUDO_USER /var/quarantine

# FIM and pmount 
apt --fix-broken install -y 
apt install -y fim pmount

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

# Start Pandora at boot
cp pandora.service /etc/systemd/system/pandora.service
sed -i "s/_USER_/$SUDO_USER/g" /etc/systemd/system/pandora.service
systemctl daemon-reload
systemctl enable pandora

# Do not print messages on console
echo "mesg n" >> /home/$SUDO_USER/.profile

# Start Pandora-box on getty1 at boot
mkdir -p /etc/systemd/system/getty@tty1.service.d
echo "[Service]" > /etc/systemd/system/getty@tty1.service.d/override.conf
echo "ExecStart=" >> /etc/systemd/system/getty@tty1.service.d/override.conf
echo "ExecStart=-su - pandora -c ./pandora-box/pandora-box.py" >> /etc/systemd/system/getty@tty1.service.d/override.conf
echo "StandardInput=tty" >> /etc/systemd/system/getty@tty1.service.d/override.conf
echo "StandardOutput=tty" >> /etc/systemd/system/getty@tty1.service.d/override.conf
echo "Type=idle" >> /etc/systemd/system/getty@tty1.service.d/override.conf

reboot
