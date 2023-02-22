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

import os
import time
import logging
from curses import wrapper
from datetime import datetime
import configparser
import shutil
import curses

import pyudev
import psutil

import pypandora

class PandoraBox:
    """The PandoraBox class"""

    # -----------------------------------------------------------
    # Config variables
    # -----------------------------------------------------------
    is_fake_scan = None
    has_usb_auto_mount = None
    pandora_root_url = None
    has_quarantine = None
    quarantine_folder = None
    has_curses = None

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

    # ----------------------------------------------------------

    def _config(self):
        """ read configuration file """
        # intantiate a ConfirParser
        config_parser = configparser.ConfigParser()
        # read the config file
        config_parser.read('pandora-box.ini')
        # set values
        self.is_fake_scan=config_parser['DEFAULT']['FAKE_SCAN'].lower()=="true"
        self.has_usb_auto_mount=config_parser['DEFAULT']['USB_AUTO_MOUNT'].lower()=="true"
        self.pandora_root_url=config_parser['DEFAULT']['PANDORA_ROOT_URL']
        # Quarantine
        self.has_quarantine = config_parser['DEFAULT']['QUARANTINE'].lower()=="true"
        self.quarantine_folder = config_parser['DEFAULT']['QUARANTINE_FOLDER']
        # Curses
        self.has_curses = config_parser['DEFAULT']['CURSES'].lower()=="true"


    # ----------------------------------------------------------

    def _human_readable_size(self,size, decimal_places=1):
        """ Convert size to human readble string """
        for unit in ['B','KB','MB','GB','TB']:
            if size < 1024.0:
                break
            size /= 1024.0
        return f"{size:.{decimal_places}f}{unit}"


    # -----------------------------------------------------------
    # Image Screen
    # -----------------------------------------------------------

    def display_image(self, status):
        """ Display image on screen """
        if not self.has_curses:
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
                os.system(f"fim -qa -c 'while(1){{display;sleep 1;next;}}' {image} "\
                    "</dev/null 2>/dev/null >/dev/null &")
            else :
                # only one image
                os.system(f"fim -qa {image} </dev/null 2>/dev/null >/dev/null &")


    # -----------------------------------------------------------

    def wait_mouse_click(self):
        """ Wait for mouse click event """
        with open("/dev/input/mice", "rb" ) as mouse:
            down = False
            while True:
                buf = mouse.read(3)
                if (buf[0] & 0x1)==1:
                    down = True
                if ((buf[0] & 0x1)==0) and down:
                    break

    # -----------------------------------------------------------
    # has_curses Screen
    # -----------------------------------------------------------

    def _init_curses(self):
        """Initialise curses"""
        if self.has_curses:
            self.screen = curses.initscr()
            self.screen.keypad(1)
            curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
            curses.flushinp()
            curses.noecho()
            curses.curs_set(0)
        else:
            self.display_image("WAIT")

    def _print_fslabel(self, label):
        """Print FS Label"""
        if label is None:
            label = ""
        if self.has_curses:
            self.status_win.addstr(1, 1, f"Partition : {label:32}", curses.color_pair(2))
            self.status_win.refresh()

    def _print_size(self, label):
        """Print FS Size"""
        if self.has_curses:
            self.status_win.addstr(2, 1, f"Size : {label:32} ", curses.color_pair(2))
            self.status_win.refresh()

    def _print_used(self, label):
        """Print FS Used Size"""
        if self.has_curses:
            self.status_win.addstr(3, 1, f"Used : {label:32} ",curses.color_pair(2))
            self.status_win.refresh()

    def _print_fstype(self, label):
        """Print device FS type"""
        if self.has_curses:
            self.status_win.addstr(1, 50, f"Part / Type : {label:32}", curses.color_pair(2))
            self.status_win.refresh()

    def _print_model(self, label):
        """Print device model"""
        if self.has_curses:
            self.status_win.addstr(2, 50, f"Model : {label:32}", curses.color_pair(2))
            self.status_win.refresh()

    def _print_serial(self, label):
        """Print device serail number"""
        if self.has_curses:
            self.status_win.addstr(3, 50, f"Serial : {label:32}", curses.color_pair(2))
            self.status_win.refresh()

    def _init_bar(self):
        """Initialise progress bar"""
        if self.has_curses:
            self.progress_win = curses.newwin(3, curses.COLS-12, 17, 5)
            self.progress_win.border(0)
            self.progress_win.refresh()

    def _update_bar(self, progress):
        """Update progress bar"""
        if self.has_curses:
            if progress == 0:
                self.progress_win.clear()
                self.progress_win.border(0)
                time.sleep(0)
                self.progress_win.addstr(0, 1, "Progress:")
            else:
                pos = ((curses.COLS-14) * progress) // 100
                self.progress_win.addstr(1, 1, "#"*pos)
                self.progress_win.addstr(0, 1, f"Progress: {progress}%")
            self.progress_win.refresh()

    def _print_screen(self):
        """Print main screen"""
        if self.has_curses:
            curses.init_pair(1, curses.COLOR_RED, curses.COLOR_BLACK)
            curses.init_pair(2, curses.COLOR_BLUE, curses.COLOR_BLACK)
            curses.init_pair(3, curses.COLOR_GREEN, curses.COLOR_BLACK)
            self.title_win = curses.newwin(12, curses.COLS, 0, 0)
            # title_win.border(0)
            title_col = (curses.COLS - len(self.logo[0]))//2
            self.title_win.addstr(1, title_col, self.logo[0], curses.color_pair(1))
            self.title_win.addstr(2, title_col, self.logo[1], curses.color_pair(1))
            self.title_win.addstr(3, title_col, self.logo[2], curses.color_pair(1))
            self.title_win.addstr(4, title_col, self.logo[3], curses.color_pair(1))
            self.title_win.addstr(5, title_col, self.logo[4], curses.color_pair(1))
            self.title_win.addstr(6, title_col, self.logo[5], curses.color_pair(1))
            self.title_win.addstr(7, title_col, self.logo[6], curses.color_pair(1))
            self.title_win.addstr(8, title_col, self.logo[7], curses.color_pair(1))
            self.title_win.addstr(9, title_col, self.logo[8], curses.color_pair(1))
            self.title_win.addstr(10, title_col, self.logo[9], curses.color_pair(1))
            self.title_win.refresh()
            self.status_win = curses.newwin(5, curses.COLS, 12, 0)
            self.status_win.border(0)
            self.status_win.addstr(0, 1, "USB Key Information")
            self._print_fslabel("")
            self._print_size("")
            self._print_used("")
            self._print_fstype("")
            self._print_model("")
            self._print_serial("")
            self._init_bar()
            self._update_bar(0)
        self._log('Ready.')
        logging.info("pandora-box-start")

    def _end_curses(self):
        """Closes curses"""
        if self.has_curses:
            curses.endwin()
            curses.flushinp()
        else:
            # hide old image
            os.system("killall -s 9 fim 2>/dev/null")

    # -----------------------------------------------------------
    # Logging windows
    # -----------------------------------------------------------

    def _init_log(self):
        """Inititalize logging function"""
        if self.has_curses:
            self.log_win = curses.newwin(curses.LINES-20, curses.COLS, 20, 0)
            self.log_win.border(0)
        logging.basicConfig(
            filename='/var/log/pandora-box.log',
            level=logging.INFO,
            format='%(asctime)s - %(message)s',
            datefmt='%m/%d/%y %H:%M'
        )

    logs = []
    def _log(self, msg):
        """log something"""
        if self.has_curses:
            # display log on screen
            self.logs.append(msg)
            if len(self.logs)>(curses.LINES-22):
                self.logs.pop(0)
            self.log_win.clear()
            self.log_win.border(0)
            for i in range(min(curses.LINES-22,len(self.logs))):
                self.log_win.addstr(i+1,1,self.logs[i][:curses.COLS-2],curses.color_pair(3))
            self.log_win.refresh()

    # -----------------------------------------------------------
    # Device
    # -----------------------------------------------------------

    def mount_device(self):
        """Mount USB device"""
        self._log('Mount device')
        if self.has_usb_auto_mount:
            self.mount_point = None
            loop = 0
            while (self.mount_point is None) and (loop < 15):
                # need to sleep before devide is mounted
                time.sleep(1)
                for partition in psutil.disk_partitions():
                    if partition.device == self.device.device_node:
                        self.mount_point = partition.mountpoint
                loop += 1
            if self.mount_device is None:
                self._log('No partition mounted')
        else:
            self.mount_point = "/media/box"
            if not os.path.exists("/media/box"):
                self._log("folder /media/box does not exists")
                return None
            os.system(f"pmount {self.device.device_node} /media/box >/dev/null 2>/dev/null")
            loop = 0
            while loop < 10:
                time.sleep(1)
                try:
                    os.statvfs(self.mount_point)
                except Exception as ex:
                    self._log(f"Unexpected error: {ex}")
                    loop +=1
                    continue
                break

    def umount_device(self):
        """Unmount USB device"""
        if self.has_usb_auto_mount:
            self._log("Sync partitions")
            os.system("sync")
        else:
            os.system("pumount /media/box 2>/dev/null >/dev/null")

    def _log_device_info(self, dev):
        """Log device information"""
        logging.info(
            'device_name="%s", ' \
            'path_id="%s", ' \
            'bus system="%s", ' \
            'USB_driver="%s", ' \
            'device_type="%s", ' \
            'device_usage="%s", ' \
            'partition type="%s", ' \
            'fs_type="%s", ' \
            'partition_label="%s", ' \
            'device_model="%s", ' \
            'model_id="%s", ' \
            'serial_short="%s", '\
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

    def scan(self, used):
        """Scan a mount point with Pandora"""
        self.infected_files = []
        scanned = 0
        file_count = 0
        scan_start_time = time.time()
        if self.has_quarantine:
            qfolder = os.path.join(self.quarantine_folder,datetime.now().strftime("%y%m%d-%H%M"))
        if not self.is_fake_scan:
            pandora = pypandora.PyPandora(root_url=self.pandora_root_url)
        try:
            for root, _, files in os.walk(self.mount_point):
                for file in files:
                    status = None
                    full_path = os.path.join(root,file)
                    file_size = os.path.getsize(full_path)
                    # log("Check %s [%s]" % (file, human_readable_size(file_size)))
                    file_scan_start_time = time.time()
                    if self.is_fake_scan :
                        time.sleep(0.1)
                        status = "SKIPPED"
                    else:
                        if file_size > (1024*1024*1024):
                            status = "TOO BIG"
                        else:
                            self._log(f'-> [{full_path}]')
                            logging.info(f'scan=[{full_path}]')
                            res = pandora.submit_from_disk(full_path)
                            time.sleep(0.1)
                            loop = 0
                            while loop < 960:
                                res = pandora.task_status(res["taskId"])
                                status = res["status"]
                                if status != "WAITING":
                                    break
                                time.sleep(0.5)
                                loop += 1
                    file_scan_end_time = time.time()

                    self._log(
                        f'Scan {file} '\
                        f'[{self._human_readable_size(file_size)}] '\
                        '-> '\
                        f'{status} ({int(file_scan_end_time - file_scan_start_time)}s)')
                    logging.info(
                        f'file="{file}", '\
                        f'size="{file_size}", '\
                        f'status="{status}"", '\
                        f'duration="{int(file_scan_end_time - file_scan_start_time)}"')
                    scanned += os.path.getsize(full_path)
                    file_count += 1
                    self._update_bar(scanned * 100 // used)

                    if status == "ALERT":
                        self.infected_files.append(full_path)
                        if self.has_quarantine:
                            if not os.path.isdir(qfolder) :
                                os.mkdir(qfolder)
                            shutil.copyfile(full_path, os.path.join(qfolder,file))
        except Exception as ex :
            self._log(f"Unexpected error: {str(ex)}")
            logging.info(f'error="{str(ex)}"', exc_info=True)
            return "ERROR"
        self._update_bar(100)
        self._log("Scan done in %ds, %d files scanned, %d files infected" % 
            ((time.time() - scan_start_time),file_count,len(self.infected_files)))
        logging.info(
            f'duration="{int(time.time() - scan_start_time)}", '\
            f'files_scanned="{file_count}", '\
            f'files_infected="{len(self.infected_files)}"')
        return "CLEAN"

    # --------------------------------------

    def wait_device(self):
        """Wait for insert of remove of USB device"""
        # Loop
        context = pyudev.Context()
        monitor = pyudev.Monitor.from_netlink(context)
        monitor.filter_by("block")
        try:
            for dev in iter(monitor.poll, None):
                if dev.get("ID_FS_USAGE") == "filesystem" and dev.device_node[5:7] == "sd":
                    if dev.action == "add":
                        return self._device_inserted(dev)
                    if dev.action == "remove":
                        return self._device_removed()
        except Exception as ex:
            self._log(f"Unexpected error: {str(ex)}")
            logging.info(f'error="{str(ex)}"', exc_info=True)
        return "STOP"

    def _device_inserted(self, dev):
        self._log("Device inserted")
        logging.info("device-inserted")
        self.device = dev
        self._log_device_info(self.device)
        if not self.has_curses:
            self.display_image("WORK")
        else:
            # display device type
            self._print_fslabel(self.device.get("ID_FS_LABEL"))
            self._print_fstype(self.device.get("ID_PART_TABLE_TYPE")
                + " " + self.device.get("ID_FS_TYPE"))
            self._print_model(self.device.get("ID_MODEL"))
            self._print_serial(self.device.get("ID_SERIAL_SHORT"))
        return "INSERTED"

    def _device_removed(self):
        self._log("Device removed")
        logging.info("device-removed")
        self.device = None
        if not self.has_curses:
            self.display_image("WAIT")
        else:
            self._print_fslabel("")
            self._print_size("")
            self._print_used("")
            self._print_fstype("")
            self._print_model("")
            self._print_serial("")
            self._update_bar(0)
        return "WAIT"
    # --------------------------------------

    def mount(self):
        """ Mount device """
        self.mount_device()
        self._log(f'Partition mounted at {self.mount_point}')
        if self.mount_point is None:
            # no partition
            if not self.has_curses:
                self.display_image("WAIT")
            return "WAIT"
        try:
            os.statvfs(self.mount_point)
        except Exception as ex :
            self._log(f"Unexpected error: {str(ex)}")
            logging.info(f'error="{str(ex)}"', exc_info=True)
            if not self.has_curses:
                self.display_image("WAIT")
            return "WAIT"
        return "SCAN"

    # --------------------------------------

    def scan_device(self):
        """Scan devce with pypandora"""
        try:
            statvfs=os.statvfs(self.mount_point)
        except Exception as ex :
            self._log(f"error={ex}")
            logging.info("An exception was thrown!", exc_info=True)
            if not self.has_curses:
                self.display_image("WAIT")
            return "WAIT"
        self._print_size(self._human_readable_size(statvfs.f_frsize * statvfs.f_blocks))
        self._print_used(
        self._human_readable_size(statvfs.f_frsize * (statvfs.f_blocks - statvfs.f_bfree)))
        return self.scan(statvfs.f_frsize * (statvfs.f_blocks - statvfs.f_bfree))

    # --------------------------------------

    def error(self):
        """ Display error message """
        if not self.has_curses:
            self.display_image("ERROR")
        return "WAIT"

    # --------------------------------------

    def clean(self):
        """Remove infected files"""
        if len(self.infected_files) > 0:
            self._log(f"{len(self.infected_files)} infected_files detecetd !")
            logging.info(f"infeted_files={len(self.infected_files)}")
            if not self.has_curses:
                self.display_image("BAD")
                self.wait_mouse_click()
            else:
                self._log('PRESS KEY TO CLEAN')
                self.screen.getch()
            # Remove infected files
            for file in self.infected_files:
                try :
                    os.remove(file)
                    self._log(f"{file} removed")
                    logging.info(f'removed="{file}"')
                except Exception as ex :
                    self._log(f"Unexpected error: {str(ex)}")
                    logging.info(f'error="{str(ex)}"', exc_info=True)
            os.system("sync")
            if not self.has_curses:
                self.display_image("OK")
        else:
            if not self.has_curses:
                self.display_image("OK")
        self.umount_device()
        return "WAIT"

    # --------------------------------------

    def move_to_script_folder(self):
        """Move to pandora-box folder"""
        abspath = os.path.abspath(__file__)
        dname = os.path.dirname(abspath)
        os.chdir(dname)

    # --------------------------------------

    def startup(self):
        """Start Pandora-box"""
        # Move to script folder
        self.move_to_script_folder()
        # read config
        self._config()
        # Initialize curesrs
        self._init_curses()
        # Initilize log
        self._init_log()
        # Read logo
        with open('pandora-box.txt', mode='r', encoding='utf-8') as file1:
            self.logo = file1.readlines()
        # Print logo screen
        self._print_screen()
        # First unmount remaining device
        self.umount_device()
        return "WAIT"

    # --------------------------------------

    def loop(self, state):
        """Main event loop"""
        match state:
            case "START":
                return self.startup()
            case "WAIT":
                return self.wait_device()
            case "INSERTED":
                return self.mount()
            case "SCAN":
                return self.scan_device()
            case "CLEAN":
                return self.clean()
            case "ERROR":
                return self.error()
            case _:
                return "STOP"

    # --------------------------------------

    def main(self):
        """Main entry point"""
        try :
            state="START"
            while state!="STOP":
                state = self.loop(state)
        except Exception as ex :
            self._log(f"Unexpected error: {str(ex)}")
            logging.info(f'error="{str(ex)}"', exc_info=True)
        finally:
            self._end_curses()

def main(_):
    """Main entry point"""
    pandora_box = PandoraBox()
    pandora_box.main()

if __name__ == "__main__":
    wrapper(main)
