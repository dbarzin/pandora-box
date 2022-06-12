#!/usr/bin/python3

"""Pandora-Box is a USB scaning station based on Pandora."""

import curses
from curses import wrapper
import pypandora
import time
import sys
import pyudev
import psutil
import os
import logging

# -----------------------------------------------------------
# Config variables
# -----------------------------------------------------------

NO_SCAN = True
USB_AUTO_MOUNT = True
PANDORA_ROOT_URL = "http://127.0.0.1:6100"

# -----------------------------------------------------------
# Screen
# -----------------------------------------------------------

"""Initialise curses"""
def intit_curses():
    global screen
    screen = curses.initscr()
    screen.keypad(1)
    curses.curs_set(0)
    curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
    curses.flushinp()
    curses.noecho()
#    screen.clear()


"""Print status string"""
def print_status(strStatus):
    global status_win
    #status_win.addstr(1, 1, "Status : %-32s" % strStatus, curses.color_pair(2))
    #status_win.refresh()

"""Print current action"""
def print_action(label):
    global status_win
    #status_win.addstr(3, 1, "Action : %-32s" % label, curses.color_pair(2))
    #status_win.refresh()

"""Print FS Label"""
def print_fslabel(label):
    global status_win
    status_win.addstr(1, 1, "Partition : %-32s" % label, curses.color_pair(2))
    status_win.refresh()

"""Print FS Size"""
def print_size(label):
    global status_win
    if label == 0.0:
        status_win.addstr(2, 1, "Size :            ",curses.color_pair(2))
    else:
        status_win.addstr(2, 1, "Size : %4.1fGB    " % label,curses.color_pair(2))
    status_win.refresh()

"""Print FS Used Size"""
def print_used(label):
    global status_win
    if label == 0.0:
        status_win.addstr(3, 1, "Used :            ",curses.color_pair(2))
    else:
        status_win.addstr(3, 1, "Used : %4.1fGB    " % label,curses.color_pair(2))
    status_win.refresh()

def print_fstype(label):
    global status_win
    status_win.addstr(1, 50, "Part / Type : %-32s" % label, curses.color_pair(2))
    status_win.refresh()

def print_model(label):
    global status_win
    status_win.addstr(2, 50, "Model : %-32s" % label, curses.color_pair(2))
    status_win.refresh()

def print_serial(label):
    global status_win
    status_win.addstr(3, 50, "Serial : %-32s" % label, curses.color_pair(2))
    status_win.refresh()

"""Initialise progress bar"""
def init_bar():
    global progress_win
    progress_win = curses.newwin(3, 83, 17, 10)
    # progress_win.border(1)
    progress_win.refresh()

"""Update progress bar"""
def update_bar(progress):
    global progress_win
    if progress == 0:
        progress_win.clear()
        progress_win.border(0)
        time.sleep(0)
    else:
        pos = (80 * progress) // 100 
        progress_win.addstr(1, pos+1, "#")
    progress_win.refresh()

def init_log():
    global log_win
    log_win = curses.newwin(16, 101, 20, 0)
    log_win.border(0)
    logging.basicConfig(
        filename='pandorabox.log', 
        level=logging.INFO,
        format='%(asctime)s - %(message)s',
        datefmt='%m/%d/%y %H:%M',
        filemode='w'
    )

logs = []
def log(str):
    log_win.addstr(1,1,str,curses.color_pair(3))
    log_win.refresh()
    logging.info(str)
    logs.append(str)
    if len(logs)>15:
        logs.pop(0)
    for i in range(min(15,len(logs))):
        log_win.addstr(i+1,1,"%-80s"%logs[i],curses.color_pair(3))
    log_win.refresh()

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

"""Print main screen"""
def print_screen():
    global status_win
    curses.init_pair(1, curses.COLOR_RED, curses.COLOR_BLACK)
    curses.init_pair(2, curses.COLOR_BLUE, curses.COLOR_BLACK)
    curses.init_pair(3, curses.COLOR_GREEN, curses.COLOR_BLACK)
    title_win = curses.newwin(12, 101, 0, 0)
    # title_win.border(0)
    title_win.addstr(1, 1, s[0], curses.color_pair(1))
    title_win.addstr(2, 1, s[1], curses.color_pair(1))
    title_win.addstr(3, 1, s[2], curses.color_pair(1))
    title_win.addstr(4, 1, s[3], curses.color_pair(1))
    title_win.addstr(5, 1, s[4], curses.color_pair(1))
    title_win.addstr(6, 1, s[5], curses.color_pair(1))
    title_win.addstr(7, 1, s[6], curses.color_pair(1))
    title_win.addstr(8, 1, s[7], curses.color_pair(1))
    title_win.addstr(9, 1, s[8], curses.color_pair(1))
    title_win.addstr(10, 1, s[9], curses.color_pair(1))
    title_win.refresh()
    status_win = curses.newwin(5, 101, 12, 0)
    status_win.border(0)
    # print_status("WAITING")
    print_fslabel("")
    print_size(0.0)
    print_used(0.0)
    print_fstype("")
    print_action("")
    print_model("")
    print_serial("")
    init_bar()
    update_bar(0)
    init_log()
    log('Ready.')

"""Closes curses"""
def end_curses():
    curses.endwin()
    curses.flushinp()


# -----------------------------------------------------------
# Device
# -----------------------------------------------------------

"""Mount USB device"""
def mount_device(device):
    if USB_AUTO_MOUNT:
        found = False
        loop = 0
        while (not found) and (loop < 10):
            # need to sleep before devide is mounted
            time.sleep(1)
            for partition in psutil.disk_partitions():
                if partition.device == device.device_node:
                    log("Device mounted at {}".format(partition.mountpoint))
                    found = True
            loop += 1
        if loop < 10:
            return partition.mountpoint
        else:
            return ""
    else:
        res = os.system("pmount " + device.device_node + " box")
        if res == 1:
            return "/media/box"
        else:
            return ""
        log("Device mounted at /media/box")


"""Unmount USB device"""
def umount_device():
    if not USB_AUTO_MOUNT:
        log("Unmounting device /media/box")
        res = os.system("pumount /media/box")
        # print("Return type: ", res)


"""Main device loop"""
def device_loop():
    context = pyudev.Context()
    monitor = pyudev.Monitor.from_netlink(context)
    monitor.filter_by("block")
    for device in iter(monitor.poll, None):
        if device.get("ID_FS_USAGE") == "filesystem" and device.device_node[5:7] == "sd":
            if device.action == "add":
                log("Device inserted")
                log_device_info(device)
                # display device type
                print_status("KEY INSERTED")
                print_fslabel(device.get("ID_FS_LABEL"))
                print_fstype(device.get("ID_PART_TABLE_TYPE") + " " + device.get("ID_FS_TYPE"))
                print_model(device.get("ID_MODEL"))
                print_serial(device.get("ID_SERIAL_SHORT"))
                # Mount device
                mount_point = mount_device(device)
                statvfs=os.statvfs(mount_point)
                print_size(statvfs.f_frsize * statvfs.f_blocks // 1024 // 1024 / 1024)
                print_used(statvfs.f_frsize * (statvfs.f_blocks - statvfs.f_bfree) // 1024 // 1024 / 1024)
                log("Scan started...........")
                # fake scan
                loading = 0
                while loading < 100:
                    loading += 1
                    time.sleep(0.03)
                    update_bar(loading)
                log("Scan done.")

            if device.action == "remove":
                log("Device removed")
                #print_status("WAITING")
                print_action("Device removed")
                print_fslabel("")
                print_size(0.0)
                print_used(0.0)
                print_fstype("")
                print_action("")
                print_model("")
                print_serial("")
                umount_device()
                update_bar(0)


def log_device_info(dev):
    logging.info("Device name: %s" % dev.get("DEVNAME"))
    logging.info("Path id: %s" % dev.get("ID_PATH"))
    logging.info("Bus system: %s" % dev.get("ID_BUS"))
    logging.info("USB driver: %s" % dev.get("ID_USB_DRIVER"))
    logging.info("Device type: %s" % dev.get("DEVTYPE"))
    logging.info("Device usage: %s" % dev.get("ID_FS_USAGE"))
    logging.info("Partition type: %s" % dev.get("ID_PART_TABLE_TYPE"))
    logging.info("FS type: %s" % dev.get("ID_FS_TYPE"))
    logging.info("Partition label: %s" % dev.get("ID_FS_LABEL"))
    # logging.info("FS: %s" % dev.get("ID_FS_SYSTEM_ID"))
    logging.info("Device model: %s" % dev.get("ID_MODEL"))
    # logging.info('Usage: %s' % dev.get("ID_FS_USAGE"))
    logging.info('Model: %s' % dev.get("ID_MODEL_ID"))
    logging.info('Serial short: %s' % dev.get("ID_SERIAL_SHORT"))
    logging.info('Serial: %s' % dev.get("ID_SERIAL"))
    # logging.info(os.stat(dev.get("DEVNAME")))

# -----------------------------------------------------------
# pandora
# -----------------------------------------------------------


"""Scan a mount point with Pandora"""
def scan(mountPoint):
    pp = pypandora.PyPandora(root_url=PANDORA_ROOT_URL)

    for arg in sys.argv[1:]:
        print(arg, end="", flush=True)
        print(":", end="", flush=True)

        res = pp.submit_from_disk(arg)

        while True:
            print(".", end="", flush=True)
            time.sleep(1)

            res = pp.task_status(res["taskId"])

            if res["status"] != "WAITING":
                break

        print(res["status"])


# --------------------------------------


"""Main entry point"""
def main(stdscr):
    try:
        intit_curses()
        print_screen()
        device_loop()

    finally:
        end_curses()


# --------------------------------------


if __name__ == "__main__":
    wrapper(main)
