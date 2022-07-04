#/usr/bin/bash -e
# Install procedure for Pandora-Box

set -e

cd ..

# Peotry
curl -sSL https://raw.githubusercontent.com/python-poetry/poetry/master/get-poetry.py | python -
poetry --version

# REDIS
apt-get update
apt install build-essential tcl

git clone https://github.com/redis/redis.git
cd redis
git checkout 6.2
make
# Optionally, you can run the tests:
make test
cd ..

chown -R $SUDO_USER redis

# Kvrocks
apt-get update
apt install gcc g++ make libsnappy-dev autoconf automake libtool googletest libgtest-dev

git clone --recursive https://github.com/apache/incubator-kvrocks.git kvrocks
cd kvrocks
git checkout 2.0
make -j4
# Optionally, you can run the tests:
make test
cd ..

chown -R $SUDO_USER kvrocks

# Pandora
git clone https://github.com/pandora-analysis/pandora.git

apt install python3-dev  # for compiling things
apt install libpango-1.0-0 libharfbuzz0b libpangoft2-1.0-0  # For HTML -> PDF
apt install libreoffice-base-nogui libreoffice-calc-nogui libreoffice-draw-nogui libreoffice-impress-nogui libreoffice-math-nogui libreoffice-writer-nogui  # For Office -> PDF
apt install exiftool  # for extracting exif information
apt install unrar  # for extracting rar files
apt install libxml2-dev libxslt1-dev antiword unrtf poppler-utils pstotext tesseract-ocr flac ffmpeg lame libmad0 libsox-fmt-mp3 sox libjpeg-dev swig  # for textract

cd pandora  
poetry install
echo PANDORA_HOME="`pwd`" >> .env

cp config/generic.json.sample config/generic.json

# ClamAV
apt-get install clamav-daemon
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

# Workers

for file in pandora/workers/*.sample; do cp -i ${file} ${file%%.sample}; done

chown -R $SUDO_USER .

poetry run update --yes

#---------------------
# Pandora-box
#---------------------

# Python libraries
pip install psutil pyudev

# Quarantine folder
mkdir /var/quarantine
chown $SUDO_USER /var/quarantine

# Mouse terminal
apt install imagemagick pmount

# Suppress all messages from the kernel (and its drivers) except panic messages from appearing on the console.
echo "kernel.printk = 3 4 1 3" | tee -a /etc/sysctl.conf

# allow write to /dev/fb0
usermod -a -G video $SUDO_USER

# allow read mouse input
usermod -a -G input $SUDO_USER

# Start Poetry at boot
echo "su - $USER -c \"cd /home/$USER/pandora ; poetry run update --yes\" 2>&1 >storage/pandora.log" >> /etc/rc.local
chmod +x /etc/rc.local

