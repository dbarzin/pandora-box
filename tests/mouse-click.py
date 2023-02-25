#!/usr/bin/python3

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


waitMouseClick()
