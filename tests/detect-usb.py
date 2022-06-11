#!/usr/bin/python3

import pyudev
import psutil
import time

context = pyudev.Context()
monitor = pyudev.Monitor.from_netlink(context)
monitor.filter_by('block')

# enumerate at device connection
for device in iter(monitor.poll, None):
  if 'ID_FS_TYPE' in device:
    if device.action == 'add':
      if device.device_node[5:7] == 'sd':
        print("New device {}".format(device.device_node))
        # loop until device is mounted
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

    if device.action == 'remove':
      if device.device_node[5:7] == 'sd':
        print('Device removed')
 


