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

"""The Pandora-Box Module."""

import configparser
import curses
import datetime
import logging
import os
import pyudev
import psutil
import queue
import threading
import time
import shutil

import pypandora


# -----------------------------------------------------------
# Threading variables
# -----------------------------------------------------------
threads = []
exitFlag = False
queueLock = threading.Lock()
workQueue = None

# -----------------------------------------------------------
# Config variables
# -----------------------------------------------------------
is_fake_scan = None
has_usb_auto_mount = None
pandora_root_url = None
has_quarantine = None
quarantine_folder = None
has_curses = None
maxThreads = None

# -----------------------------------------------------------
# Curses
# -----------------------------------------------------------
screen = None
status_win = None
progress_win = None
title_win = None
log_win = None

# Pandora logo
logo = None

# -----------------------------------------------------------
# Interval box variables
# -----------------------------------------------------------
device = None
mount_point = None
infected_files = None

# -----------------------------------------------------------


class scanThread (threading.Thread):
    """Scanning thread"""
    def __init__(self):
        threading.Thread.__init__(self)
        self.pandora = pypandora.PyPandora(root_url=pandora_root_url)

    def run(self):
        while not exitFlag:
            queueLock.acquire()
            if not workQueue.empty():
                file = workQueue.get()
                queueLock.release()
                self.scan(file)
            else:
                queueLock.release()
            time.sleep(0.1)
        # print(f"Thread-{self.id} Done.")

    def scan(self, file):
        global infected_files, scanned, file_count, used
        try:
            # get file information
            file_name = os.path.basename(file)
            file_size = os.path.getsize(file)

            # log the scan has started
            # log(
            #    f'Scan {file_name} '
            #    f'[{human_readable_size(file_size)}] '
            #    f'Thread-{id} ')

            file_scan_start_time = time.time()
            if is_fake_scan:
                status = "SKIPPED"
            else:

                # do not scan files bigger than 1G
                if file_size > (1024 * 1024 * 1024):
                    status = "TOO BIG"
                else:
                    res = self.pandora.submit_from_disk(file)

                    time.sleep(0.1)
                    loop = 0

                    while loop < (1024 * 256):
                        res = self.pandora.task_status(res["taskId"])

                        # Handle responde from Pandora
                        status = res["status"]
                        if (status != "WAITING"):
                            break

                        # wait a little
                        pass
                        time.sleep(0.1)

                        loop += 1
            file_scan_end_time = time.time()

            # log the result
            log(
                f'Scan {file_name} '
                f'[{human_readable_size(file_size)}] '
                '-> '
                f'{status} ({(file_scan_end_time - file_scan_start_time):.1f}s)')
            logging.info(
                f'file="{file_name}", '
                f'size="{file_size}", '
                f'status="{status}"", '
                f'duration="{int(file_scan_end_time - file_scan_start_time)}"')

            # Get lock
            queueLock.acquire()

            scanned += file_size
            file_count += 1

            if status == "ALERT":
                # add file to list
                infected_files.append(file)

            # Release lock
            queueLock.release()

            # update status bar
            update_bar(scanned * 100 // used)

            if has_quarantine and status == "ALERT":
                if not os.path.isdir(qfolder):
                    os.mkdir(qfolder)
                shutil.copyfile(file, os.path.join(qfolder, file_name))

        except Exception as ex:
            log(f"Unexpected error: {str(ex)}", flush=True)
            logging.info(f'error="{str(ex)}"', exc_info=True)


# ----------------------------------------------------------

def config():
    global is_fake_scan, has_usb_auto_mount, pandora_root_url
    global has_quarantine, quarantine_folder, has_curses, maxThreads
    """ read configuration file """
    # intantiate a ConfirParser
    config_parser = configparser.ConfigParser()
    # read the config file
    config_parser.read('pandora-box.ini')
    # set values
    is_fake_scan = config_parser['DEFAULT']['FAKE_SCAN'].lower() == "true"
    has_usb_auto_mount = config_parser['DEFAULT']['USB_AUTO_MOUNT'].lower() == "true"
    pandora_root_url = config_parser['DEFAULT']['PANDORA_ROOT_URL']
    # Quarantine
    has_quarantine = config_parser['DEFAULT']['QUARANTINE'].lower() == "true"
    quarantine_folder = config_parser['DEFAULT']['QUARANTINE_FOLDER']
    # Curses
    has_curses = config_parser['DEFAULT']['CURSES'].lower() == "true"
    # MaxThreads
    maxThreads = int(config_parser['DEFAULT']['THREADS'])


# ----------------------------------------------------------

def human_readable_size(size, decimal_places=1):
    """ Convert size to human readble string """
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024.0:
            return f"{size:.{decimal_places}f}{unit}"
        size /= 1024.0
    return None


# -----------------------------------------------------------
# Image Screen
# -----------------------------------------------------------

def display_image(status):
    """ Display image on screen """
    if not has_curses:
        if status == "WAIT":
            image = "images/key*.png"
        elif status == "WORK":
            image = "images/wait*.png"
        elif status == "OK":
            image = "images/ok.png"
        elif status == "BAD":
            image = "images/bad.png"
        elif status == "ERROR":
            image = "images/error.png"
        else:
            return
        # hide old image
        os.system("killall -s 9 fim 2>/dev/null")
        # display image
        if "*" in image:
            # slide show
            os.system(f"fim -qa -c 'while(1){{display;sleep 1;next;}}' {image} "
                      "</dev/null 2>/dev/null >/dev/null &")
        else:
            # only one image
            os.system(f"fim -qa {image} </dev/null 2>/dev/null >/dev/null &")


# -----------------------------------------------------------

def wait_mouse_click():
    """ Wait for mouse click event """
    with open("/dev/input/mice", "rb") as mouse:
        down = False
        while True:
            buf = mouse.read(3)
            if (buf[0] & 0x1) == 1:
                down = True
            if ((buf[0] & 0x1) == 0) and down:
                break

# -----------------------------------------------------------
# has_curses Screen
# -----------------------------------------------------------


def init_curses():
    global screen
    """Initialise curses"""
    if has_curses:
        screen = curses.initscr()
        screen.keypad(1)
        curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
        curses.flushinp()
        curses.noecho()
        curses.curs_set(0)
    else:
        display_image("WAIT")


def print_fslabel(label):
    """Print FS Label"""
    if has_curses:
        status_win.addstr(1, 1, f"Partition : {label!s:32}", curses.color_pair(2))
        status_win.refresh()


def print_size(label):
    """Print FS Size"""
    if has_curses:
        status_win.addstr(2, 1, f"Size : {label!s:32} ", curses.color_pair(2))
        status_win.refresh()


def print_used(label):
    """Print FS Used Size"""
    if has_curses:
        status_win.addstr(3, 1, f"Used : {label!s:32} ", curses.color_pair(2))
        status_win.refresh()


def print_fstype(label):
    """Print device FS type"""
    if has_curses:
        status_win.addstr(1, 50, f"Part / Type : {label!s:32}", curses.color_pair(2))
        status_win.refresh()


def print_model(label):
    """Print device model"""
    if has_curses:
        status_win.addstr(2, 50, f"Model : {label!s:32}", curses.color_pair(2))
        status_win.refresh()


def print_serial(label):
    """Print device serail number"""
    if has_curses:
        status_win.addstr(3, 50, f"Serial : {label!s:32}", curses.color_pair(2))
        status_win.refresh()


def init_bar():
    """Initialise progress bar"""
    global progress_win
    if has_curses:
        progress_win = curses.newwin(3, curses.COLS - 12, 17, 5)
        progress_win.border(0)
        progress_win.refresh()


def update_bar(progress, flush=False):
    global last_update_time
    """Update progress bar"""
    if (flush or ((time.time() - last_update_time) >= 1)):
        last_update_time = time.time()
        if has_curses:
            if progress == 0:
                progress_win.clear()
                progress_win.border(0)
                time.sleep(0)
                progress_win.addstr(0, 1, "Progress:")
            else:
                pos = ((curses.COLS - 14) * progress) // 100
                progress_win.addstr(1, 1, "#" * pos)
                progress_win.addstr(0, 1, f"Progress: {progress}%")
            progress_win.refresh()


def print_screen():
    global status_win
    """Print main screen"""
    if has_curses:
        curses.init_pair(1, curses.COLOR_RED, curses.COLOR_BLACK)
        curses.init_pair(2, curses.COLOR_BLUE, curses.COLOR_BLACK)
        curses.init_pair(3, curses.COLOR_GREEN, curses.COLOR_BLACK)
        title_win = curses.newwin(12, curses.COLS, 0, 0)
        # title_win.border(0)
        title_col = (curses.COLS - len(logo[0])) // 2
        title_win.addstr(1, title_col, logo[0], curses.color_pair(1))
        title_win.addstr(2, title_col, logo[1], curses.color_pair(1))
        title_win.addstr(3, title_col, logo[2], curses.color_pair(1))
        title_win.addstr(4, title_col, logo[3], curses.color_pair(1))
        title_win.addstr(5, title_col, logo[4], curses.color_pair(1))
        title_win.addstr(6, title_col, logo[5], curses.color_pair(1))
        title_win.addstr(7, title_col, logo[6], curses.color_pair(1))
        title_win.addstr(8, title_col, logo[7], curses.color_pair(1))
        title_win.addstr(9, title_col, logo[8], curses.color_pair(1))
        title_win.addstr(10, title_col, logo[9], curses.color_pair(1))
        title_win.refresh()
        status_win = curses.newwin(5, curses.COLS, 12, 0)
        status_win.border(0)
        status_win.addstr(0, 1, "USB Key Information")
        print_fslabel("")
        print_size("")
        print_used("")
        print_fstype("")
        print_model("")
        print_serial("")
        init_bar()
        update_bar(0, flush=True)
    log('Ready.', flush=True)
    logging.info("pandora-box-start")


def end_curses():
    global has_curses
    global curses
    """Closes curses"""
    if has_curses:
        curses.endwin()
        curses.flushinp()
    else:
        # hide old image
        os.system("killall -s 9 fim 2>/dev/null")


# -----------------------------------------------------------
# Logging windows
# -----------------------------------------------------------

def initlog():
    """Inititalize logging function"""
    global log_win
    if has_curses:
        log_win = curses.newwin(curses.LINES - 20, curses.COLS, 20, 0)
        log_win.border(0)
    logging.basicConfig(
        filename='/var/log/pandora-box.log',
        level=logging.INFO,
        format='%(asctime)s - %(message)s',
        datefmt='%m/%d/%y %H:%M'
    )


logs = []
last_update_time = 0


def log(msg, flush=False):
    """log a message with a new line"""
    if has_curses:
        # display log on screen
        logs.append(msg)
        if len(logs) > (curses.LINES - 22):
            logs.pop(0)
        log_update(flush)


def log_msg(msg):
    """update last message -> no new line"""
    if has_curses:
        # display log on screen
        logs[-1] = msg
        log_update()


def log_update(flush=False):
    """Update the log screen"""
    global last_update_time
    # do not refresh the screen too often
    if (flush or ((time.time() - last_update_time) >= 1)):
        last_update_time = time.time()
        log_win.clear()
        log_win.border(0)
        for i in range(min(curses.LINES - 22, len(logs))):
            log_win.addstr(i + 1, 1, logs[i][:curses.COLS - 2], curses.color_pair(3))
        log_win.refresh()


# -----------------------------------------------------------
# Device
# -----------------------------------------------------------

def mount_device():
    """Mount USB device"""
    global mount_point
    log('Mount device', flush=True)
    if has_usb_auto_mount:
        mount_point = None
        loop = 0
        while (mount_point is None) and (loop < 15):
            # need to sleep before devide is mounted
            time.sleep(1)
            for partition in psutil.disk_partitions():
                if partition.device == device.device_node:
                    mount_point = partition.mountpoint
            loop += 1
        if mount_device is None:
            log('No partition mounted', flush=True)
    else:
        mount_point = "/media/box"
        if not os.path.exists("/media/box"):
            log("folder /media/box does not exists", flush=True)
            return None
        os.system(f"pmount {device.device_node} /media/box >/dev/null 2>/dev/null")
        loop = 0
        while loop < 10:
            time.sleep(1)
            try:
                os.statvfs(mount_point)
            except Exception as ex:
                log(f"Unexpected error: {ex}", flush=True)
                loop += 1
                continue
            break


def umount_device():
    """Unmount USB device"""
    if has_usb_auto_mount:
        log("Sync partitions", flush=True)
        os.system("sync")
    else:
        os.system("pumount /media/box 2>/dev/null >/dev/null")


def log_device_info(dev):
    """Log device information"""
    logging.info(
        'device_name="%s", '
        'path_id="%s", '
        'bus system="%s", '
        'USB_driver="%s", '
        'device_type="%s", '
        'device_usage="%s", '
        'partition type="%s", '
        'fs_type="%s", '
        'partition_label="%s", '
        'device_model="%s", '
        'model_id="%s", '
        'serial_short="%s", '
        'serial="%s"',
        dev.get("DEVNAME"),
        dev.get("ID_PATH"),
        dev.get("ID_BUS"),
        dev.get("ID_USB_DRIVER"),
        dev.get("DEVTYPE"),
        dev.get("ID_FS_USAGE"),
        dev.get("ID_PART_TABLE_TYPE"),
        dev.get("ID_FS_TYPE"),
        dev.get("ID_FS_LABEL"),
        dev.get("ID_MODEL"),
        dev.get("ID_MODEL_ID"),
        dev.get("ID_SERIAL_SHORT"),
        dev.get("ID_SERIAL")
    )


# -----------------------------------------------------------
# pandora
# -----------------------------------------------------------

def scan():
    """Scan devce with pypandora"""
    global pandora, qfolder
    global workQueue, exitFlag, threads, queueLock
    global mount_point, infected_files, scanned, file_count, used

    # get device size
    try:
        statvfs = os.statvfs(mount_point)
    except Exception as ex:
        log(f"error={ex}", flush=True)
        logging.info("An exception was thrown!", exc_info=True)
        if not has_curses:
            display_image("ERROR")
        return "ERROR"

    # Print device information
    print_size(human_readable_size(statvfs.f_frsize * statvfs.f_blocks))
    print_used(human_readable_size(statvfs.f_frsize * (statvfs.f_blocks - statvfs.f_bfree)))
    human_readable_size(statvfs.f_frsize * (statvfs.f_blocks - statvfs.f_bfree))

    used = statvfs.f_frsize * (statvfs.f_blocks - statvfs.f_bfree)

    # scan device
    infected_files = []
    scanned = 0
    file_count = 0
    scan_start_time = time.time()

    if has_quarantine:
        qfolder = os.path.join(quarantine_folder, datetime.datetime.now().strftime("%y%m%d-%H%M"))

    # Instantice work quere
    workQueue = queue.Queue(512)

    # set exit condition to false
    exitFlag = False

    # Instanciate threads
    for _ in range(maxThreads):
        thread = scanThread()
        thread.start()
        threads.append(thread)

    # Fill the work queue
    for root, _, files in os.walk(mount_point):
        for file in files:
            while workQueue.full():
                pass
            queueLock.acquire()
            workQueue.put(os.path.join(root, file))
            queueLock.release()

    # Wait for queue to empty
    while not workQueue.empty():
        pass

    # Notify threads it's time to exit
    exitFlag = True

    # Wait for all threads to complete
    for t in threads:
        t.join()

    update_bar(100, flush=True)
    log("Scan done in %.1fs, %d files scanned, %d files infected" %
        ((time.time() - scan_start_time), file_count, len(infected_files)),
        flush=True)
    logging.info(
        f'duration="{int(time.time() - scan_start_time)}", '
        f'files_scanned="{file_count}", '
        f'files_infected="{len(infected_files)}"')
    return "CLEAN"


# --------------------------------------

def wait():
    """Wait for insert of remove of USB device"""
    # Loop
    context = pyudev.Context()
    monitor = pyudev.Monitor.from_netlink(context)
    monitor.filter_by("block")
    try:
        for dev in iter(monitor.poll, None):
            if dev.get("ID_FS_USAGE") == "filesystem" and dev.device_node[5:7] == "sd":
                if dev.action == "add":
                    return device_inserted(dev)
                if dev.action == "remove":
                    return device_removed()
    except Exception as ex:
        log(f"Unexpected error: {str(ex)}", flush=True)
        logging.info(f'error="{str(ex)}"', exc_info=True)
    return "STOP"


def device_inserted(dev):
    global has_curses
    global device
    log("Device inserted", flush=True)
    logging.info("device-inserted")
    device = dev
    log_device_info(device)
    if not has_curses:
        display_image("WORK")
    else:
        # display device type
        print_fslabel(device.get("ID_FS_LABEL"))
        print_fstype(device.get("ID_PART_TABLE_TYPE") + " " + device.get("ID_FS_TYPE"))
        print_model(device.get("ID_MODEL"))
        print_serial(device.get("ID_SERIAL_SHORT"))
    return "INSERTED"


def device_removed():
    global has_curses
    global device
    log("Device removed", flush=True)
    logging.info("device-removed")
    device = None
    if not has_curses:
        display_image("WAIT")
    else:
        print_fslabel("")
        print_size("")
        print_used("")
        print_fstype("")
        print_model("")
        print_serial("")
        update_bar(0, flush=True)
    return "WAIT"


# --------------------------------------

def mount():
    """ Mount device """
    global mount_point
    global has_curses
    mount_device()
    log(f'Partition mounted at {mount_point}', flush=True)
    if mount_point is None:
        # no partition
        if not has_curses:
            display_image("WAIT")
        return "WAIT"
    try:
        os.statvfs(mount_point)
    except Exception as ex:
        log(f"Unexpected error: {str(ex)}", flush=True)
        logging.info(f'error="{str(ex)}"', exc_info=True)
        if not has_curses:
            display_image("WAIT")
        return "WAIT"
    return "SCAN"


# --------------------------------------

def error():
    """ Display error message """
    global has_curses
    if not has_curses:
        display_image("ERROR")
    return "WAIT"


# --------------------------------------

def clean():
    """Remove infected files"""
    if len(infected_files) > 0:
        # display message
        log(f"{len(infected_files)} infected files detecetd:")
        logging.info(f"infeted_files={len(infected_files)}")

        if not has_curses:
            display_image("BAD")
            wait_mouse_click()
        else:
            # print list of files
            cnt = 0
            for file in infected_files:
                log(file)
                cnt = cnt + 1
                if (cnt >= 10):
                    log('...')
                    break
            # wait for clean
            log('PRESS KEY TO CLEAN', flush=True)
            wait_mouse_click()

        # check key is still here

        # Remove infected files
        files_removed = 0
        for file in infected_files:
            try:
                os.remove(file)
                log(f"{file} removed")
                logging.info(f'removed="{file}"')
                files_removed += 1
            except Exception as ex:
                log(f"could not remove: {str(ex)}", flush=True)
                logging.info(f'not_removed="{file}, error="{str(ex)}"', exc_info=True)
        os.system("sync")
        if not has_curses:
            display_image("OK")
        else:
            log('Device cleaned !', flush=True)
        logging.info(f'cleaned="{files_removed}/{len(infected_files)}"')
    else:
        if not has_curses:
            display_image("OK")
    umount_device()
    return "WAIT"


# --------------------------------------

def move_to_script_folder():
    """Move to pandora-box folder"""
    abspath = os.path.abspath(__file__)
    dname = os.path.dirname(abspath)
    os.chdir(dname)


# --------------------------------------

def startup():
    """Start Pandora-box"""
    global logo
    # Move to script folder
    move_to_script_folder()
    # read config
    config()
    # Initialize curesrs
    init_curses()
    # Initilize log
    initlog()
    # Read logo
    with open('pandora-box.txt', mode='r', encoding='utf-8') as file1:
        logo = file1.readlines()
    # Print logo screen
    print_screen()
    # First unmount remaining device
    umount_device()
    return "WAIT"


# --------------------------------------

def loop(state):
    """Main event loop"""
    match state:
        case "START":
            return startup()
        case "WAIT":
            return wait()
        case "INSERTED":
            return mount()
        case "SCAN":
            return scan()
        case "CLEAN":
            return clean()
        case "ERROR":
            return error()
        case _:
            return "STOP"


# --------------------------------------

def main(args):
    """Main entry point"""
    try:
        state = "START"
        while state != "STOP":
            state = loop(state)
    except Exception as ex:
        print({str(ex)})
        log(f"Unexpected error: {str(ex)}", flush=True)
        logging.info(f'error="{str(ex)}"', exc_info=True)
    finally:
        end_curses()


if __name__ == "__main__":
    print("start")
    curses.wrapper(main)
    print("done")
