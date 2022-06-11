#!/usr/bin/python3

import pyudev
import psutil
import time
import os

context = pyudev.Context()
monitor = pyudev.Monitor.from_netlink(context)
monitor.filter_by('block')

autoMount=False

def printDeviceInfo(dev):
    print('')
    print('<BLOCK INFORMATION>')
    print('Device name: %s' % dev.get('DEVNAME'))
    print('Device type: %s' % dev.get('DEVTYPE'))
    print('Bus system: %s' % dev.get('ID_BUS'))
    print('Partition label: %s' % dev.get('ID_FS_LABEL'))
    print('FS: %s' % dev.get('ID_FS_SYSTEM_ID'))
    print('FS type: %s' % dev.get('ID_FS_TYPE'))
    print('Device usage: %s' % dev.get('ID_FS_USAGE'))
    print('Device model: %s' % dev.get('ID_MODEL'))
    print('Partition type: %s' % dev.get('ID_PART_TABLE_TYPE'))
    print('USB driver: %s' % dev.get('ID_USB_DRIVER'))
    print('Path id: %s' % dev.get('ID_PATH'))
    #print('Capacity: %s' % stdOut[0].strip())
    print('</BLOCK INFORMATION>')

# enumerate at device connection
for device in iter(monitor.poll, None):
  if 'ID_FS_TYPE' in device:
    if device.action == 'add':
      if device.device_node[5:7] == 'sd' and device.get('DEVTYPE')=='partition':
        printDeviceInfo(device)
        print("New device {}".format(device.device_node))
        # loop until device is mounted
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

    if device.action == 'remove':
      if device.device_node[5:7] == 'sd' and device.get('DEVTYPE')=='partition':
        print('Device removed')
        if not autoMount:
            print("unmount device /media/box")
            res = os.system("pumount /media/box")
            print("Return type: ", res)

 


