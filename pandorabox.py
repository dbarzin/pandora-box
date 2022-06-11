#!/usr/bin/python3

"""Pandora-Box is a USB scaning station based on Pandora."""

import curses
import pypandora
import time
import sys
import pyudev
import psutil
import os

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
    screen.clear()


"""Print status string"""
def print_status(strStatus):
    screen.addstr(12, 0, "Status : %-32s" % strStatus)
    screen.refresh()


"""Print FS Label"""
def print_fslabel(strLabel):
    screen.addstr(13, 0, "Device : %-32s" % strLabel)
    screen.refresh()


"""Print current action"""
def print_action(strAction):
    screen.addstr(14, 0, "Action : %-64s" % strAction)
    screen.refresh()


"""Initialise progress bar"""
def init_bar():
    global progress_win
    progress_win = curses.newwin(3, 62, 3, 16)
    progress_win.border(0)


"""Update progress bar"""
def update_bar(progress):
    global progress_win
    rangex = (60 / float(100)) * progress
    pos = int(rangex)
    display = "#"
    if pos != 0:
        progress_win.addstr(1, pos, "{}".format(display))
        progress_win.refresh()


def print_screen():
    screen.addstr(1, 0, "   ██▓███   ▄▄▄       ███▄    █ ▓█████▄  ▒█████   ██▀███   ▄▄▄          ▄▄▄▄    ▒█████  ▒██   ██▒")
    screen.addstr(2, 0, "  ▓██░  ██▒▒████▄     ██ ▀█   █ ▒██▀ ██▌▒██▒  ██▒▓██ ▒ ██▒▒████▄       ▓█████▄ ▒██▒  ██▒▒▒ █ █ ▒░")
    screen.addstr(3, 0, "  ▓██░ ██▓▒▒██  ▀█▄  ▓██  ▀█ ██▒░██   █▌▒██░  ██▒▓██ ░▄█ ▒▒██  ▀█▄     ▒██▒ ▄██▒██░  ██▒░░  █   ░")
    screen.addstr(4, 0, "  ▒██▄█▓▒ ▒░██▄▄▄▄██ ▓██▒  ▐▌██▒░▓█▄   ▌▒██   ██░▒██▀▀█▄  ░██▄▄▄▄██    ▒██░█▀  ▒██   ██░ ░ █ █ ▒ ")
    screen.addstr(5, 0, "  ▒██▒ ░  ░ ▓█   ▓██▒▒██░   ▓██░░▒████▓ ░ ████▓▒░░██▓ ▒██▒ ▓█   ▓██▒   ░▓█  ▀█▓░ ████▓▒░▒██▒ ▒██▒")
    screen.addstr(6, 0, "  ▒▓▒░ ░  ░ ▒▒   ▓▒█░░ ▒░   ▒ ▒  ▒▒▓  ▒ ░ ▒░▒░▒░ ░ ▒▓ ░▒▓░ ▒▒   ▓▒█░   ░▒▓███▀▒░ ▒░▒░▒░ ▒▒ ░ ░▓ ░")
    screen.addstr(7, 0, "  ░▒ ░       ▒   ▒▒ ░░ ░░   ░ ▒░ ░ ▒  ▒   ░ ▒ ▒░   ░▒ ░ ▒░  ▒   ▒▒ ░   ▒░▒   ░   ░ ▒ ▒░ ░░   ░▒ ░")
    screen.addstr(8, 0, "  ░░         ░   ▒      ░   ░ ░  ░ ░  ░ ░ ░ ░ ▒    ░░   ░   ░   ▒       ░    ░ ░ ░ ░ ▒   ░    ░  ")
    screen.addstr(9, 0, "               ░  ░         ░    ░        ░ ░     ░           ░  ░    ░          ░ ░   ░    ░    ")
    screen.addstr(10, 0, "                               ░                                           ░                     ")
    print_status("WAITING")
    print_fslabel("")
    print_action("")
    init_bar()
    update_bar(1)


def end_curses():
    curses.endwin()
    curses.flushinp()


# -----------------------------------------------------------
# device
# -----------------------------------------------------------


def mount_device(device):
    if USB_AUTO_MOUNT:
        found = False
        loop = 0
        while (not found) and (loop < 10):
            # need to sleep before devide is mounted
            time.sleep(1)
            for partition in psutil.disk_partitions():
                if partition.device == device.device_node:
                    print_action("Mounted at {}".format(partition.mountpoint))
                    found = True
            loop += 1
    else:
        print_action("mount device to /media/box")
        res = os.system("pmount " + device.device_node + " box")
        # print("Return type: ", res)


def umount_device():
    if not USB_AUTO_MOUNT:
        print_action("unmount device /media/box")
        res = os.system("pumount /media/box")
        # print("Return type: ", res)


def device_loop():
    context = pyudev.Context()
    monitor = pyudev.Monitor.from_netlink(context)
    monitor.filter_by("block")
    for device in iter(monitor.poll, None):
        if device.get("ID_FS_USAGE") == "filesystem" and device.device_node[5:7] == "sd":
            if device.action == "add":
                # print("New device {}".format(device.device_node))
                mount_device(device)
                # display device type
                print_status("KEY INSERTED")
                print_fslabel(device.get("ID_FS_LABEL"))

            if device.action == "remove":
                print_status("WAITING")
                print_action("Device removed")
                print_fslabel("")
                umount_device()


# -----------------------------------------------------------
# pandora
# -----------------------------------------------------------


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


def pandorabox():
    try:
        intit_curses()
        print_screen()
        device_loop()

    finally:
        end_curses()


# --------------------------------------


if __name__ == "__main__":
    pandorabox()
