#!/usr/bin/python3
import curses
import locale

def main(stdscr):
    # Initialiser les locales
    locale.setlocale(locale.LC_ALL, '')

    # Clear screen
    stdscr.clear()

    # Dessiner une bordure avec les caractères ACS
    stdscr.border(
        curses.ACS_VLINE, curses.ACS_VLINE,
        curses.ACS_HLINE, curses.ACS_HLINE,
        curses.ACS_ULCORNER, curses.ACS_URCORNER,
        curses.ACS_LLCORNER, curses.ACS_LRCORNER
    )

    # Rafraîchir l'écran pour afficher les modifications
    stdscr.refresh()

    # Attendre une touche pour sortir
    stdscr.getkey()

curses.wrapper(main)
import curses
import locale

def main(stdscr):
    # Initialiser les locales
    locale.setlocale(locale.LC_ALL, '')

    # Clear screen
    stdscr.clear()

    # Dessiner une bordure avec les caractères ACS
    stdscr.border(
        curses.ACS_VLINE, curses.ACS_VLINE,
        curses.ACS_HLINE, curses.ACS_HLINE,
        curses.ACS_ULCORNER, curses.ACS_URCORNER,
        curses.ACS_LLCORNER, curses.ACS_LRCORNER
    )

    # Rafraîchir l'écran pour afficher les modifications
    stdscr.refresh()

    # Attendre une touche pour sortir
    stdscr.getkey()

curses.wrapper(main)
