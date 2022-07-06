#/usr/bin/bash -e

#================================
# Install script for Pandora-Box
#================================

set -e
cd ~

#---------------------
# Python 
#---------------------
apt update && sudo apt update -y
apt install -y python-is-python3 python3-pip
apt install -y libssl-dev

#---------------------
# Peotry
#---------------------
su - $SUDO_USER -c "curl -sSL https://raw.githubusercontent.com/python-poetry/poetry/master/get-poetry.py | python -"
su - $SUDO_USER -c "poetry --version"

#---------------------
# REDIS
#---------------------
apt-get update
apt install -y build-essential tcl

git clone https://github.com/redis/redis.git
cd redis
git checkout 6.2
make
# Optionally, you can run the tests:
# make test
cd ..

chown -R $SUDO_USER redis

#---------------------
# Kvrocks
#---------------------
apt-get update
apt install -y gcc g++ make libsnappy-dev autoconf automake libtool googletest libgtest-dev

git clone --recursive https://github.com/apache/incubator-kvrocks.git kvrocks
cd kvrocks
git checkout 2.0
make -j4
# Optionally, you can run the tests:
# make test
cd ..

chown -R $SUDO_USER kvrocks

#---------------------
# Pandora
#---------------------
su - $SUDO_USER -c "git clone https://github.com/pandora-analysis/pandora.git"

# install packages
apt install -y python3-dev  # for compiling things
apt install -y libpango-1.0-0 libharfbuzz0b libpangoft2-1.0-0  # For HTML -> PDF
apt install -y libreoffice-base-nogui libreoffice-calc-nogui libreoffice-draw-nogui libreoffice-impress-nogui libreoffice-math-nogui libreoffice-writer-nogui  # For Office -> PDF
apt install -y exiftool  # for extracting exif information
apt install -y unrar  # for extracting rar files
apt install -y libxml2-dev libxslt1-dev antiword unrtf poppler-utils pstotext tesseract-ocr flac ffmpeg lame libmad0 libsox-fmt-mp3 sox libjpeg-dev swig  # for textract

# set .env
cd ~/pandora
echo PANDORA_HOME="`pwd`" >> .env
chown $SUDO_USER .env

su - $SUDO_USER -c "cd ~/pandora; poetry install"
su - $SUDO_USER -c "cd ~/pandora; cp config/generic.json.sample config/generic.json"

# install yara-python
su - $SUDO_USER -c "pip install yara-python"

# ClamAV
apt-get install -y clamav-daemon
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

# Configure workers
su - $SUDO_USER -c "cd pandora; for file in pandora/workers/*.sample; do cp -i ${file} ${file%%.sample}; done"

#---------------------
# Pandora-box
#---------------------
cd ~/pandora-box

# Python libraries
su - $SUDO_USER -c "pip install pypandora psutil pyudev"

# Quarantine folder
mkdir -p /var/quarantine
chown $SUDO_USER /var/quarantine

# ImageMagick and pmount 
apt --fix-broken install -y 
apt install -y imagemagick pmount

# Suppress all messages from the kernel (and its drivers) except panic messages from appearing on the console.
echo "kernel.printk = 3 4 1 3" | tee -a /etc/sysctl.conf
# Set Permanently ulimit -n / open files in ubuntu
echo "fs.file-max = 65535" | tee -a /etc/sysctl.conf

# allow write to /dev/fb0
usermod -a -G video $SUDO_USER

# allow read mouse input
usermod -a -G input $SUDO_USER

# Start Pandora at boot
sudo cp pandora.service.sample /etc/systemd/system/pandora.service
sudpo sed -i "s/_USER_/$SUDO_USER/g" /etc/systemd/system/pandora.service
sudo systemctl daemon-reload
sudo systemctl enable pandora

# Start Pandora-box on getty1 at boot
mkdir -p /etc/systemd/system/getty@tty1.service.d
echo "[Service]" > /etc/systemd/system/getty@tty1.service.d/override.conf
echo "ExecStart=" >> /etc/systemd/system/getty@tty1.service.d/override.conf
echo "ExecStart=-su - pandora -c ./pandora-box/pandora-box.py" >> /etc/systemd/system/getty@tty1.service.d/override.conf
echo "StandardInput=tty" >> /etc/systemd/system/getty@tty1.service.d/override.conf
echo "StandardOutput=tty" >> /etc/systemd/system/getty@tty1.service.d/override.conf
echo "Type=idle" >> /etc/systemd/system/getty@tty1.service.d/override.conf

reboot
