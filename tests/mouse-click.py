#!/usr/bin/python3

def waitMouseClick():
    mouse = open( "/dev/input/mice", "rb" )
    while True:
        buf = mouse.read(3)
        if ((buf[0] & 0x1)==1):
            break;
    mouse.close()

waitMouseClick()

