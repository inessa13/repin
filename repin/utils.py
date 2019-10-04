import itertools

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
        for pid, cached in itertools.islice(cached_search.items(), 0, 3)
    )

    if message is None:
        message = '\nUse --all to process them all'

    raise errors.Warn('Found {}: {}{}{}'.format(
        len(cached_search),
        found,
        ', ...' if len(cached_search) > 3 else '',
        message))


def check_found(namespace, cached_search, all_=None, message=None):
    quiet = getattr(namespace, 'quiet', False)
    if all_ is None:
        all_ = getattr(namespace, 'all', False)

    check_empty(cached_search, quiet=quiet)
    check_multi(cached_search, all_, quiet, message=message)
