import gitlab

from .. import apis, cli_args, errors, filters, log, utils
from ..cache import cache
from ..config import config


@cli_args.command(help='get total info about all collected projects')
@cli_args.query(default=':all')
@cli_args.exclude()
@cli_args.all
def total(namespace):
    config.load()

    if namespace.all:
        namespace.exclude = ':none'

    cached_search = cache.filter_map(namespace.query, False, namespace.exclude)

    counts = {}

    for pid, cached in cached_search.items():
        for filter_tag, filter_ in filters.FILTERS.items():
            counts.setdefault(filter_tag, 0)
            if filter_(cached):
                counts[filter_tag] += 1

    max_name = 1
    for filter_tag in filters.FILTERS.keys():
        max_name = max(max_name, len(filter_tag) + 1)

    for filter_tag, count in counts.items():
        filter_tag_pad = filter_tag.ljust(max_name)
        if namespace.all or count:
            if count and filter_tag == ':broken':
                log.error('{} {}'.format(filter_tag_pad, count))
            elif count and filters.tag_is_warn(filter_tag):
                log.warn('{} {}'.format(filter_tag_pad, count))
            else:
                log.success('{} {}'.format(filter_tag_pad, count))


@cli_args.command(help='clear projects from cache')
@cli_args.query()
@cli_args.exact
@cli_args.exclude()
@cli_args.all
@cli_args.force
def clear(namespace):
    config.load()

    if namespace.query == ':all':
        if not namespace.force:
            raise errors.Error('--force required on clear :all')
        cache.clear()
        raise errors.Success('Cache cleared')

    cached_search = cache.filter_map(
        namespace.query, namespace.exact, namespace.exclude)

    utils.check_found(namespace, cached_search)

    for name, cached in cached_search.items():
        cache.delete(name)
    cache.flush()


@cli_args.command(name='list', help='list cached projects')
@cli_args.query(default='')
@cli_args.exclude()
@cli_args.exact
@cli_args.quiet
@cli_args.limit
@cli_args.arg(
    '-t', '--total', action='store_true', help='print only total on filter')
def list_(namespace):
    config.load()

    if not namespace.query:
        namespace.query = ':all'
        namespace.limit = 20

    cached_search = cache.filter_map(
        namespace.query, namespace.exact, namespace.exclude)

    if namespace.total:
        if namespace.quiet:
            raise errors.Info(len(cached_search))
        else:
            raise errors.Success(
                'Found: {}, Total: {}'.format(
                    len(cached_search), cache.total()))

    utils.check_found(namespace, cached_search, all_=True)

    for line, (pid, cached) in enumerate(cached_search.items()):
        if namespace.limit is not None and line >= namespace.limit:
            if namespace.quiet:
                raise errors.Abort
            raise errors.Warn('... remaining {} entries'.format(
                len(cached_search) - line))
        log.info('{}'.format(cached.get('path') or cached.get('name') or pid))


@cli_args.command(aliases=('det',), help='show project info from cache')
@cli_args.query()
@cli_args.all
@cli_args.exact
@cli_args.exclude(default=None)
@cli_args.force
def details(namespace):
    config.load()

    cached_search = cache.filter_map(
        namespace.query, namespace.exact, namespace.exclude)

    utils.check_found(namespace, cached_search)

    for pid, cached in cached_search.items():
        if len(cached_search) > 1:
            log.info(cached['name'])

        tags = []
        for filter_tag, filter_ in filters.FILTERS.items():
            if filter_(cached):
                tags.append(filter_tag)

        if filters.filter_is_broken(cached):
            log.warn('Package is broken, call `repair` to fix it.')

        if namespace.force:
            try:
                project = apis.get().projects.get(pid)
                log.pprint(project.attributes)
            except gitlab.exceptions.GitlabGetError:
                raise errors.Error(
                    '{}: missing'.format(cached.get('name') or pid))
        else:
            log.pprint(cached)
