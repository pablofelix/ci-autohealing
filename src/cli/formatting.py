"""Output formatting for IC CLI — colors, headers, and tables."""

import sys

RED = '\033[0;31m'
GREEN = '\033[0;32m'
YELLOW = '\033[1;33m'
BLUE = '\033[0;34m'
CYAN = '\033[0;36m'
BOLD = '\033[1m'
DIM = '\033[2m'
HEADER_COLOR = '\033[1;38;5;110m'
NC = '\033[0m'

_use_color = sys.stdout.isatty()


def _c(code, text):
    if _use_color:
        return '{}{}{}'.format(code, text, NC)
    return text


def red(text):
    return _c(RED, text)


def green(text):
    return _c(GREEN, text)


def yellow(text):
    return _c(YELLOW, text)


def blue(text):
    return _c(BLUE, text)


def cyan(text):
    return _c(CYAN, text)


def bold(text):
    return _c(BOLD, text)


def dim(text):
    return _c(DIM, text)


def section_header(text):
    print(_c(DIM, '━' * 40))
    print(_c(HEADER_COLOR, text))
    print(_c(DIM, '━' * 40))


def subsection_header(text):
    print(bold(text))
