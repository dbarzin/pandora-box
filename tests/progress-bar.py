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
    pos = (60 * progress) // 100
    if pos != 0:
        progress_win.addstr(1, 1, "#" * pos)
        progress_win.refresh()


initBar()
loading = 0
while loading <= 100:
    time.sleep(0.03)
    updateBar(loading)
    loading += 3
time.sleep(1)
curses.flushinp()
