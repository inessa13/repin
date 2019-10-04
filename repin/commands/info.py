from .. import __version__, cli_args, filters, log


@cli_args.command(help='get app info')
@cli_args.verbose
def info(namespace):
    log.info('Repin {}'.format(__version__))
    if namespace.verbose:
        log.info('available filters:')
        for f in filters.FILTERS.keys():
            log.info(f)
