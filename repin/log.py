import logging
import pprint

from . import errors

CLR_FAIL = '\033[91m'
CLR_WARNING = '\033[93m'
CLR_OKGREEN = '\033[92m'
CLR_END = '\033[0m'


pprint = pprint.pprint


def error(message):
    print(CLR_FAIL + message + CLR_END)


def warn(message):
    print(CLR_WARNING + message + CLR_END)


def success(message):
    print(CLR_OKGREEN + message + CLR_END)


def info(*messages):
    print(*messages)


def exception(message):
    logging.exception(message)


def catch(exc):
    if isinstance(exc, errors.Error):
        error(str(exc.args[0]))
    elif isinstance(exc, errors.Warn):
        warn(str(exc.args[0]))
    elif isinstance(exc, errors.Success):
        success(str(exc.args[0]))
    elif isinstance(exc, errors.Info):
        info(str(exc.args[0]))
    elif isinstance(exc, errors.Abort):
        pass
    else:
        exception('Unhandled exception')
