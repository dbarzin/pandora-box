#!/usr/bin/python3

import curses

screen = curses.initscr()
screen.keypad(1)
curses.curs_set(0)
curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
curses.flushinp()
curses.noecho()
screen.clear()

screen.addstr(1, 0, "Press any key")

screen.getch()

curses.endwin()
curses.flushinp()
