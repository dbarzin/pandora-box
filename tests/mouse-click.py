#!/usr/bin/python3

mouse = open( "/dev/input/mice", "rb" )

def waitMouseClick():
    while True:
        buf = mouse.read(3)
        if ((buf[0] & 0x1)==1):
            break;

waitMouseClick()

