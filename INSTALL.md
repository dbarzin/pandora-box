
Pandora-BOX
============

Pandorabox is a USB scaning station base on Pandora

Install
-------

Install Ubuntu 22.04 server LTS

select (x) Ubuntu Server (minumized)

Choose to install OpenSSH server

That's all, no other packages needed

After reboot, login with the user create during the install and type :

   sudo apt install git
   git clone https://github.com/dbarzin/pandora-box
   cd pandora-box
   sudo ./install.sh




=========================================================================


Mouse terminal
---------------

sudo apt install gpm

imagemagick
-----------

imagemagick for convert command

sudo apt install imagemagick


User mount device
---------------

sudo apt install pmount 


Messages on console
-------------------

Suppress all messages from the kernel (and its drivers) except panic messages from appearing on the console.

echo "kernel.printk = 3 4 1 3" | sudo tee -a /etc/sysctl.conf


Python
------

pip install psutil pyudev

Progress Bar
------------

tqdm


Asii Art
--------
_Bloody Style_

   ██▓███   ▄▄▄       ███▄    █ ▓█████▄  ▒█████   ██▀███   ▄▄▄          ▄▄▄▄    ▒█████  ▒██   ██▒
  ▓██░  ██▒▒████▄     ██ ▀█   █ ▒██▀ ██▌▒██▒  ██▒▓██ ▒ ██▒▒████▄       ▓█████▄ ▒██▒  ██▒▒▒ █ █ ▒░
  ▓██░ ██▓▒▒██  ▀█▄  ▓██  ▀█ ██▒░██   █▌▒██░  ██▒▓██ ░▄█ ▒▒██  ▀█▄     ▒██▒ ▄██▒██░  ██▒░░  █   ░
  ▒██▄█▓▒ ▒░██▄▄▄▄██ ▓██▒  ▐▌██▒░▓█▄   ▌▒██   ██░▒██▀▀█▄  ░██▄▄▄▄██    ▒██░█▀  ▒██   ██░ ░ █ █ ▒ 
  ▒██▒ ░  ░ ▓█   ▓██▒▒██░   ▓██░░▒████▓ ░ ████▓▒░░██▓ ▒██▒ ▓█   ▓██▒   ░▓█  ▀█▓░ ████▓▒░▒██▒ ▒██▒
  ▒▓▒░ ░  ░ ▒▒   ▓▒█░░ ▒░   ▒ ▒  ▒▒▓  ▒ ░ ▒░▒░▒░ ░ ▒▓ ░▒▓░ ▒▒   ▓▒█░   ░▒▓███▀▒░ ▒░▒░▒░ ▒▒ ░ ░▓ ░
  ░▒ ░       ▒   ▒▒ ░░ ░░   ░ ▒░ ░ ▒  ▒   ░ ▒ ▒░   ░▒ ░ ▒░  ▒   ▒▒ ░   ▒░▒   ░   ░ ▒ ▒░ ░░   ░▒ ░
  ░░         ░   ▒      ░   ░ ░  ░ ░  ░ ░ ░ ░ ▒    ░░   ░   ░   ▒       ░    ░ ░ ░ ░ ▒   ░    ░  
               ░  ░         ░    ░        ░ ░     ░           ░  ░    ░          ░ ░   ░    ░  
                               ░                                           ░                   
https://patorjk.com/software/taag/#p=display&f=Bloody&t=Pandora-Box

_Flower Power_
.-------.    ____    ,---.   .--. ______         ,-----.    .-------.       ____     _______       ,-----.     _____     __   
\  _(`)_ \ .'  __ `. |    \  |  ||    _ `''.   .'  .-,  '.  |  _ _   \    .'  __ `. \  ____  \   .'  .-,  '.   \   _\   /  /  
| (_ o._)|/   '  \  \|  ,  \ |  || _ | ) _  \ / ,-.|  \ _ \ | ( ' )  |   /   '  \  \| |    \ |  / ,-.|  \ _ \  .-./ ). /  '   
|  (_,_) /|___|  /  ||  |\_ \|  ||( ''_'  ) |;  \  '_ /  | :|(_ o _) /   |___|  /  || |____/ / ;  \  '_ /  | : \ '_ .') .'    
|   '-.-'    _.-`   ||  _( )_\  || . (_) `. ||  _`,/ \ _/  || (_,_).' __    _.-`   ||   _ _ '. |  _`,/ \ _/  |(_ (_) _) '     
|   |     .'   _    || (_ o _)  ||(_    ._) ': (  '\_/ \   ;|  |\ \  |  |.'   _    ||  ( ' )  \: (  '\_/ \   ;  /    \   \    
|   |     |  _( )_  ||  (_,_)\  ||  (_.\.' /  \ `"/  \  ) / |  | \ `'   /|  _( )_  || (_{;}_) | \ `"/  \  ) /   `-'`-'    \   
/   )     \ (_ o _) /|  |    |  ||       .'    '. \_/``".'  |  |  \    / \ (_ o _) /|  (_,_)  /  '. \_/``".'   /  /   \    \  
`---'      '.(_,_).' '--'    '--''-----'`        '-----'    ''-'   `'-'   '.(_,_).' /_______.'     '-----'    '--'     '----' 
                                                                                                                              
https://patorjk.com/software/taag/#p=display&f=Flower%20Power&t=PandoraBox

Little Devils

_  (`-') (`-')  _ <-. (`-')_  _(`-')                 (`-')  (`-')  _     <-.(`-')            (`-')     
 \-.(OO ) (OO ).-/    \( OO) )( (OO ).->     .->   <-.(OO )  (OO ).-/      __( OO)      .->   (OO )_.-> 
 _.'    \ / ,---.  ,--./ ,--/  \    .'_ (`-')----. ,------,) / ,---.      '-'---.\ (`-')----. (_| \_)--.
(_...--'' | \ /`.\ |   \ |  |  '`'-..__)( OO).-.  '|   /`. ' | \ /`.\     | .-. (/ ( OO).-.  '\  `.'  / 
|  |_.' | '-'|_.' ||  . '|  |) |  |  ' |( _) | |  ||  |_.' | '-'|_.' |    | '-' `.)( _) | |  | \    .') 
|  .___.'(|  .-.  ||  |\    |  |  |  / : \|  |)|  ||  .   .'(|  .-.  |    | /`'.  | \|  |)|  | .'    \  
|  |      |  | |  ||  | \   |  |  '-'  /  '  '-'  '|  |\  \  |  | |  |    | '--'  /  '  '-'  '/  .'.  \ 
`--'      `--' `--'`--'  `--'  `------'    `-----' `--' '--' `--' `--'    `------'    `-----'`--'   '--'

https://patorjk.com/software/taag/#p=display&f=Lil%20Devil&t=Pandora%20Box

Screensaver
-----------

tty-clock -srt

Start
-----
Start ClamAV

    sudo service clamav-daemon start
    
Start Pandora

    cd pandora
    poetry run start


Homepage
--------

convert -resize 1920x1080 -background black -gravity center -extent 1920x1080 image1.png bgra:/dev/fb0

AutoStart on console
--------------------

mkdir -p /etc/systemd/system/getty@tty1.service.d
echo "[Service]" > /etc/systemd/system/getty@tty1.service.d/override.conf
echo "ExecStart=" >> /etc/systemd/system/getty@tty1.service.d/override.conf
echo "ExecStart=-su - pandora -c ./pandora-box/pandora-box.py" >> /etc/systemd/system/getty@tty1.service.d/override.conf
echo "StandardInput=tty" >> /etc/systemd/system/getty@tty1.service.d/override.conf
echo "StandardOutput=tty" >> /etc/systemd/system/getty@tty1.service.d/override.conf
echo "Type=idle" >> /etc/systemd/system/getty@tty1.service.d/override.conf




Restert getty1

sudo systemctl daemon-reload; sudo systemctl restart getty@tty1.service

src: https://wiki.archlinux.org/title/Getty#Automatic_login_to_virtual_console


The option Type=idle found in the default getty@.service will delay the service startup until all jobs are completed in order to avoid polluting the login prompt with boot-up messages.


Quarantine Folder
-----------------

mkdir /var/quarantine
chmod 0777 /var/quarantine


Move to pandora-box folder
---------------------------

abspath = os.path.abspath(__file__)
dname = os.path.dirname(abspath)
os.chdir(dname)

