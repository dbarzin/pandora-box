#!/usr/bin/python3

import os
import sys
import time
import threading

mouseEvent = threading.Event()
enterEvent = threading.Event()
mouseOrEnterCondition = threading.Condition()


def mouseClickThread():
    mouse = open("/dev/input/mice", "rb")
    os.set_blocking(mouse.fileno(), False)

    down = False
    while not enterEvent.is_set():
        buf = mouse.read(3)
        if not (buf is None):
            if ((buf[0] & 0x1) == 1):
                down = True
            if (((buf[0] & 0x1) == 0) and down):
                break
        time.sleep(0.1)
    mouse.close()

    mouseEvent.set()
    with mouseOrEnterCondition:
        mouseOrEnterCondition.notify()


def enterKeyThread():
    os.set_blocking(sys.stdin.fileno(), False)

    while not mouseEvent.is_set():
        input = sys.stdin.readline()
        if (len(input) > 0):
            break
        time.sleep(0.1)

    enterEvent.set()
    with mouseOrEnterCondition:
        mouseOrEnterCondition.notify()


def waitMouseOrEnter():
    with mouseOrEnterCondition:
        threading.Thread(target=mouseClickThread, args=()).start()
        threading.Thread(target=enterKeyThread, args=()).start()

        mouseEvent.clear()
        enterEvent.clear()
        while not (mouseEvent.is_set() or enterEvent.is_set()):
            mouseOrEnterCondition.wait()


print("Wait mouse click or enter")
waitMouseOrEnter()
print("Done.")
