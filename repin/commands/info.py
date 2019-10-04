from .. import __version__, filters, log


def info(namespace):
    log.info('Repin {}'.format(__version__))
    if namespace.verbose:
        log.info('available filters:')
        for f in filters.FILTERS.keys():
            log.info(f)
