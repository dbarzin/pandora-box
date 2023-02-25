#!/usr/bin/python3
import os


def waitMouseClick():
    mouse = open("/dev/input/mice", "rb")
    down = False
    while True:
        buf = mouse.read(3)
        if ((buf[0] & 0x1) == 1):
            down = True
        if (((buf[0] & 0x1) == 0) and down):
            break
    mouse.close()


os.system("fim *.png -qa -c 'while(1){display;sleep 1;next;}' </dev/null 2>/dev/null &")
waitMouseClick()
os.system("killall -s 9 fim")
