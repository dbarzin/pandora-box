#!/usr/bin/python3

import curses

screen = curses.initscr()
screen.keypad(1)
curses.curs_set(0)
curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
curses.flushinp()
curses.noecho()
screen.clear()

while True:
    key = screen.getch()
    screen.clear()
    screen.addstr(0, 0, 'key: {}'.format(key))
    if key == curses.KEY_MOUSE:
        _, x, y, _, button = curses.getmouse()
        screen.addstr(1, 0, 'x, y, button = {}, {}, {}'.format(x, y, button))
    elif key == 27:
        break

curses.endwin()
curses.flushinp()



