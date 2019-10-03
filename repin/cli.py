#!/usr/bin/env python3
import argparse
import base64
import concurrent.futures
import datetime
import getpass
import logging
import pprint

import gitlab
import Levenshtein

from . import __version__, cli_utils, collectors, errors
from .cache import cache
from .config import config

CLR_FAIL = '\033[91m'
CLR_WARNING = '\033[93m'
CLR_OKGREEN = '\033[92m'
CLR_END = '\033[0m'
GL_PER_PAGE = 100
PROJECT_NAME_LEN = 60


def error(message):
    print(CLR_FAIL + message + CLR_END)


def warn(message):
    print(CLR_WARNING + message + CLR_END)


def success(message):
    print(CLR_OKGREEN + message + CLR_END)


def cmd_info(__):
    success('version: {}'.format(__version__))
    success('available filters:')
    for f in cli_utils.FILTERS.keys():
        print(f)


def cmd_version(__):
    print('Repin {}'.format(__version__))


def cmd_init(namespace):
    config.prepare('.' if namespace.local else '~')

    if config.has_profile(namespace.profile):
        return warn('profile `{}` already exists'.format(namespace.profile))

    url = input('url: ')
    token = getpass.getpass('token: ')

    config.add_profile(namespace.profile, url, token)
    config.switch_profile(namespace.profile)
    config.flush()
    success('inited')


def cmd_config(namespace):
    config.load()

    if namespace.switch:
        config.switch_profile(namespace.switch)
        config.flush()
        success('set config to {}'.format(namespace.switch))
        return

    print('Config root: {}'.format(config.root))
    print('Profile: {}'.format(config.current_profile()))
    if namespace.verbose:
        print('Available profiles:')
        for profile, url in config.iter_profiles():
            print(' ', profile, url)


def cmd_total(namespace):
    config.load()

    if namespace.all:
        namespace.exclude = ':none'

    cached_search = cache.filter_map(namespace.query, False, namespace.exclude)

    counts = {}

    for pid, cached in cached_search.items():
        for filter_tag, filter_ in cli_utils.FILTERS.items():
            counts.setdefault(filter_tag, 0)
            if filter_(cached):
                counts[filter_tag] += 1

    max_name = 1
    for filter_tag in cli_utils.FILTERS.keys():
        max_name = max(max_name, len(filter_tag) + 1)

    for filter_tag, count in counts.items():
        filter_tag_pad = filter_tag.ljust(max_name)
        if namespace.all or count:
            if count and filter_tag == ':broken':
                error('{} {}'.format(filter_tag_pad, count))
            elif count and cli_utils.tag_is_warn(filter_tag):
                warn('{} {}'.format(filter_tag_pad, count))
            else:
                success('{} {}'.format(filter_tag_pad, count))


def get_api():
    return gitlab.Gitlab.from_config(config.current_profile(), [
        config.path
    ])


def add_cache(project, force=False, save=True, update=True):
    cached = cache.update(project.id, {
        'name': project.name,
        'path': '{}/{}'.format(project.namespace['full_path'], project.path),
        'created_at': project.created_at,
        'last_activity_at': project.last_activity_at,
        'web_url': project.web_url,
        'archived': project.archived,
        ':last_update_at': datetime.datetime.now(),
    })

    if update:
        collected = collectors.collect(project, cached, force)
        cache.update(project.id, collected)

    if save:
        cache.flush()

    return cached


def fix_cache(gl, pid, cached, force, default):
    force = force or cli_utils.filter_is(default, cached)
    if not force:
        warn('{}: not {}'.format(cached['name'], default))
        return False

    try:
        project = gl.projects.get(pid)
    except gitlab.exceptions.GitlabGetError:
        error('{}: missing'.format(cached.get('name') or pid))
        return False

    add_cache(project, force=force, save=False)

    if cli_utils.filter_is_broken(cached):
        error('{}: package not updated'.format(cached.get('name') or pid))
        return False

    success('{}: package updated'.format(cached['name']))
    return True


def cmd_collect(namespace):
    config.load()
    gl = get_api()

    # TODO:
    list_options = {
        # 'visibility': 'private',
        'membership': True,
    }

    if namespace.exclude != ':archived' and ':' in namespace.exclude:
        return error('Collect cant use filters in exclude')

    if namespace.query == ':all':
        list_options['per_page'] = GL_PER_PAGE
        projects = gl.projects.list(as_list=False, **list_options)
        print(projects.total_pages)
        print(projects.total)
        for index, project in enumerate(projects):
            if namespace.exclude and namespace.exclude in '{}/{}'.format(
                    project.namespace['full_path'], project.path):
                continue
            print('{} ({}/{})'.format(project.name, index + 1, projects.total))
            try:
                add_cache(
                    project,
                    force=namespace.force,
                    save=False,
                    update=namespace.update,
                )
            except KeyboardInterrupt:
                warn('Interrupted')
                break
            # TODO: save on finally

        cache.flush()

    elif ':' in namespace.query:
        return error('Collect cant use filters beside :all')

    else:
        projects = gl.projects.list(search=namespace.query, **list_options)
        for index, project in enumerate(projects):
            warn('{}: collecting... ({})'.format(project.name, index + 1))
            cache_ = add_cache(
                project,
                force=namespace.force,
                save=False,
                update=namespace.update,
            )
            if len(projects) == 1:
                pprint.pprint(cache_)

            if not index % 10:
                cache.flush()
        cache.flush()

    success('total {}'.format(cache.total()))


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

    if not cached_search:
        return error('Nothing found')

    if (namespace.query != default
            and not namespace.all
            and len(cached_search) > 1):
        warn('Found {}: {}'.format(len(cached_search), _limit_str(', '.join(
            cached['path'] for cached in cached_search.values()
        ), 100)))
        warn('Use --all to repair them all.')
        return

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=20)

    fixed = 0

    tasks = {
        pool.submit(fix_cache, gl, pid, cached, namespace.force, default): pid
        for pid, cached in cached_search.items()}

    for i, feature in enumerate(concurrent.futures.as_completed(tasks)):
        pid = tasks[feature]

        try:
            update_result = feature.result()
        except Exception:
            logging.exception(
                '{}: package fix failed'.format(
                    cache.select(pid, {}).get('name') or pid))
            continue

        if update_result:
            fixed += 1

        if (fixed or update_result) and not i % 10:
            cache.flush()

    if fixed:
        cache.flush()

    warn('Fixed: {}, Found: {}, Total: {}'.format(
        fixed, len(cached_search), cache.total()))


def cmd_clear(namespace):
    config.load()

    cached_search = cache.filter_map(
        namespace.query, namespace.exact, namespace.exclude)

    if not cached_search:
        return error('Nothing found')

    if not namespace.all and len(cached_search) > 1:
        warn('Found {}: {}'.format(len(cached_search), _limit_str(', '.join(
            cached['path'] for cached in cached_search.values()
        ), 100)))
        warn('Use --all to clear them all.')
        return

    for name, cached in cached_search.items():
        cache.delete(name)
    cache.flush()


def cmd_list(namespace):
    config.load()

    cached_search = cache.filter_map(
        namespace.query, namespace.exact, namespace.exclude)
    if not namespace.total:
        line = 0
        for pid, cached in cached_search.items():
            if namespace.limit is not None and line >= namespace.limit:
                if not namespace.quiet and line:
                    warn('Remaining {}'.format(len(cached_search) - line))
                break
            line += 1
            print('{}'.format(cached.get('path') or cached.get('name') or pid))

    if not namespace.quiet:
        success(
            'Found: {}, Total: {}'.format(
                len(cached_search), cache.total()))


def cmd_show(namespace):
    config.load()

    cached_search = cache.filter_map(
        namespace.query, namespace.exact, namespace.exclude)

    if not cached_search:
        return error('Nothing found')
    if not namespace.all and len(cached_search) > 1:
        warn('Found: {}'.format(', '.join(
            cached.get('path') or cached.get('name') or pid
            for pid, cached in cached_search.items())))
        return

    for pid, cached in cached_search.items():
        if len(cached_search) > 1:
            success(cached['name'])

        tags = []
        for filter_tag, filter_ in cli_utils.FILTERS.items():
            if filter_(cached):
                tags.append(filter_tag)

        if tags:
            success('Tags: {}'.format(' '.join(tags)))
        else:
            warn('Package have no tags')

        if cli_utils.filter_is_broken(cached):
            warn('Package is broken, call `repair` to fix it.')

        if namespace.force:
            pprint.pprint(cached)


def cmd_cat(namespace):
    config.load()

    cached_search = cache.filter_map(
        namespace.query, namespace.exact, False)

    if not cached_search:
        return error('Nothing found')
    if not namespace.all and len(cached_search) > 1:
        warn('Found: {}'.format(', '.join(
            cached.get('path') or cached.get('name') or pid
            for pid, cached in cached_search.items())))
        return

    gl = get_api()
    for pid, cached in cached_search.items():
        try:
            project = gl.projects.get(pid)
        except gitlab.exceptions.GitlabGetError:
            error('{}: missing'.format(cached.get('name') or pid))
            continue

        try:
            file = project.files.get(
                file_path=namespace.file, ref='master')
        except gitlab.exceptions.GitlabGetError:
            continue
        print(base64.b64decode(file.content).decode())


def cmd_requirements(namespace):
    config.load()

    cached_search = cache.filter_map(namespace.query, namespace.exact)

    if not cached_search:
        if not namespace.quiet:
            return error('Nothing found')
    if not namespace.all and len(cached_search) > 1:
        if not namespace.quiet:
            warn('Found {}: {}'.format(len(cached_search), _limit_str(', '.join(
                cached['path'] for cached in cached_search.values()
            ), 100)))
            warn('Use --all to repair them all.')
        return

    for pid, cached in cached_search.items():
        if not namespace.quiet:
            success(cached['name'])
        if cli_utils.filter_is_broken(cached):
            if not namespace.quiet:
                warn('Package is broken, call `repair` to fix it.')
            continue
        if not cli_utils.filter_have_reqs(cached):
            if not namespace.quiet:
                warn('Package have no requirements')
            continue

        # TODO: build requirements tree

        if not namespace.quiet:
            success('package{}dep\tversion\tcomment'.format(
                ' ' * (32 - len('package'))))
        for req in cached[':requirements']['list']:
            req_line = req.strip()
            if req_line.startswith('# ') or req_line.startswith('--'):
                continue
            project_name, dep_mode, version, comment = \
                _split_requirement_package_version(req)
            if not project_name:
                continue
            print('{}{}{}\t{}\t# {}'.format(
                project_name,
                ' ' * (32 - len(project_name)),
                dep_mode,
                version,
                comment,
            ))


def cmd_reverse(namespace):
    config.load()

    cached_search = cache.filter_map(namespace.query, True)

    if not cached_search:
        if namespace.force:
            cached_search = {
                None: {'name': namespace.query, ':languages': {'Python': 100}}
            }
        else:
            return error('Nothing found')

    if len(cached_search) > 1:
        return warn('Found {}: {}'.format(len(cached_search), ', '.join(
            cached.get('path') or cached.get('name') or pid
            for pid, cached in cached_search.items()
        )))

    self_pid, cached = cached_search.popitem()

    if cli_utils.filter_is_package(cached):
        self_name = cached[':setup.py'].get('name', cached['name'])
    elif cli_utils.get_flit_metadata(cached).get('dist-name'):
        self_name = cli_utils.get_flit_metadata(cached)['dist-name']
    elif cli_utils.filter_lang_python(cached):
        self_name = cached['name']
    else:
        if namespace.force:
            self_name = cached['name']
        else:
            return error(
                '{}: is not a python package. Use --force to continue')

    dep_for = []
    dep_for_mb = {}
    for pid, project in cache.items():
        # skip self
        if pid == self_pid:
            continue
        # skip projects without requirements
        if not cli_utils.filter_have_reqs(project):
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
            success('Found reversed dependencies:')
            warn('version{}dep{}project{}comment'.format(
                ' ' * (max_ver - len('version')),
                ' ' * (max_dep - len('dep')),
                ' ' * (max_name - len('project')),
            ))
        for project_name, dep_mode, version, comment in dep_for:
            if namespace.quiet:
                print('{}{}{}'.format(
                    (version or 'latest').ljust(max_ver),
                    (dep_mode or '').ljust(max_dep),
                    project_name,
                ))
            else:
                print('{}{}{}# {}'.format(
                    (version or '*').ljust(max_ver),
                    (dep_mode or '').ljust(max_dep),
                    project_name.ljust(max_name),
                    # ' ' * (PROJECT_NAME_LEN - len(project_name)),
                    comment,
                ))
    elif not namespace.quiet:
        warn('No strict reversed dependencies found')

    if dep_for_mb:
        if not namespace.quiet:
            success('Found similar reversed dependencies:')
        for name, similar in dep_for_mb.items():
            if not namespace.quiet:
                success('{}: '.format(name))
            for project_name, dep_mode, version, comment in similar:
                warn('{}\t{}\t{}{}# {}'.format(
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
                '-v', '--verbose', action='store_true',
                help='verbose output')
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
        'collect', cmd_collect,
        args=('query_all', 'exclude', 'force'),
        help='collect new projects')
    parser_collect.add_argument(
        '--update', action='store_true',
        help='update after collect')

    init_parser(
        'clear', cmd_clear,
        args=('query', 'exact', 'exclude', 'all'),
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
        args=('query', 'exact', 'exclude', 'quiet'),
        help='list cached projects')
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
            return error('Interrupted')
        except errors.Error as exc:
            return error(exc.args[0])
        except errors.Warn as exc:
            return warn(exc.args[0])
        except Exception as e:
            logging.exception('!!!')
            return error(e.args[0])

    parser.print_help()


if __name__ == '__main__':
    main()
