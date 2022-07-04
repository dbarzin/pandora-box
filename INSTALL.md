Pandora-BOX
============

Install Ubuntu 22.04 server LTS

select (x) Ubuntu Server (minumized)

Choose to "install OpenSSH server"

That's all, no other packages needed

After reboot, login with the user create during the install and type :

    sudo apt install git
    git clone https://github.com/dbarzin/pandora-box
    cd pandora-box
    sudo ./install.sh

You can configura Pandora-box in the _pandora-box.ini_ file :

    [DEFAULT]
    ; Curses mode (full text)
    CURSES = False 

    ; Screen size (graphic mode)
    SCREEN_SIZE = "1024x600"

    ; Set USB_AUTO_MOUNT to true is if the OS mount automaticaly mount USB keys
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

