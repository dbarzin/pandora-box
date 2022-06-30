#/usr/bin/bash -e
# Install procedure for Pandora-Box

set -e

cd ..

# Peotry
curl -sSL https://raw.githubusercontent.com/python-poetry/poetry/master/get-poetry.py | python -
poetry --version

# REDIS
sudo apt-get update
sudo apt install build-essential tcl

git clone https://github.com/redis/redis.git
cd redis
git checkout 6.2
make
# Optionally, you can run the tests:
make test
cd ..

# Kvrocks
sudo apt-get update
sudo apt install gcc g++ make libsnappy-dev autoconf automake libtool googletest libgtest-dev

git clone --recursive https://github.com/apache/incubator-kvrocks.git kvrocks
cd kvrocks
git checkout 2.0
make -j4
# Optionally, you can run the tests:
make test
cd ..

# Pandora
git clone https://github.com/pandora-analysis/pandora.git

sudo apt install python3-dev  # for compiling things
sudo apt install libpango-1.0-0 libharfbuzz0b libpangoft2-1.0-0  # For HTML -> PDF
sudo apt install libreoffice-base-nogui libreoffice-calc-nogui libreoffice-draw-nogui libreoffice-impress-nogui libreoffice-math-nogui libreoffice-writer-nogui  # For Office -> PDF
sudo apt install exiftool  # for extracting exif information
sudo apt install unrar  # for extracting rar files
sudo apt install libxml2-dev libxslt1-dev antiword unrtf poppler-utils pstotext tesseract-ocr flac ffmpeg lame libmad0 libsox-fmt-mp3 sox libjpeg-dev swig  # for textract

cd pandora  
poetry install
echo PANDORA_HOME="`pwd`" >> .env

cp config/generic.json.sample config/generic.json

#ClamAV
sudo apt-get install clamav-daemon
# In order for the module to work, you need the signatures. 
# Running the command "freshclam" will do it but if the script is already running
# (it is started by the systemd service clamav-freshclam)
# You might want to run the commands below: 
sudo systemctl stop clamav-freshclam.service  # Stop the service
sudo freshclam  # Run the signatures update
sudo systemctl start clamav-freshclam.service # Start the service so we keep getting the updates

sudo service clamav-daemon start

# Comodo
wget https://download.comodo.com/cis/download/installs/linux/cav-linux_x64.deb
sudo dpkg --ignore-depends=libssl0.9.8 -i cav-linux_x64.deb

sudo wget http://cdn.download.comodo.com/av/updates58/sigs/bases/bases.cav -O /opt/COMODO/scanners/bases.cav

# Workers

for file in pandora/workers/*.sample; do cp -i ${file} ${file%%.sample}; done

poetry run update --yes

#---------------------
# Pandora 
#---------------------

# Python libraries
pip install psutil pyudev

# Quarantine folder
sudo mkdir /var/quarantine
sudo chown $USER /var/quarantine

# Mouse terminal
sudo apt install imagemagick pmount

# Suppress all messages from the kernel (and its drivers) except panic messages from appearing on the console.
echo "kernel.printk = 3 4 1 3" | sudo tee -a /etc/sysctl.conf

# allow write to /dev/fb0
sudo usermod -a -G video $USER

# allow read mouse input
sudo usermod -a -G input $USER


# Start Poetry
echo "cd /home/$USER/pandora" >> /etc/rc.local
echo "poetry run update --yes" >> /etc/rc.local

