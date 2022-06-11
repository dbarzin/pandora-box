#!/usr/bin/python3

import curses
import time

curses.initscr()

def initBar():
    global progress_win
    progress_win = curses.newwin(3, 62, 3, 10)
    progress_win.border(0)

def updateBar(progress):
    global progress_win
    rangex = (60 / float(100)) * progress
    pos = int(rangex)
    display = '#'
    if pos != 0:
        progress_win.addstr(1, pos, "{}".format(display))
        progress_win.refresh()

initBar()
loading = 0
while loading < 100:
    loading += 1
    time.sleep(0.03)
    updateBar(loading)

time.sleep(1)

curses.endwin()
curses.flushinp()

