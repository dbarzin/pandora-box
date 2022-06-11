#!/usr/bin/python3

import curses
import pypandora
import time 
import sys

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
	screen.addstr(11,0,"READY.");

def endCurses():
	curses.endwin()
	curses.flushinp()

#-----------------------------------------------------------
# device
#-----------------------------------------------------------

# mount device
def mountDevice(device):
	global autoMount
    if autoMount:
        found=False
        loop=0
        while (not found) and (loop<10):
        # need to sleep before devide is mounted
            time.sleep(1)
            for partition in psutil.disk_partitions():
              if partition.device == device.device_node:
                print("Mounted at {}".format(partition.mountpoint)) 
                found=True
            loop+=1
    else:
       print("mount device to /media/box")
       res=os.system("pmount "+device.device_node+" box") 
       print("Return type: ", res)

# unmount device
def umountDevice(device):
	if not autoMount:
	    print("unmount device /media/box")
	    res = os.system("pumount /media/box")
	    print("Return type: ", res)

def deviceLoop():
	for device in iter(monitor.poll, None):
	  if 'ID_FS_TYPE' in device:
	    if device.action == 'add':
	      if device.device_node[5:7] == 'sd' and device.get('DEVTYPE')=='partition':
	        #printDeviceInfo(device)
	        print("New device {}".format(device.device_node))
	        mountDevice(device.device_node)

	    if device.action == 'remove':
	      if device.device_node[5:7] == 'sd' and device.get('DEVTYPE')=='partition':
	        print('Device removed')
	        umountDevice()

#-----------------------------------------------------------
# pandora
#-----------------------------------------------------------

# scan device at mountPoint
def scan(mountPoint):
	pp = pypandora.PyPandora(root_url= 'http://127.0.0.1:6100')

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

while True:
    key = screen.getch()
    if key == curses.KEY_MOUSE:
        break
    if key == 27:
        break

endCurses()

