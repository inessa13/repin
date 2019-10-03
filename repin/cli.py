#!/usr/bin/env python3
import argparse
import base64
import concurrent.futures
import getpass
import time

import gitlab
import Levenshtein

from . import __version__, filters, errors, utils, commands, helpers, log
from .cache import cache
from .config import config

PROJECT_NAME_LEN = 60


def cmd_info(__):
    log.info('version: {}'.format(__version__))
    log.info('available filters:')
    for f in filters.FILTERS.keys():
        log.info(f)


def cmd_version(__):
    log.info('Repin {}'.format(__version__))


def cmd_init(namespace):
    config.prepare('.' if namespace.local else '~')

    if config.has_profile(namespace.profile):
        raise errors.Warn(
            'profile `{}` already exists'.format(namespace.profile))

    url = input('url: ')
    token = getpass.getpass('token: ')

    config.add_profile(namespace.profile, url, token)
    config.switch_profile(namespace.profile)
    config.flush()
    log.success('inited')


def cmd_config(namespace):
    config.load()

    if namespace.switch:
        config.switch_profile(namespace.switch)
        config.flush()
        raise errors.Success('set config to {}'.format(namespace.switch))

    log.info('Config root: {}'.format(config.root))
    log.info('Profile: {}'.format(config.current_profile()))
    if namespace.verbose:
        log.info('Available profiles:')
        for profile, url in config.iter_profiles():
            log.info(' ', profile, url)


def cmd_total(namespace):
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


def get_api():
    return gitlab.Gitlab.from_config(config.current_profile(), [
        config.path
    ])


def _limit_str(value, limit):
    if len(value) <= limit:
        return value
    return value[:limit - 3] + '...'


def cmd_update(namespace):
    config.load()

    return cmd_repair(namespace, default=':outdated')


def cmd_repair(namespace, default=':broken'):
    config.load()

    gl = get_api()
    cached_search = cache.filter_map(
        namespace.query, namespace.exact, namespace.exclude)

    utils.check_found(
        namespace, cached_search, namespace.query != default or namespace.all)

    fixed = 0
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=5)
    retry_timeout = 2

    tasks = {
        pool.submit(helpers.fix_cache, gl, pid, cached, namespace.force, default): pid
        for pid, cached in cached_search.items()}
    while tasks:
        retry = set()
        retry_step = 1
        try:
            for i, feature in enumerate(concurrent.futures.as_completed(tasks)):
                pid = tasks[feature]

                try:
                    cached = feature.result()
                    log.info('{}: package updated'.format(cached['name']))

                except errors.Client as exc:
                    log.catch(exc)
                    continue

                except KeyError as exc:
                    if exc.args[0] == 'retry-after':
                        retry.add(pid)
                    else:
                        log.exception(
                            '{}: package fix failed'.format(
                                cache.select(pid, {}).get('name') or pid))
                    continue

                except Exception:
                    log.exception(
                        '{}: package fix failed'.format(
                            cache.select(pid, {}).get('name') or pid))
                    continue

                else:
                    fixed += 1

                if fixed and not i % 10:
                    cache.flush()
        except KeyboardInterrupt:
            log.warn('Interrupted')
            break

        if retry:
            log.warn('need to retry {} entries'.format(len(retry)))
            time.sleep(retry_timeout * retry_step)
            tasks = {
                pool.submit(
                    helpers.fix_cache,
                    gl, pid, cached, namespace.force, default,
                ): pid
                for pid, cached in cached_search.items()
                if pid in retry
            }
            retry_step += 1
        else:
            break

    if fixed:
        cache.flush()

    log.success('Fixed: {}, Found: {}, Total: {}'.format(
        fixed, len(cached_search), cache.total()))


def cmd_clear(namespace):
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


def cmd_list(namespace):
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


def cmd_show(namespace):
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

        if tags:
            log.info('Tags: {}'.format(' '.join(tags)))
        else:
            log.warn('Package have no tags')

        if filters.filter_is_broken(cached):
            log.warn('Package is broken, call `repair` to fix it.')

        if namespace.force:
            log.pprint(cached)


def cmd_cat(namespace):
    config.load()

    cached_search = cache.filter_map(
        namespace.query, namespace.exact, False)

    utils.check_found(namespace, cached_search)

    gl = get_api()
    for pid, cached in cached_search.items():
        try:
            project = gl.projects.get(pid)
        except gitlab.exceptions.GitlabGetError:
            log.error('{}: missing'.format(cached.get('name') or pid))
            continue

        if namespace.file[-1] == '/':
            try:
                files = project.repository_tree(
                    path=namespace.file, ref='master')
            except gitlab.exceptions.GitlabGetError:
                continue
            for file in files:
                if file['type'] == 'tree':
                    log.info(file['path'] + '/')
                else:
                    log.info(file['path'])
        else:
            try:
                file = project.files.get(
                    file_path=namespace.file, ref='master')
            except gitlab.exceptions.GitlabGetError:
                continue
            log.info(base64.b64decode(file.content).decode())


def cmd_requirements(namespace):
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
            project_name, dep_mode, version, comment = \
                _split_requirement_package_version(req)
            if not project_name:
                continue
            log.info('{}{}{}\t{}\t# {}'.format(
                project_name,
                ' ' * (32 - len(project_name)),
                dep_mode,
                version,
                comment,
            ))


def cmd_reverse(namespace):
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

        comment_addt = ''
        if project['archived']:
            comment_addt += ' :archived'

        for req in project[':requirements']['list']:
            if not req:
                continue
            reverse_name, dep_mode, version, comment = \
                _split_requirement_package_version(req)
            comment += comment_addt
            if reverse_name == self_name:
                dep_for.append((project_name, dep_mode, version, comment))
            elif not namespace.exact and Levenshtein.distance(
                    reverse_name, self_name) <= 2:
                dep_for_mb.setdefault(reverse_name, []).append(
                    (project_name, dep_mode, version, comment))

    if dep_for:
        max_ver = 8
        max_dep = 4
        max_name = PROJECT_NAME_LEN
        for project_name, dep_mode, version, comment in dep_for:
            if version:
                max_ver = max(max_ver, len(version) + 2)
            max_name = max(max_name, len(project_name) + 2)

        if not namespace.quiet:
            log.info('Found reversed dependencies:')
            log.info('version{}dep{}project{}comment'.format(
                ' ' * (max_ver - len('version')),
                ' ' * (max_dep - len('dep')),
                ' ' * (max_name - len('project')),
            ))
        for project_name, dep_mode, version, comment in dep_for:
            if namespace.quiet:
                log.info('{}{}{}'.format(
                    (version or 'latest').ljust(max_ver),
                    (dep_mode or '').ljust(max_dep),
                    project_name,
                ))
            else:
                log.info('{}{}{}# {}'.format(
                    (version or '*').ljust(max_ver),
                    (dep_mode or '').ljust(max_dep),
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
            for project_name, dep_mode, version, comment in similar:
                log.info('{}\t{}\t{}{}# {}'.format(
                    version or 'latest', dep_mode or '', project_name,
                    ' ' * (PROJECT_NAME_LEN - len(project_name)),
                    comment,
                ))


def _split_requirement_package_version(req):
    if '#' in req:
        req, comment = req.split('#', 1)
        req = req.strip()
    else:
        comment = ''

    if ',' in req:
        # TODO:
        req = req.split(',', 1)[0]

    for d in ('==', '~=', '>=', '>', '<'):
        if d in req:
            req = req.split(d, 1)
            req = req[0], d, req[1], comment
            break
    else:
        req = (req, None, None, comment)
    return req


def parser_factory(subparsers):
    def init_parser(name, func, args=(), **kwargs):
        parser = subparsers.add_parser(name, **kwargs)
        parser.set_defaults(func=func)
        if 'query' in args:
            parser.add_argument('query', help='project name/path/tag')
        if 'query_all' in args:
            parser.add_argument(
                'query',
                nargs='?',
                default=':all',
                help='project name/path/tag')
        if 'exact' in args:
            parser.add_argument(
                '-e', '--exact', action='store_true',
                help='exact query match project name/path')
        if 'exclude' in args:
            parser.add_argument(
                '-x', '--exclude',
                default=':archived',
                help='exclude from query; by default excluding archived')
        if 'exclude2' in args:
            parser.add_argument(
                '-x', '--exclude',
                help='exclude from query; by default excluding archived')
        if 'all' in args:
            parser.add_argument(
                '-a', '--all',
                action='store_true', help='proceed with all found entries')
        if 'force' in args:
            parser.add_argument(
                '-F', '--force', action='store_true',
                help='force proceed')
        if 'quiet' in args:
            parser.add_argument(
                '-q', '--quiet', action='store_true',
                help='quiet output')
        if 'verbose' in args:
            parser.add_argument(
                '-v', '--verbose',
                nargs='?', action=utils.VerboseAction, help='verbose output')
        return parser
    return init_parser


def main():
    parser = argparse.ArgumentParser()
    init_parser = parser_factory(
        parser.add_subparsers(help='sub-command help'))

    parser_init = init_parser('init', cmd_init, help='init new config')
    parser_init.add_argument(
        '-l', '--local', action='store_true', help='init in cwd')
    parser_init.add_argument(
        '-p', '--profile', default='default', help='profile name')

    init_parser('info', cmd_info, help='get app info')
    init_parser('version', cmd_version, help='get app version')

    parser_config = init_parser('config', cmd_config, help='get config info')
    parser_config.add_argument(
        '-s', '--switch', help='config alias')
    parser_config.add_argument(
        '-v', '--verbose', action='store_true', help='show detailed info')

    init_parser(
        'total', cmd_total,
        args=('all', 'query_all', 'exclude'),
        help='get total info about all collected projects')

    parser_collect = init_parser(
        'collect', commands.cmd_collect,
        args=('query_all', 'exclude', 'force', 'verbose'),
        help='collect new projects')
    parser_collect.add_argument(
        '--update', action='store_true',
        help='update after collect')
    parser_collect.add_argument(
        '-S', '--skip-membership',
        action='store_true',
        help='skip membership check on project search')
    parser_collect.add_argument(
        '-n', '--no-store',
        action='store_true',
        help='only find and output')
    parser_collect.add_argument(
        '-l', '--limit', type=int, help='output limit')

    init_parser(
        'clear', cmd_clear,
        args=('query', 'exact', 'exclude', 'all', 'force'),
        help='clear projects from cache')

    init_parser(
        'show', cmd_show,
        args=('query', 'all', 'exact', 'exclude2', 'force'),
        help='show project info from cache')

    parser_requirements = init_parser(
        'reqs', cmd_requirements,
        args=('query', 'exact', 'all', 'quiet'),
        help='show project info from cache')
    parser_requirements.add_argument('-i', '--index-url', help='show all info')

    init_parser(
        'reverse', cmd_reverse,
        args=('query', 'exact', 'force', 'quiet'),
        help='main feature! get list of packages, requiring this one')

    parser_repair = init_parser(
        'repair', cmd_repair,
        args=('exact', 'exclude', 'all', 'force'),
        help='retrieve data from gitlab if missing something',
    )
    parser_repair.add_argument(
        'query', nargs='?', default=':broken', help='project name/path/tag')

    parser_update = init_parser(
        'update', cmd_update,
        aliases=('up',),
        args=('exact', 'exclude', 'all', 'force'),
        help='retrieve data from gitlab if missing something',
    )
    parser_update.add_argument(
        'query', nargs='?', default=':outdated', help='project name/path/tag')

    parser_list = init_parser(
        'list', cmd_list,
        args=('exact', 'exclude', 'quiet'),
        help='list cached projects')
    parser_list.add_argument(
        'query', nargs='?', default=None, help='project name/path/tag')
    parser_list.add_argument(
        '-t', '--total', action='store_true',
        help='print only total on filter')
    parser_list.add_argument('-l', '--limit', type=int, help='output limit')

    parser_cat = init_parser(
        'cat', cmd_cat,
        args=('query', 'all', 'exact', 'exclude2'),
        help='cat')
    parser_cat.add_argument('file', type=str, help='file path to cat')

    namespace = parser.parse_args()
    if getattr(namespace, 'func', None):
        try:
            return namespace.func(namespace)
        except KeyboardInterrupt:
            return log.error('Interrupted')
        except errors.Abort:
            return
        except errors.Client as exc:
            return log.catch(exc)
        except Exception:
            return log.exception('Unhandled exception')

    parser.print_help()


if __name__ == '__main__':
    main()
