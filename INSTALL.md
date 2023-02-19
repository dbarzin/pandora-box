Pandora-BOX
============

Host
----

CORE i5 - 4G RAM - 20G disk

Installation
------------

Install Ubuntu 22.04 server LTS

select (x) Ubuntu Server (minimized)

Choose to "install OpenSSH server"

That's all, no other packages needed

After reboot, login with the user created during the install and type :

    sudo apt install -y git
    git clone https://github.com/dbarzin/pandora-box
    cd pandora-box
    sudo ./install.sh

Configuration
-------------

Copy your configuration file to _pandora-box.ini_

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

Submit a test file to Pandora with the command line

    poetry run pandora --url http://127.0.0.1:6100 -f <<file_name>>
	
Look a the Pandora logs files

    tail -500f /var/log/pandora_message.log
    tail -500f /var/log/pandora_error.log

Look a the Pandora-box logs files

    tail -500f /var/log/pandora-box.log
    
    
