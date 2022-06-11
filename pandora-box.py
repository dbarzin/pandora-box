#!/usr/bin/python3

import curses
import pypandora
import time 
import sys
import pyudev
import psutil

#-----------------------------------------------------------
# Variables
#-----------------------------------------------------------

NO_SCAN=True
USB_AUTO_MOUNT=True
PANDORA_ROOT_URL="http://127.0.0.1:6100"


#-----------------------------------------------------------
# Screen
#-----------------------------------------------------------

def intitCurses():
	global screen
	screen = curses.initscr()
	screen.keypad(1)
	curses.curs_set(0)
	curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
	curses.flushinp()
	curses.noecho()
	screen.clear()

def printStatus(strStatus):
	screen.addstr(12,0,'Status : %-32s' % strStatus)
	screen.refresh()

def printFSLabel(strLabel):
	screen.addstr(13,0,'Device : %-32s' % strLabel)
	screen.refresh()

def printAction(strAction):
	screen.addstr(14,0,'Action : %-64s' % strAction)
	screen.refresh()

def initBar():
    global progress_win
    progress_win = curses.newwin(3, 62, 3, 16)
    progress_win.border(0)

def updateBar(progress):
    global progress_win
    rangex = (60 / float(100)) * progress
    pos = int(rangex)
    display = '#'
    if pos != 0:
        progress_win.addstr(1, pos, "{}".format(display))
        progress_win.refresh()

def printScreen():
	screen.addstr(1,0,"   ██▓███   ▄▄▄       ███▄    █ ▓█████▄  ▒█████   ██▀███   ▄▄▄          ▄▄▄▄    ▒█████  ▒██   ██▒")
	screen.addstr(2,0,"  ▓██░  ██▒▒████▄     ██ ▀█   █ ▒██▀ ██▌▒██▒  ██▒▓██ ▒ ██▒▒████▄       ▓█████▄ ▒██▒  ██▒▒▒ █ █ ▒░")
	screen.addstr(3,0,"  ▓██░ ██▓▒▒██  ▀█▄  ▓██  ▀█ ██▒░██   █▌▒██░  ██▒▓██ ░▄█ ▒▒██  ▀█▄     ▒██▒ ▄██▒██░  ██▒░░  █   ░")
	screen.addstr(4,0,"  ▒██▄█▓▒ ▒░██▄▄▄▄██ ▓██▒  ▐▌██▒░▓█▄   ▌▒██   ██░▒██▀▀█▄  ░██▄▄▄▄██    ▒██░█▀  ▒██   ██░ ░ █ █ ▒ ")
	screen.addstr(5,0,"  ▒██▒ ░  ░ ▓█   ▓██▒▒██░   ▓██░░▒████▓ ░ ████▓▒░░██▓ ▒██▒ ▓█   ▓██▒   ░▓█  ▀█▓░ ████▓▒░▒██▒ ▒██▒")
	screen.addstr(6,0,"  ▒▓▒░ ░  ░ ▒▒   ▓▒█░░ ▒░   ▒ ▒  ▒▒▓  ▒ ░ ▒░▒░▒░ ░ ▒▓ ░▒▓░ ▒▒   ▓▒█░   ░▒▓███▀▒░ ▒░▒░▒░ ▒▒ ░ ░▓ ░")
	screen.addstr(7,0,"  ░▒ ░       ▒   ▒▒ ░░ ░░   ░ ▒░ ░ ▒  ▒   ░ ▒ ▒░   ░▒ ░ ▒░  ▒   ▒▒ ░   ▒░▒   ░   ░ ▒ ▒░ ░░   ░▒ ░")
	screen.addstr(8,0,"  ░░         ░   ▒      ░   ░ ░  ░ ░  ░ ░ ░ ░ ▒    ░░   ░   ░   ▒       ░    ░ ░ ░ ░ ▒   ░    ░  ")
	screen.addstr(9,0,"               ░  ░         ░    ░        ░ ░     ░           ░  ░    ░          ░ ░   ░    ░    ")
	screen.addstr(10,0,"                               ░                                           ░                     ")
	printStatus("WAITING")
	printFSLabel('')
	printAction('')
	initBar()
	updateBar(1)

def endCurses():
	curses.endwin()
	curses.flushinp()

#-----------------------------------------------------------
# device
#-----------------------------------------------------------

# mount device
def mountDevice(device):
    if USB_AUTO_MOUNT:
        found=False
        loop=0
        while (not found) and (loop<10):
        # need to sleep before devide is mounted
            time.sleep(1)
            for partition in psutil.disk_partitions():
              if partition.device == device.device_node:
                printAction("Mounted at {}".format(partition.mountpoint)) 
                found=True
            loop+=1
    else:
       printAction("mount device to /media/box")
       res=os.system("pmount "+device.device_node+" box") 
       #print("Return type: ", res)

# unmount device
def umountDevice():
	if not USB_AUTO_MOUNT:
	    printAction("unmount device /media/box")
	    res = os.system("pumount /media/box")
	    # print("Return type: ", res)

def deviceLoop():
	context = pyudev.Context()
	monitor = pyudev.Monitor.from_netlink(context)
	monitor.filter_by('block')
	for device in iter(monitor.poll, None):
	  if 'ID_FS_TYPE' in device:
	    if device.action == 'add':
	      if device.device_node[5:7] == 'sd' and device.get('DEVTYPE')=='partition':
	        #print("New device {}".format(device.device_node))
	        mountDevice(device)
	        # display device type
	        printStatus("KEY INSERTED")
	        printFSLabel(device.get('ID_FS_LABEL'))

	    if device.action == 'remove':
	      if device.device_node[5:7] == 'sd' and device.get('DEVTYPE')=='partition':
	      	printStatus("WAITING")
	      	printAction('Device removed')
	      	printFSLabel('')
	      	umountDevice()

#-----------------------------------------------------------
# pandora
#-----------------------------------------------------------

# scan device at mountPoint
def scan(mountPoint):
	pp = pypandora.PyPandora(root_url= PANDORA_ROOT_URL)

	for arg in sys.argv[1:]:
	    print(arg,end='',flush=True)
	    print(":",end='',flush=True)

	    res = pp.submit_from_disk(arg)

	    while True:
	        print('.',end='',flush=True)
	        time.sleep(1)

	        res = pp.task_status(res['taskId'])

	        if res['status']!='WAITING':
	            break

	    print(res['status'])

#--------------------------------------
intitCurses()
printScreen()

deviceLoop()

while True:
    key = screen.getch()
    if key == curses.KEY_MOUSE:
        break
    if key == 27:
        break

endCurses()

