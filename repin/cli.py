#!/usr/bin/env python3
import argparse
import base64
import configparser
import logging
import os
import pprint
import shutil

import gitlab
import Levenshtein
import toml.decoder
import yaml
import yaml.parser
import yaml.representer

from . import __version__, cli_utils, collectors

CLR_FAIL = '\033[91m'
CLR_WARNING = '\033[93m'
CLR_OKGREEN = '\033[92m'
CLR_END = '\033[0m'
GL_PER_PAGE = 100
CONFIG_DIR = '.repin'
CACHE_FILE_NAME = '.repin-cache'
CACHE_FILE_BACK_NAME = '.repin-cache-back'
CONFIG_FILE_NAME = '.python-gitlab.cfg'
CONFIG_TEMPLATE = """[global]
default = {default}
ssl_verify = true
timeout = 10

[{default}]
api_version = 4
url = {url}
private_token = {token}
"""
REQS = (
    'setup.py',
    'requirements.txt',
    'reqs.txt',
    'Pipfile',
    'requirements/prod.txt',
    'requirements/live.txt',
)


class AppError(Exception):
    pass


def error(message):
    print(CLR_FAIL + message + CLR_END)


def warn(message):
    print(CLR_WARNING + message + CLR_END)


def success(message):
    print(CLR_OKGREEN + message + CLR_END)


def cmd_init(namespace):
    if namespace.local and namespace.user:
        return error('can not use local and user flags together')
    elif namespace.user:
        for_user = 'y'
    elif namespace.local:
        for_user = 'n'
    else:
        for_user = input(
            'init for a user or local path (y - for user)?  [y]/n ')

    path = '.' if for_user == 'n' else os.path.expanduser('~')

    base_path = os.path.abspath(os.path.join(path, CONFIG_DIR))
    if not os.path.isdir(base_path):
        os.makedirs(base_path)

    config_file = os.path.join(base_path, CONFIG_FILE_NAME)
    if os.path.isfile(config_file):
        config = configparser.ConfigParser()
        config.read(config_file)
        if config.has_option(namespace.config, 'url'):
            return warn('Already exists')

    url = input('url: ')
    token = input('token: ')

    if os.path.isfile(config_file):
        config = configparser.ConfigParser()
        config.read(config_file)
        config.add_section(namespace.config)
        config.set(namespace.config, 'url', url)
        config.set(namespace.config, 'private_token', token)
        config.set(namespace.config, 'api_version', '4')
        _save_config(config)
    else:
        with open(config_file, 'w') as f:
            f.write(CONFIG_TEMPLATE.format(
                default=namespace.config, url=url, token=token))

    success('Inited')


def _save_config(config):
    base_path = get_config_dir()
    config_file = os.path.join(base_path, CONFIG_FILE_NAME)
    with open(config_file, 'w') as file:
        config.write(file)


def cmd_info(namespace):
    success('version: {}'.format(__version__))
    success('available filters:')
    for f in cli_utils.FILTERS.keys():
        print(f)


def _get_current_config():
    config = get_config()
    return config.get('global', 'default')


def cmd_config(namespace):
    config = get_config()

    if namespace.switch:
        if not config.has_option(namespace.switch, 'url'):
            return error('Invalid config')
        if config.get('global', 'default') == namespace.switch:
            return warn('Config already set to {}'.format(namespace.switch))
        config.set('global', 'default', namespace.switch)
        _save_config(config)
        success('set config to {}'.format(namespace.switch))
        return

    print('Config root: {}'.format(get_config_dir()))
    print('Current config: {}'.format(config['global'].get('default')))
    print('Avaliable configs:')
    for key, opt in config.items():
        if key not in ('DEFAULT', 'global'):
            print(' ', key, opt.get('url'))


def cmd_total(namespace):
    if namespace.query:
        cached_search, project_cache = filter_cache(
            namespace.query, False)
    else:
        cached_search = get_project_data() or {}

    counts = {}

    for pid, cached in cached_search.items():
        for filter_tag, filter_ in cli_utils.FILTERS.items():
            counts.setdefault(filter_tag, 0)
            if filter_(cached):
                counts[filter_tag] += 1

    for filter_tag, count in counts.items():
        if namespace.all or count:
            if count and filter_tag == ':broken':
                error('{}: {}'.format(filter_tag, count))
            elif count and filter_tag.startswith('na:'):
                warn('{}: {}'.format(filter_tag, count))
            else:
                success('{}: {}'.format(filter_tag, count))


def get_config_dir():
    paths = (
        os.path.abspath(os.path.join('.', CONFIG_DIR)),  # local
        os.path.abspath(os.path.join(os.path.expanduser('~'), CONFIG_DIR)),
        os.path.abspath(os.path.dirname(__file__)),  # global
    )
    for path in paths:
        if os.path.isfile(os.path.join(path, CONFIG_FILE_NAME)):
            return path
    raise AppError('Missing config. Make `init`.')


def get_project_data():
    base_path = os.path.join(get_config_dir(), _get_current_config())

    if not os.path.exists(base_path):
        return None

    try:
        with open(os.path.join(base_path, CACHE_FILE_NAME), 'r') as f:
            return yaml.load(f)
    except yaml.parser.ParserError:
        backup = os.path.join(base_path, CACHE_FILE_BACK_NAME)
        if os.path.exists(backup):
            shutil.move(
                backup,
                os.path.join(base_path, CACHE_FILE_NAME),
            )
            return get_project_data()
        raise
    except IOError:
        return None


def save_project_data(data):
    base_path = os.path.join(get_config_dir(), _get_current_config())

    if not os.path.exists(base_path):
        os.mkdir(base_path)

    if os.path.exists(os.path.join(base_path, CACHE_FILE_NAME)):
        shutil.copy(
            os.path.join(base_path, CACHE_FILE_NAME),
            os.path.join(base_path, CACHE_FILE_BACK_NAME))

    for sub in toml.decoder.InlineTableDict.__subclasses__():
        yaml.add_representer(
            sub, yaml.representer.SafeRepresenter.represent_dict)

    try:
        with open(os.path.join(base_path, CACHE_FILE_NAME), 'w') as f:
            yaml.dump(data, f)
    except yaml.representer.RepresenterError:
        shutil.copy(
            os.path.join(base_path, CACHE_FILE_BACK_NAME),
            os.path.join(base_path, CACHE_FILE_NAME))
        raise


def get_api():
    base_path = get_config_dir()
    return gitlab.Gitlab.from_config(_get_current_config(), [
        os.path.join(base_path, CONFIG_FILE_NAME)
    ])


def get_config():
    base_path = get_config_dir()
    parser = configparser.ConfigParser()
    parser.read(os.path.join(base_path, CONFIG_FILE_NAME))
    return parser


def add_cache(project, project_cache=None, force=False, save=True, fast=False):
    if project_cache is None:
        project_cache = get_project_data() or {}

    cached = project_cache.setdefault(project.id, {})

    if not fast:
        collectors.collect(project, cached, force)

    cached.update({
        'name': project.name,
        'path': '{}/{}'.format(project.namespace['full_path'], project.path),
        'created_at': project.created_at,
        'last_activity_at': project.last_activity_at,
        'web_url': project.web_url,
        'archived': project.archived,
    })

    if save:
        save_project_data(project_cache)

    return cached


def fix_cache(gl, project_cache, pid, cached, force):
    force = force or cli_utils.filter_is_broken(cached)
    if not force:
        warn('{}: not broken'.format(cached['name']))
        return False

    try:
        project = gl.projects.get(pid)
    except gitlab.exceptions.GitlabGetError:
        error('{}: missing'.format(cached.get('name') or pid))
        return False

    add_cache(project, project_cache, force=force, save=False)

    if cli_utils.filter_is_broken(cached):
        error('{}: package not fixed'.format(cached.get('name') or pid))
        return False

    success('{}: package fixed'.format(cached['name']))
    return True


def cmd_collect(namespace):
    gl = get_api()
    project_cache = get_project_data() or {}
    # TODO:
    list_options = {
        # 'visibility': 'private',
        # 'membership': True,
    }

    if namespace.exclude != ':archived' and ':' in namespace.exclude:
        return error('Collect cant use filters in exclude')

    if namespace.fast and namespace.force:
        return error('Cannot use --fast with --force')

    if namespace.query == ':all':
        page = 1
        list_options['per_page'] = GL_PER_PAGE
        projects = gl.projects.list(page=page, **list_options)
        interrupted = False
        while projects:
            for index, project in enumerate(projects):
                if namespace.exclude and namespace.exclude in '{}/{}'.format(
                        project.namespace['full_path'], project.path):
                    continue
                print('{}: collecting... ({})'.format(
                    project.name, index + 1 + (page - 1) * GL_PER_PAGE))
                try:
                    add_cache(
                        project,
                        project_cache,
                        force=namespace.force,
                        save=False,
                        fast=namespace.fast,
                    )
                except KeyboardInterrupt:
                    warn('Interrupted')
                    interrupted = True
                    break
                # TODO: save on finally

            save_project_data(project_cache)

            if interrupted:
                break

            page += 1
            projects = gl.projects.list(page=page, **list_options)

    elif ':' in namespace.query:
        return error('Collect cant use filters beside :all')

    else:
        projects = gl.projects.list(search=namespace.query, **list_options)
        for index, project in enumerate(projects):
            warn('{}: collecting... ({})'.format(project.name, index + 1))
            cache = add_cache(
                project,
                project_cache,
                force=namespace.force,
                save=True,
                fast=namespace.fast,
            )
            if len(projects) == 1:
                pprint.pprint(cache)

    success('total {}'.format(len(project_cache)))


def _limit_str(value, limit):
    if len(value) <= limit:
        return value
    return value[:limit - 3] + '...'


def cmd_repair(namespace):
    gl = get_api()
    cached_search, project_cache = filter_cache(
        namespace.query, namespace.exact, namespace.exclude)

    if not cached_search:
        return error('Nothing found')

    if (namespace.query != ':broken'
            and not namespace.all
            and len(cached_search) > 1):
        warn('Found {}: {}'.format(len(cached_search), _limit_str(', '.join(
            cached['path'] for cached in cached_search.values()
        ), 100)))
        warn('Use --all to repair them all.')
        return

    fixed = 0
    for i, (pid, cached) in enumerate(cached_search.items()):
        try:
            fix_result = fix_cache(
                gl, project_cache, pid, cached, namespace.force)
        except KeyboardInterrupt:
            warn('Interrupted')
            break
        except Exception:
            logging.exception(
                '{}: package fix failed'.format(cached.get('name') or pid))
            continue

        if fix_result:
            fixed += 1

        if not i % 10:
            save_project_data(project_cache)

    save_project_data(project_cache)
    warn('Fixed: {}, Found: {}, Total: {}'.format(
        fixed, len(cached_search), len(project_cache)))


def _parse_query(query, exact=False, mode=all, mode_inverse=any):
    if '.' in query and ',' not in query:
        mode = mode_inverse
        query = query.replace('.', ',')

    key = 'path' if '/' in query else 'name'
    if ':' in query:
        query = query.split(',')
        filters = [cli_utils.FILTERS.get(sub) for sub in query]
        if not all(filters):
            warn('Unknown filter: {}'.format(', '.join(
                sub for sub in query if sub not in cli_utils.FILTERS)))
            return None
        return lambda cached: mode(sub(cached) for sub in filters)
    elif exact:
        return lambda c: query == c.get(key)
    return lambda c: query in c.get(key, '')


def filter_cache(query, exact, exclude=None):
    if query == exclude:
        exclude = ':none'

    project_cache = get_project_data() or {}

    filter_finally = filter_ = _parse_query(query, exact, all, any)
    if not filter_:
        return {}, project_cache

    if exclude:
        exclude = _parse_query(exclude, False, any, all)
        if not exclude:
            return {}, project_cache
        filter_finally = lambda c: filter_(c) and not exclude(c)

    return {
        pid: cached for pid, cached in project_cache.items()
        if filter_finally(cached)
    }, project_cache


def cmd_clear(namespace):
    cached_search, project_cache = filter_cache(
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
        del project_cache[name]
    save_project_data(project_cache)


def cmd_list(namespace):
    cached_search, project_cache = filter_cache(
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
                len(cached_search), len(project_cache)))


def cmd_show(namespace):
    cached_search, project_cache = filter_cache(
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
    cached_search, project_cache = filter_cache(
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
    cached_search, project_cache = filter_cache(
        namespace.query, namespace.exact)

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
    cached_search, project_cache = filter_cache(namespace.query, True)

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
    for pid, project in project_cache.items():
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

    PROJECT_NAME_LEN = 60
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
        '-u', '--user', action='store_true', help='init user root')
    parser_init.add_argument(
        '-c', '--config', default='default', help='config alias')

    init_parser('info', cmd_info, help='get app info')

    parser_config = init_parser('config', cmd_config, help='get config info')
    parser_config.add_argument(
        '-s', '--switch', help='config alias')

    parser_total = init_parser(
        'total', cmd_total,
        args=('all',),
        help='get total info about all collected projects')
    parser_total.add_argument('-q', '--query', help='project name/path/tag')

    parser_collect = init_parser(
        'collect', cmd_collect,
        args=('query', 'exclude', 'force'),
        help='collect new projects')
    parser_collect.add_argument(
        '-f', '--fast', action='store_true',
        help='fast mode, dont do additional API calls')

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

    init_parser(
        'repair', cmd_repair,
        args=('query', 'exact', 'exclude', 'all', 'force'),
        help='retrieve data from gitlab if missing something',
    )

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
        except AppError as e:
            return error(e.args[0])
        except Exception as e:
            logging.exception('!!!')
            return error(e.args[0])

    parser.print_help()


if __name__ == '__main__':
    main()
