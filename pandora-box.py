#!/usr/bin/python3
#
# This file is part of the Pandora-box distribution.
# https://github.com/dbarzin/pandora-box
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

import curses
from curses import wrapper
import pypandora
import time
import pyudev
import psutil
import os
import logging
import configparser
import shutil
from datetime import datetime

# -----------------------------------------------------------
# Config variables
# -----------------------------------------------------------

USB_AUTO_MOUNT = False
PANDORA_ROOT_URL = "http://127.0.0.1:6100"
FAKE_SCAN = False
QUARANTINE = False
CURSES = True

""" read configuration file """
def config():
    global USB_AUTO_MOUNT, PANDORA_ROOT_URL
    global FAKE_SCAN, QUARANTINE, QUARANTINE_FOLDER
    global CURSES
    # intantiate a ConfirParser
    config = configparser.ConfigParser()
    # read the config file
    config.read('pandora-box.ini')
    # set values
    FAKE_SCAN=config['DEFAULT']['FAKE_SCAN'].lower()=="true"
    USB_AUTO_MOUNT=config['DEFAULT']['USB_AUTO_MOUNT'].lower()=="true"
    PANDORA_ROOT_URL=config['DEFAULT']['PANDORA_ROOT_URL']
    # Quarantine
    QUARANTINE = config['DEFAULT']['QUARANTINE'].lower()=="true"
    QUARANTINE_FOLDER = config['DEFAULT']['QUARANTINE_FOLDER']
    # Curses
    CURSES = config['DEFAULT']['CURSES'].lower()=="true"

# ----------------------------------------------------------

""" Convert size to human readble string """
def human_readable_size(size, decimal_places=1):
    for unit in ['B','KB','MB','GB','TB']:
        if size < 1024.0:
            break
        size /= 1024.0
    return f"{size:.{decimal_places}f}{unit}"


# -----------------------------------------------------------
# Image Screen
# -----------------------------------------------------------

def display_image(status):
    if not CURSES:
        if status=="WAIT":
            image = "images/key*.png"
        elif status=="WORK":
            image = "images/wait*.png"
        elif status=="OK":
            image = "images/ok.png"
        elif status=="BAD":
            image = "images/bad.png"
        elif status=="ERROR":
            image = "images/error.png"
        else:
            return
        # hide old image
        os.system("killall -s 9 fim 2>/dev/null")
        # display image
        if "*" in image:
            # slide show
            os.system("fim -qa -c 'while(1){display;sleep 1;next;}' %s "\
                "</dev/null 2>/dev/null >/dev/null &"
                % image)
        else :
            # only one image
            os.system("fim -qa %s </dev/null 2>/dev/null >/dev/null &" % image)


# -----------------------------------------------------------

def waitMouseClick():
    mouse = open( "/dev/input/mice", "rb" )
    down = False
    while True:
        buf = mouse.read(3)
        if (buf[0] & 0x1)==1:
            down = True
        if ((buf[0] & 0x1)==0) and down:
            break;
    mouse.close()

# -----------------------------------------------------------
# CURSES Screen
# -----------------------------------------------------------

"""Initialise curses"""
def init_curses():
    global screen
    if CURSES:
        screen = curses.initscr()
        screen.keypad(1)
        curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
        curses.flushinp()
        curses.noecho()
        curses.curs_set(0)
    else:
        display_image("WAIT")

"""Print FS Label"""
def print_fslabel(label):
    global status_win
    if CURSES:
        status_win.addstr(1, 1, "Partition : %-32s" % label, curses.color_pair(2))
        status_win.refresh()        

"""Print FS Size"""
def print_size(label):
    global status_win
    if CURSES:
        if label == None:
            status_win.addstr(2, 1, "Size :            ",curses.color_pair(2))
        else:
            status_win.addstr(2, 1, "Size : %s " % label,curses.color_pair(2))
            logging.info("size=%s" % label)
        status_win.refresh()

"""Print FS Used Size"""
def print_used(label):
    global status_win
    if CURSES:
        if label == None:
            status_win.addstr(3, 1, "Used :            ",curses.color_pair(2))
        else:
            status_win.addstr(3, 1, "Used : %s " % label,curses.color_pair(2))
            logging.info("used=%s" % label)
        status_win.refresh()

def print_fstype(label):
    global status_win
    if CURSES:
        status_win.addstr(1, 50, "Part / Type : %-32s" % label, curses.color_pair(2))
        status_win.refresh()

def print_model(label):
    global status_win
    if CURSES:
        status_win.addstr(2, 50, "Model : %-32s" % label, curses.color_pair(2))
        status_win.refresh()

def print_serial(label):
    global status_win
    if CURSES:
        status_win.addstr(3, 50, "Serial : %-32s" % label, curses.color_pair(2))
        status_win.refresh()

"""Initialise progress bar"""
def init_bar():
    global progress_win
    if CURSES:
        progress_win = curses.newwin(3, curses.COLS-12, 17, 5)
        progress_win.border(0)
        progress_win.refresh()

"""Update progress bar"""
def update_bar(progress):
    global progress_win
    if CURSES:
        if progress == 0:
            progress_win.clear()
            progress_win.border(0)
            time.sleep(0)
            progress_win.addstr(0, 1, "Progress:")
        else:
            pos = ((curses.COLS-14) * progress) // 100 
            progress_win.addstr(1, 1, "#"*pos)
            progress_win.addstr(0, 1, "Progress: %d%%" % progress)
        progress_win.refresh()

"""Splash screen"""
s = [None] * 10;
s[0] = "   ██▓███   ▄▄▄       ███▄    █ ▓█████▄  ▒█████   ██▀███   ▄▄▄          ▄▄▄▄    ▒█████  ▒██   ██▒"
s[1] = "  ▓██░  ██▒▒████▄     ██ ▀█   █ ▒██▀ ██▌▒██▒  ██▒▓██ ▒ ██▒▒████▄       ▓█████▄ ▒██▒  ██▒▒▒ █ █ ▒░"
s[2] = "  ▓██░ ██▓▒▒██  ▀█▄  ▓██  ▀█ ██▒░██   █▌▒██░  ██▒▓██ ░▄█ ▒▒██  ▀█▄     ▒██▒ ▄██▒██░  ██▒░░  █   ░"
s[3] = "  ▒██▄█▓▒ ▒░██▄▄▄▄██ ▓██▒  ▐▌██▒░▓█▄   ▌▒██   ██░▒██▀▀█▄  ░██▄▄▄▄██    ▒██░█▀  ▒██   ██░ ░ █ █ ▒ "
s[4] = "  ▒██▒ ░  ░ ▓█   ▓██▒▒██░   ▓██░░▒████▓ ░ ████▓▒░░██▓ ▒██▒ ▓█   ▓██▒   ░▓█  ▀█▓░ ████▓▒░▒██▒ ▒██▒"
s[5] = "  ▒▓▒░ ░  ░ ▒▒   ▓▒█░░ ▒░   ▒ ▒  ▒▒▓  ▒ ░ ▒░▒░▒░ ░ ▒▓ ░▒▓░ ▒▒   ▓▒█░   ░▒▓███▀▒░ ▒░▒░▒░ ▒▒ ░ ░▓ ░"
s[6] = "  ░▒ ░       ▒   ▒▒ ░░ ░░   ░ ▒░ ░ ▒  ▒   ░ ▒ ▒░   ░▒ ░ ▒░  ▒   ▒▒ ░   ▒░▒   ░   ░ ▒ ▒░ ░░   ░▒ ░"
s[7] = "  ░░         ░   ▒      ░   ░ ░  ░ ░  ░ ░ ░ ░ ▒    ░░   ░   ░   ▒       ░    ░ ░ ░ ░ ▒   ░    ░  "
s[8] = "               ░  ░         ░    ░        ░ ░     ░           ░  ░    ░          ░ ░   ░    ░    "
s[9] = "                               ░                                           ░                     "

#curses.LINES, curses.COLS

"""Print main screen"""
def print_screen():
    global status_win
    if CURSES:
        curses.init_pair(1, curses.COLOR_RED, curses.COLOR_BLACK)
        curses.init_pair(2, curses.COLOR_BLUE, curses.COLOR_BLACK)
        curses.init_pair(3, curses.COLOR_GREEN, curses.COLOR_BLACK)
        title_win = curses.newwin(12, curses.COLS, 0, 0)
        # title_win.border(0)
        title_col = (curses.COLS - len(s[0]))//2
        title_win.addstr(1, title_col, s[0], curses.color_pair(1))
        title_win.addstr(2, title_col, s[1], curses.color_pair(1))
        title_win.addstr(3, title_col, s[2], curses.color_pair(1))
        title_win.addstr(4, title_col, s[3], curses.color_pair(1))
        title_win.addstr(5, title_col, s[4], curses.color_pair(1))
        title_win.addstr(6, title_col, s[5], curses.color_pair(1))
        title_win.addstr(7, title_col, s[6], curses.color_pair(1))
        title_win.addstr(8, title_col, s[7], curses.color_pair(1))
        title_win.addstr(9, title_col, s[8], curses.color_pair(1))
        title_win.addstr(10, title_col, s[9], curses.color_pair(1))
        title_win.refresh()
        status_win = curses.newwin(5, curses.COLS, 12, 0)
        status_win.border(0)
        status_win.addstr(0, 1, "USB Key Information")
        print_fslabel("")
        print_size(None)
        print_used(None)
        print_fstype("")
        print_model("")
        print_serial("")
        init_bar()
        update_bar(0)
    log('Ready.')

"""Closes curses"""
def end_curses():
    if CURSES:
        curses.endwin()
        curses.flushinp()
    else:
        # hide old image
        os.system("killall -s 9 fim 2>/dev/null")

# -----------------------------------------------------------
# Logging windows
# -----------------------------------------------------------

def init_log():
    global log_win, logging
    if CURSES:
        log_win = curses.newwin(curses.LINES-20, curses.COLS, 20, 0)
        log_win.border(0)
    logging.basicConfig(
        filename='pandora-box.log', 
        level=logging.INFO,
        format='%(asctime)s - %(message)s',
        datefmt='%m/%d/%y %H:%M'
    )

logs = []
def log(str):
    global log_win, logging
    logging.info(str)
    if CURSES:
        # display log on screen
        logs.append(str)
        if len(logs)>(curses.LINES-22):
            logs.pop(0)
        log_win.clear()
        log_win.border(0)
        for i in range(min(curses.LINES-22,len(logs))):
            log_win.addstr(i+1,1,logs[i][:curses.COLS-2],curses.color_pair(3))
        log_win.refresh()
#   else:
#        print(str,end="\n\r")

# -----------------------------------------------------------
# Device
# -----------------------------------------------------------

"""Mount USB device"""
def mount_device():
    global device
    log('Try to mount partition')
    if USB_AUTO_MOUNT:
        found = False
        loop = 0
        while (not found) and (loop < 15):
            # need to sleep before devide is mounted
            time.sleep(1)
            for partition in psutil.disk_partitions():
                if partition.device == device.device_node:
                    found = True
            loop += 1
        if loop < 10:
            return partition.mountpoint
        else:
            log('No partition mounted')
            return None
    else:
        if not os.path.exists("/media/box"):
            log("folder /media/box does not exists")
            return None
        res = os.system("pmount " + device.device_node + " /media/box >/dev/null 2>/dev/null")
        found = False
        loop = 0
        while (not found) and (loop < 10):
            time.sleep(1)
            try:
                statvfs=os.statvfs(mount_point)
            except Exception as e :
                loop +=1
                continue
            break;
        return "/media/box"

"""Unmount USB device"""
def umount_device():
    if USB_AUTO_MOUNT: 
        log("Sync partitions")
        res = os.system("sync")
    else:
       log("Unmount partitions")
       res = os.system("pumount /media/box 2>/dev/null >/dev/null")

def log_device_info(dev):
    logging.info(
        "device_name=%s, " % dev.get("DEVNAME") +
        "path_id=%s, " % dev.get("ID_PATH") +
        "bus system=%s, " % dev.get("ID_BUS") +
        "USB_driver=%s, " % dev.get("ID_USB_DRIVER") +
        "device_type=%s, " % dev.get("DEVTYPE") +
        "device_usage=%s, " % dev.get("ID_FS_USAGE") +
        "partition type=%s, " % dev.get("ID_PART_TABLE_TYPE") +
        "fs_type=%s, " % dev.get("ID_FS_TYPE") +
        "partition_label: %s, " % dev.get("ID_FS_LABEL") +
        "device_model=%s, " % dev.get("ID_MODEL") +
        'model_id=%s, ' % dev.get("ID_MODEL_ID") +
        'serial_short=%s, ' % dev.get("ID_SERIAL_SHORT") +
        'serial=%s' % dev.get("ID_SERIAL"))

# -----------------------------------------------------------
# pandora
# -----------------------------------------------------------

"""Scan a mount point with Pandora"""
def scan(mount_point, used):
    global device, infected_filed
    infected_files = []
    scanned = 0
    file_count = 0
    scan_start_time = time.time()
    if QUARANTINE:
        quanrantine_folder = os.path.join(QUARANTINE_FOLDER,datetime.now().strftime("%y%m%d-%H%M"))
    if not FAKE_SCAN:
        pandora = pypandora.PyPandora(root_url=PANDORA_ROOT_URL)
    try:
        for root, dirs, files in os.walk(mount_point):
            for file in files:
                status = None
                full_path = os.path.join(root,file)
                file_size = os.path.getsize(full_path)
                # log("Check %s [%s]" % (file, human_readable_size(file_size)))
                file_scan_start_time = time.time()
                if FAKE_SCAN :
                    time.sleep(0.1)
                    status = "SKIPPED"
                else:
                    if file_size > (1024*1024*1024):
                        status = "TOO BIG"
                    else:
                        log("ppypandora : [%s] " % full_path)
                        res = pandora.submit_from_disk(full_path)
                        time.sleep(0.1)
                        loop = 0
                        while True and (loop < 960):
                            res = pandora.task_status(res["taskId"])
                            status = res["status"]
                            if status != "WAITING":
                               break
                            time.sleep(0.5)
                            loop += 1
                file_scan_end_time = time.time()
                log("file=%s, size=%s, status=%s, duration=%ds" % (
                    file,
                    human_readable_size(file_size),
                    status,
                   (file_scan_end_time - file_scan_start_time)))
                scanned += os.path.getsize(full_path)
                file_count += 1
                update_bar(scanned * 100 // used)

                if status == "ALERT":
                    infected_files.append(full_path)
                    if QUARANTINE:
                        if not os.path.isdir(quanrantine_folder) :
                            os.mkdir(quanrantine_folder)
                        shutil.copyfile(full_path, os.path.join(quanrantine_folder,file))
    except Exception as e :
        log("Unexpected error: %s" % e)
        log("Scan failed !")
        if not CURSES:
            display_image("ERROR")
        raise
    update_bar(100)
    log("duration=%ds, files_scanned=%d, files_infected=%d" %
        ((time.time() - scan_start_time),file_count,len(infected_files)))
    return infected_files

# --------------------------------------

def wait_device():
    global device 
    # Loop
    context = pyudev.Context()
    monitor = pyudev.Monitor.from_netlink(context)
    monitor.filter_by("block")
    try:
        for device in iter(monitor.poll, None):
            if device.get("ID_FS_USAGE") == "filesystem" and device.device_node[5:7] == "sd":
                if device.action == "add":
                    log("Device inserted")
                    log_device_info(device)
                    if not CURSES:
                        display_image("WORK")
                    else:
                        # display device type
                        print_fslabel(device.get("ID_FS_LABEL"))
                        print_fstype(device.get("ID_PART_TABLE_TYPE") + " " + device.get("ID_FS_TYPE"))
                        print_model(device.get("ID_MODEL"))
                        print_serial(device.get("ID_SERIAL_SHORT"))
                    return "INSERTED"
                if device.action == "remove":
                    log("Device removed")
                    if not CURSES:
                        display_image("WAIT")
                    else:
                        print_fslabel("")
                        print_size(None)
                        print_used(None)
                        print_fstype("")
                        print_model("")
                        print_serial("")
                        update_bar(0)
                    return "WAIT"
    except Exception as e:
        log("Unexpected error: %s" % str(e) )
        logging.info("An exception was thrown!", exc_info=True)
    finally:
        log("Done.")
    return "STOP"

# --------------------------------------

def mount():
    global device, mount_point
    # Mount device
    mount_point = mount_device()
    log('Partition mounted at %s' % mount_point)
    if mount_point == None:
        # no partition
        if not CURSES:
            display_image("WAIT")
        return "WAIT"
    try:
        statvfs=os.statvfs(mount_point)
    except Exception as e :
        log("error=%s" % e)
        logging.info("An exception was thrown!", exc_info=True)
        if not CURSES:
            display_image("WAIT")
        return "WAIT"
    return "SCAN"

# --------------------------------------

def scan_device():
    global mount_point, infected_files
    try:
        statvfs=os.statvfs(mount_point)
    except Exception as e :
        log("error=%s" % e)
        logging.info("An exception was thrown!", exc_info=True)
        if not CURSES:
            display_image("WAIT")
        return "WAIT"
    print_size(human_readable_size(statvfs.f_frsize * statvfs.f_blocks))
    print_used(human_readable_size(statvfs.f_frsize * (statvfs.f_blocks - statvfs.f_bfree)))
    infected_files = scan(mount_point, statvfs.f_frsize * (statvfs.f_blocks - statvfs.f_bfree))
    return "CLEAN"

# --------------------------------------

def clean():
    global infected_files
    # Clean files
    if len(infected_files) > 0:
        log('%d infected files found !' % len(infected_files))
        if not CURSES:
            display_image("BAD")
            waitMouseClick()
        else:
            log('PRESS KEY TO CLEAN')
            screen.getch()
        # Remove infected files
        for file in infected_files:
            try :
                os.remove(file)
                log('%s removed' % file)
            except Exception as e :
                log("Unexpected error: %s" % str(e))
                logging.info("An exception was thrown!", exc_info=True)
        os.system("sync")
        log("Clean done.")
        if not CURSES:
            display_image("OK")
    else:
        if not CURSES:
            display_image("OK")
    umount_device()
    return "WAIT"

# --------------------------------------

def moveToScriptFolder():
    abspath = os.path.abspath(__file__)
    dname = os.path.dirname(abspath)
    os.chdir(dname)

# --------------------------------------

def startup():
    moveToScriptFolder()
    init_log()
    config()
    init_curses()
    print_screen()
    # First unmount remaining device
    umount_device()
    return "WAIT"

# --------------------------------------

def loop(state):
    match state:
        case "START":
            return startup()
        case "WAIT":
            return wait_device()
        case "INSERTED":
            return mount()
        case "SCAN":
            return scan_device()
        case "CLEAN":
            return clean()
        case _:
            print("Unknwn state "+state)
            return "STOP"

# --------------------------------------

"""Main entry point"""
def main(stdscr):
    try :
        state="START"
        while (state!="STOP"):
            state = loop(state)
    except Exception as e :
         log("error=%s" % e)
         logging.info("An exception was thrown!", exc_info=True)
    finally:
        end_curses()


if __name__ == "__main__":
    wrapper(main)
