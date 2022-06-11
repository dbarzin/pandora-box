#!/usr/bin/python3

import curses

screen = curses.initscr()
screen.keypad(1)
curses.curs_set(0)
curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
curses.flushinp()
curses.noecho()
screen.clear()

screen.addstr(1, 0, "   ██▓███   ▄▄▄       ███▄    █ ▓█████▄  ▒█████   ██▀███   ▄▄▄          ▄▄▄▄    ▒█████  ▒██   ██▒")
screen.addstr(2, 0, "  ▓██░  ██▒▒████▄     ██ ▀█   █ ▒██▀ ██▌▒██▒  ██▒▓██ ▒ ██▒▒████▄       ▓█████▄ ▒██▒  ██▒▒▒ █ █ ▒░")
screen.addstr(3, 0, "  ▓██░ ██▓▒▒██  ▀█▄  ▓██  ▀█ ██▒░██   █▌▒██░  ██▒▓██ ░▄█ ▒▒██  ▀█▄     ▒██▒ ▄██▒██░  ██▒░░  █   ░")
screen.addstr(4, 0, "  ▒██▄█▓▒ ▒░██▄▄▄▄██ ▓██▒  ▐▌██▒░▓█▄   ▌▒██   ██░▒██▀▀█▄  ░██▄▄▄▄██    ▒██░█▀  ▒██   ██░ ░ █ █ ▒ ")
screen.addstr(5, 0, "  ▒██▒ ░  ░ ▓█   ▓██▒▒██░   ▓██░░▒████▓ ░ ████▓▒░░██▓ ▒██▒ ▓█   ▓██▒   ░▓█  ▀█▓░ ████▓▒░▒██▒ ▒██▒")
screen.addstr(6, 0, "  ▒▓▒░ ░  ░ ▒▒   ▓▒█░░ ▒░   ▒ ▒  ▒▒▓  ▒ ░ ▒░▒░▒░ ░ ▒▓ ░▒▓░ ▒▒   ▓▒█░   ░▒▓███▀▒░ ▒░▒░▒░ ▒▒ ░ ░▓ ░")
screen.addstr(7, 0, "  ░▒ ░       ▒   ▒▒ ░░ ░░   ░ ▒░ ░ ▒  ▒   ░ ▒ ▒░   ░▒ ░ ▒░  ▒   ▒▒ ░   ▒░▒   ░   ░ ▒ ▒░ ░░   ░▒ ░")
screen.addstr(8, 0, "  ░░         ░   ▒      ░   ░ ░  ░ ░  ░ ░ ░ ░ ▒    ░░   ░   ░   ▒       ░    ░ ░ ░ ░ ▒   ░    ░  ")
screen.addstr(9, 0, "               ░  ░         ░    ░        ░ ░     ░           ░  ░    ░          ░ ░   ░    ░    ")
screen.addstr(10, 0, "                               ░                                           ░                     ")
screen.addstr(11, 0, "READY.")

while True:
    key = screen.getch()
    if key == curses.KEY_MOUSE:
        break
    if key == 27:
        break

curses.endwin()
curses.flushinp()
