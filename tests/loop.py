#!/usr/bin/python3
import os
import sys


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


# Hide blinking cursor
sys.stdout.write("\033[?1;0;0c")
sys.stdout.flush()

while True:
    os.system("killall fim")
    os.system("fim -qa image1.png 2>/dev/null &")
    waitMouseClick()
    os.system("killall fim")
    os.system("fim -qa image2.png 2>/dev/null &")
    waitMouseClick()
