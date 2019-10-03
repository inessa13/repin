import argparse

from . import errors


def check_empty(cached_search, quiet=False):
    if cached_search:
        return

    if quiet:
        raise errors.Abort

    raise errors.NothingFound


def check_multi(cached_search, all_=False, quiet=False, message=None):
    if all_ or len(cached_search) <= 1:
        return

    if quiet:
        raise errors.Abort

    found = ', '.join(
        cached.get('path') or cached.get('name') or pid
        for pid, cached in cached_search.items(limit=3)
    )

    if message is None:
        message = '\nUse --all to process them all'

    raise errors.Warn('Found {}: {}{}'.format(
        len(cached_search), found, message))


def check_found(namespace, cached_search, all_=None, message=None):
    quiet = getattr(namespace, 'quiet', False)
    if all_ is None:
        all_ = getattr(namespace, 'all', False)

    check_empty(cached_search, quiet=quiet)
    check_multi(cached_search, all_, quiet, message=message)


class VerboseAction(argparse.Action):
    def __init__(self, *args, **kwargs):
        super(VerboseAction, self).__init__(*args, **kwargs)
        self.values = 0

    def __call__(self, parser, namespace, values, option_string=None):
        if values is None:
            self.values += 1
        else:
            try:
                self.values = int(values)
            except ValueError:
                self.values = values.count('v') + 1
        setattr(namespace, self.dest, self.values)
