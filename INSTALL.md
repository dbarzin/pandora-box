Pandora-BOX
============

Host
----

CORE i5 - 4G RAM - 30G disk

Installation
------------

Install [Ubuntu 22.04 server LTS](https://ubuntu.com/download/server)

during the installation, select (x) Ubuntu Server (minimized)

Choose to "install OpenSSH server"

Create an intial user called *pandora*

That's all, no other packages needed

After reboot, login with the user *pandora* created during the install and type :

    sudo apt install -y git
    git clone https://github.com/dbarzin/pandora-box
    cd pandora-box
    sudo ./install.sh

## Firewall

Pandora-box needs web access to these websites :

- Ubuntu repositories
- github.com
- pypi.org
- install.python-poetry.org
- clamav.net
- database.clamav.net
- download.comodo.com
- hashlookup.circl.lu

## Configuration

Copy the sample configuration file to _pandora-box.ini_

    cp pandora-box.ini.ubuntu pandora-box.ini

You can configure Pandora-box in the _pandora-box.ini_ file :

    [DEFAULT]
    ; Curses mode (full text)
    CURSES = False

    ; Set USB_AUTO_MOUNT to true is if the OS automaticaly mount USB keys
    USB_AUTO_MOUNT = False

    ; Set PANDORA_ROOT_URL to the URL of the Pandora server
    ; the default value is "http://127.0.0.1:6100"
    PANDORA_ROOT_URL = http://127.0.0.1:6100

    ; Set FAKE_SCAN to true to fake the scan process (used during developement only)
    FAKE_SCAN = False

    ; Set to true to copy infected files to the quarantine folder
    ; in the USB scanning station
    QUARANTINE = True

    ; Set quarantine folder
    QUARANTINE_FOLDER = /var/quarantine

    ; Number of threads
    THREADS = 24

## Logging

Copy the rsyslog config file :

    sudo cp rsyslog.conf/pandora-box.conf to /etc/rsyslog.d

Add the following line if you are using UDP, where 192.168.12.123 is the IP address of the remote server, you will be writing your logs to:

    $ModLoad imfile
    $InputFileName /var/log/pandora-box.log
    $InputFileTag pandora-box:
    $InputFileStateFile stat-pandora-box-info
    $InputFileFacility local7
    $InputFileSeverity info  
    $InputRunFileMonitor
    local7.info @@192.168.12.123:514

Save your changes and restart the rsyslog service with the command:

    sudo systemctl restart rsyslog

Ref: https://www.rsyslog.com/doc/v5-stable/configuration/modules/imfile.html


## Timezone

Set the correct Timezone

    sudo timedatectl set-timezone Europe/Paris


## NTP

To configure NTP, edit this file :

    sudo vi /etc/systemd/timesyncd.conf

Add this line

    NTP=<<NTP_SERVER_IP>>

## Send log to rsyslog

Copy the rsyslogd configuration file

    sudo cp ./pandora-box/rsyslog.d/pandora-box.conf /etc/rsyslog.d/

Edit the rsyslogd file

    sudo vi /etc/rsyslog.d/pandora-box.conf

Change the IP Address :

    local7.info @@<<SYSLOG_SERVER_IP>>:514

## Automatic shutdown

Edit crontab file

    sudo crontab -e

Add an automatic shutdown command at 8:00 p.m.

    0 20 * * * shutdown -h

# Update

Update the operating system

    sudo apt update && sudo apt upgrade

Update Pandora

    cd pandora && poetry run update --yes

Update Pandra-box

    cd pandora-box && git pull

# Troubleshooting

Check Pandora listening on port 6100

    sudo lsof -i -P -n | grep LISTEN

Result should contains

    ...
    gunicorn: 1034         pandora    5u  IPv4  27043      0t0  TCP *:6100 (LISTEN)
    ...

poetry: command not found

    export PATH="$HOME/.local/bin:{$PATH}"

Submit a file to Pandora with the command line

    cd pandora
    poetry run pandora --url http://127.0.0.1:6100 -f <<file_name>>
    ...
    poetry run pandora --url http://127.0.0.1:6100 --task_id ... --seed ...

Submit anti malware testfile to Pandora

    cd pandora
    wget https://secure.eicar.org/eicar.com.txt
    poetry run pandora --url http://127.0.0.1:6100 -f eicar.com.txt
    ...
    poetry run pandora --url http://127.0.0.1:6100 --task_id ... --seed ...

Look a the Pandora logs files

    tail -500f /var/log/pandora_message.log
    tail -500f /var/log/pandora_error.log

Look a the Pandora-box logs files

    tail -500f /var/log/pandora-box.log
