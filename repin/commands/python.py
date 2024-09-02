import re

import Levenshtein

from .. import cli_args, errors, filters, log, utils
from ..cache import cache
from ..config import config

PROJECT_NAME_LEN = 60


@cli_args.command(aliases=('reqs',), help='show project info from cache')
@cli_args.query()
@cli_args.exact
@cli_args.all
@cli_args.quiet
@cli_args.arg('-i', '--index-url', help='show all info')
def requirements(namespace):
    config.load()

    cached_search = cache.filter_map(namespace.query, namespace.exact)

    utils.check_found(namespace, cached_search)

    for pid, cached in cached_search.items():
        if not namespace.quiet:
            log.info(cached['name'])
        if filters.filter_is_broken(cached):
            if not namespace.quiet:
                log.warn('Package is broken, call `repair` to fix it.')
            continue
        if not filters.filter_have_reqs(cached):
            if not namespace.quiet:
                log.warn('Package have no requirements')
            continue

        # TODO: build requirements tree

        if not namespace.quiet:
            log.success('package{}dep\tversion\tcomment'.format(
                ' ' * (32 - len('package'))))
        for req in cached[':requirements']['list']:
            req_line = req.strip()
            if req_line.startswith('# ') or req_line.startswith('--'):
                continue
            project_name, extra, dep_mode, version, comment = \
                _split_requirement_package_version(req)
            if not project_name:
                continue
            project_name = project_name + (f'[{extra}]' if extra else '')
            log.info('{}{}{}\t{}\t# {}'.format(
                project_name,
                ' ' * (32 - len(project_name)),
                dep_mode,
                version,
                comment,
            ))


@cli_args.command(help='get list of python packages, requiring specified')
@cli_args.query()
@cli_args.exclude()
@cli_args.exact
@cli_args.force
@cli_args.quiet
def reverse(namespace):
    config.load()

    cached_search = cache.filter_map(namespace.query, True)

    if not cached_search and namespace.force:
        cached_search = {
            None: {'name': namespace.query, ':languages': {'Python': 100}}
        }

    utils.check_found(namespace, cached_search, message='')

    self_pid, cached = cached_search.popitem()

    if filters.filter_is_package(cached):
        self_name = cached[':setup.py'].get('name', cached['name'])
    elif filters.get_flit_metadata(cached).get('dist-name'):
        self_name = filters.get_flit_metadata(cached)['dist-name']
    elif filters.filter_lang_python(cached):
        self_name = cached['name']
    else:
        if namespace.force:
            self_name = cached['name']
        else:
            raise errors.Error(
                '{}: is not a python package. Use --force to continue')

    dep_for = []
    dep_for_mb = {}
    for pid, project in cache.items():
        # skip self
        if pid == self_pid:
            continue
        # skip projects without requirements
        if not filters.filter_have_reqs(project):
            continue

        if (project.get(':setup.py')
                and project[':setup.py'] != 'n/a'
                and project[':setup.py'].get('name')):
            project_name = '{} ({})'.format(
                project[':setup.py'].get('name'), project['path'])
        else:
            project_name = project['path']

        # exclude
        if any(filters.FILTERS.get(sub)(project) for sub in namespace.exclude.split(',')):
            continue

        for extras, reqs in project[':requirements'].get('deps', {}).items():
            for req in reqs:
                if not req:
                    continue

                reverse_name, extra, dep_mode, version, comment = \
                    _split_requirement_package_version(req)

                if extra:
                    extra = f'[{extra}]'

                if reverse_name == self_name:
                    dep_for.append(
                        (project_name, extra or extras, dep_mode, version, comment),
                    )
                elif not namespace.exact and Levenshtein.distance(
                        reverse_name, self_name) <= 2:
                    dep_for_mb.setdefault(reverse_name, []).append(
                        (project_name, extra, dep_mode, version, comment))

    if dep_for:
        max_ver = 8
        max_dep = 4
        max_name = PROJECT_NAME_LEN
        max_extra = 2
        for project_name, extra, dep_mode, version, comment in dep_for:
            if version:
                max_ver = max(max_ver, len(version) + 2)
            max_name = max(max_name, len(project_name) + 2)
            if extra:
                max_extra = max(max_extra, len(extra) + 2)

        if not namespace.quiet:
            log.info('Found reversed dependencies:')
            log.info('{}{}{}{}comment'.format(
                'version'.ljust(max_ver),
                'dep'.ljust(max_dep),
                'extra'.ljust(max_extra),
                'project'.ljust(max_name),
            ))

        for project_name, extra, dep_mode, version, comment in dep_for:
            if namespace.quiet:
                log.info('{}{}{}'.format(
                    (version or 'latest').ljust(max_ver),
                    (dep_mode or '').ljust(max_dep),
                    project_name,
                ))
            else:
                log.info('{}{}{}{}# {}'.format(
                    (version or '*').ljust(max_ver),
                    (dep_mode or '').ljust(max_dep),
                    extra.ljust(max_extra),
                    project_name.ljust(max_name),
                    # ' ' * (PROJECT_NAME_LEN - len(project_name)),
                    comment,
                ))
    elif not namespace.quiet:
        log.warn('No strict reversed dependencies found')

    if dep_for_mb:
        if not namespace.quiet:
            log.success('Found similar reversed dependencies:')
        for name, similar in dep_for_mb.items():
            if not namespace.quiet:
                log.info('{}: '.format(name))
            for project_name, extra, dep_mode, version, comment in similar:
                log.info('{}\t{}\t{}{}# {}'.format(
                    version or 'latest', dep_mode or '', project_name,
                    ' ' * (PROJECT_NAME_LEN - len(project_name)),
                    comment,
                ))


def _split_requirement_package_version(req):
    version = None
    extra = None

    if isinstance(req, bytes):
        req = req.decode()

    if '#' in req:
        req, comment = req.split('#', 1)
        req = req.strip()
    else:
        comment = ''

    if ',' in req:
        # TODO:
        req = req.split(',', 1)[0]

    for dep_type in ('==', '~=', '>=', '>', '<'):
        if dep_type in req:
            req = req.split(dep_type, 1)
            version = req[1]
            req = req[0]
            break

    if m := re.match(r'([\w_-]+)\[([\w_,-]+)\]$', req):
        req = m.group(1)
        extra = m.group(2)

    return req, extra, dep_type, version, comment
