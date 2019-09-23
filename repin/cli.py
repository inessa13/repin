#!/usr/bin/env python3
import argparse
import base64
import logging
import os
import pprint
import re
import shutil

import gitlab
import Levenshtein
import mock
import toml
import yaml

from . import __version__, cli_utils

CLR_END = '\033[0m'
GL_PER_PAGE = 50
CONFIG_DIR = '.repin'
CACHE_FILE_NAME = '.repin-cache'
CACHE_FILE_BACK_NAME = '.repin-cache-back'
CONFIG_FILE_NAME = '.python-gitlab.cfg'
CONFIG_TEMPLATE = """[global]
default = default
ssl_verify = true
timeout = 10

[default]
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
    CLR_FAIL = '\033[91m'
    print(CLR_FAIL + message + CLR_END)


def warn(message):
    CLR_WARNING = '\033[93m'
    print(CLR_WARNING + message + CLR_END)


def success(message):
    CLR_OKGREEN = '\033[92m'
    print(CLR_OKGREEN + message + CLR_END)


def cmd_init(namespace):
    for_user = input('init for a user or local path (y - for user)?  [y]/n ')
    path = '.' if for_user == 'n' else os.path.expanduser('~')

    base_path = os.path.abspath(os.path.join(path, CONFIG_DIR))
    if not os.path.isdir(base_path):
        os.makedirs(base_path)

    config_file = os.path.join(base_path, CONFIG_FILE_NAME)
    if os.path.isfile(config_file):
        return warn('Already inited')

    url = input('url: ')
    token = input('token: ')
    with open(config_file, 'w') as f:
        f.write(CONFIG_TEMPLATE.format(url=url, token=token))

    success('Inited')


def cmd_info(namespace):
    success('version: {}'.format(__version__))
    success('config root: {}'.format(get_config_dir()))
    success('available filters:')
    for f in cli_utils.FILTERS.keys():
        print(f)


def cmd_total(namespace):
    project_cache = get_project_data() or {}
    counts = {}
    for pid, cached in project_cache.items():
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
    import yaml.parser
    base_path = get_config_dir()
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
    base_path = get_config_dir()
    if os.path.exists(os.path.join(base_path, CACHE_FILE_NAME)):
        shutil.copy(
            os.path.join(base_path, CACHE_FILE_NAME),
            os.path.join(base_path, CACHE_FILE_BACK_NAME))
    with open(os.path.join(base_path, CACHE_FILE_NAME), 'w') as f:
        yaml.dump(data, f)


def get_api():
    base_path = get_config_dir()
    return gitlab.Gitlab.from_config('default', [
        os.path.join(base_path, CONFIG_FILE_NAME)
    ])


def load_python_module(project, path):
    try:
        version_file = project.files.get(file_path=path, ref='master')
    except gitlab.exceptions.GitlabGetError:
        try:
            version_file = project.files.get(
                file_path=path + '/__init__.py', ref='master')
        except gitlab.exceptions.GitlabGetError:
            return None

    locals_ = {}
    file_content = base64.b64decode(version_file.content).decode()
    try:
        exec(file_content, globals(), locals_)
    except KeyboardInterrupt:
        raise
    except:
        return 'n/a'

    return type('PythonModule', (), locals_)


def parse_setup(project, cached):
    file_info = project.files.get(file_path='setup.py', ref='master')
    raw = base64.b64decode(file_info.content).decode()
    content = raw.split('\n')

    class f:
        def __init__(self, *args, **kwargs):
            pass

        def read(self, n=0):
            return ''

    setup_result = {}
    setup_locals = {
        'setup': lambda **kw: setup_result.update(**kw),
        'find_packages': lambda *a, **kw: None,
        'open': f,
    }

    eval_content = []
    for line in content:
        if re.match('from setuptools import', line):
            continue

        m = re.match(r'import\s+([._\w]+)(:?\s+as\s+([.\w]+))?', line)
        if m:
            version_path = m.group(1).replace('.', '/')
            try:
                version = load_python_module(project, version_path)
            except KeyboardInterrupt:
                raise
            except:
                logging.exception('setup.py parse failed')
                cached['package_data'] = 'n/a'
                return cached

            if version:
                if m.group(3):
                    setup_locals[m.group(3)] = version
                elif '.' not in m.group(1):
                    setup_locals[m.group(1)] = version
                else:
                    path = list(reversed(m.group(1).split('.')))
                    part1 = path.pop()
                    dx = setup_locals[part1] = mock.Mock()
                    while len(path) > 1:
                        part = path.pop()
                        setattr(dx, part, mock.Mock())
                        dx = getattr(dx, part, None)
                    part_last = path.pop()
                    setattr(dx, part_last, version)
                continue

        eval_content.append(line)

    try:
        exec('\n'.join(eval_content), globals(), setup_locals)
    except KeyboardInterrupt:
        raise
    except:
        logging.exception('setup.py parse failed')
        cached['package_data'] = 'n/a'
        return cached

    if setup_result:
        cached['package_data'] = setup_result
    else:
        cached['package_data'] = 'n/a'
    return cached


def parse_requirements(project, cached, name):
    file_info = project.files.get(file_path=name, ref='master')
    raw = base64.b64decode(file_info.content).decode()

    cached['package_data'] = {
        'install_requires': [l for l in raw.split('\n') if l],
    }
    return cached


def parse_pipfile_req(package, data):
    if data == '*':
        return package

    elif isinstance(data, str):
        # TODO:
        if ',' in data:
            data = data.split(',', 1)[0]

        m = re.match("(>|<|>=|==|<=|~=)([\w\d.]+)$", data)
        if m:
            return package + data

    elif isinstance(data, dict) and data.get('version'):
        return package + data

    else:
        # TODO:
        return package


def parse_pipfile(project, cached):
    file_info = project.files.get(file_path='Pipfile', ref='master')
    raw = base64.b64decode(file_info.content).decode()

    pipfile = toml.loads(raw)
    if pipfile:
        cached.setdefault('package_data', {})

    if pipfile.get('packages'):
        install_requires = [
            parse_pipfile_req(package, value)
            for package, value in pipfile['packages'].items()
        ]
        if install_requires:
            cached['package_data']['install_requires'] = install_requires

    return cached


def _collect_languages(project, cached):
    try:
        languages_data = project.languages()
    except KeyboardInterrupt:
        raise
    except gitlab.exceptions.GitlabGetError as e:
        if e.response_code == 500:
            languages_data = {}
        else:
            logging.exception('collect_languages failed')
            languages_data = 'n/a'
    except:
        logging.exception('collect_languages failed')
        languages_data = 'n/a'

    cached[':languages'] = languages_data


def _collect_req_sources(project, cached):
    req_sources = []
    for file in REQS:
        try:
            project.files.get(file_path=file, ref='master')
            req_sources.append(file)
        except gitlab.exceptions.GitlabGetError:
            pass
        except gitlab.exceptions.GitlabError:
            req_sources = 'n/a'
            break

    cached['req_sources'] = req_sources
    if not cli_utils.unknown_value(req_sources):
        if 'setup.py' in cached['req_sources']:
            parse_setup(project, cached)
        elif 'reqs.txt' in cached['req_sources']:
            parse_requirements(project, cached, 'reqs.txt')
        elif 'requirements.txt' in cached['req_sources']:
            parse_requirements(project, cached, 'requirements.txt')
        elif 'requirements/prod.txt' in cached['req_sources']:
            parse_requirements(project, cached, 'requirements/prod.txt')
        elif 'requirements/live.txt' in cached['req_sources']:
            parse_requirements(project, cached, 'requirements/live.txt')
        elif 'Pipfile' in cached['req_sources']:
            parse_pipfile(project, cached)


def collect_file_data(filename, cache_key):
    def decorator(func):
        def wrap(project, cached):
            try:
                file = project.files.get(file_path=filename, ref='master')
            except gitlab.exceptions.GitlabGetError:
                cached[cache_key] = False
                return
            except gitlab.exceptions.GitlabError:
                cached[cache_key] = 'n/a'
                return

            data = {'file': filename}
            raw = base64.b64decode(file.content).decode()
            if func(data, raw):
                cached[cache_key] = data
        return wrap
    return decorator


@collect_file_data('Dockerfile', 'docker_data')
def _collect_dockerfile(data, raw_content):
    for line in raw_content.split('\n'):
        m = re.match('ENTRYPOINT\s+\[(\'|")(.*)\\1\]$', line)
        if m:
            data['entrypoint'] = m.group(2)
        m = re.match('CMD\s+\[(\'|")(.*)\\1\]$', line)
        if m:
            data['cmd'] = '{}{}{}'.format(m.group(1), m.group(2), m.group(1))
    return True


@collect_file_data('.gitlab-ci.yml', 'gitlab_ci_data')
def _collect_gitlab_ci(data, raw_content):
    if 'nexus' in raw_content:
        data['nexus'] = 'mentioned'
    return True


def add_cache(project, project_cache=None, force=False, save=True, fast=False):
    project_cache = project_cache or get_project_data() or {}

    cached = project_cache.setdefault(project.id, {})

    if not fast:
        if force or cli_utils.unknown_value(cached.get(':languages')):
            _collect_languages(project, cached)

        if force or cli_utils.unknown_value(cached.get('docker_data')):
            _collect_dockerfile(project, cached)

        if force or cli_utils.unknown_value(cached.get('gitlab_ci_data')):
            _collect_gitlab_ci(project, cached)

        if cli_utils.filter_lang_python(cached):
            if force or cli_utils.unknown_value(cached.get('req_sources')):
                _collect_req_sources(project, cached)

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
    if not force and not cli_utils.filter_is_broken(cached):
        warn('{}: not broken'.format(cached['name']))
        return False

    try:
        project = gl.projects.get(pid)
    except gitlab.exceptions.GitlabGetError:
        error('{}: missing'.format(cached.get('name') or pid))
        return False

    add_cache(project, project_cache, force=force, save=True)

    if cli_utils.filter_is_broken(cached):
        error('{}: package not fixed'.format(cached.get('name') or pid))
        return False

    success('{}: package fixed'.format(cached['name']))
    return True


def cmd_collect(namespace):
    gl = get_api()
    project_cache = get_project_data() or {}
    list_options = {
        'membership': True,
    }

    if ':' in namespace.exclude:
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
                warn('{}: collecting... ({})'.format(
                    project.name, index + 1 + (page - 1) * GL_PER_PAGE))
                try:
                    add_cache(
                        project,
                        project_cache,
                        force=namespace.force,
                        save=True,
                        fast=namespace.fast,
                    )
                except KeyboardInterrupt:
                    warn('Interrupted')
                    interrupted = True
                    break
                # TODO: save on finally
            if interrupted:
                break

            page += 1
            projects = gl.projects.list(page=page, **list_options)

    elif ':' in namespace.query:
        return error('Collect cant use filters beside :all')

    else:
        projects = gl.projects.list(search=namespace.query)
        if len(projects) == 1:
            project = projects[0]
            warn('{}: collecting...'.format(project.name))
            cached = add_cache(project, project_cache, force=namespace.force, save=True)
            pprint.pprint(cached)
        else:
            for index, project in enumerate(projects):
                warn('{}: collecting... ({})'.format(project.name, index + 1))
                add_cache(project, project_cache, force=namespace.force, save=True)

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
    for pid, cached in cached_search.items():
        try:
            fix_result = fix_cache(
                gl, project_cache, pid, cached, namespace.force)
        except KeyboardInterrupt:
            warn('Interrupted')
            break

        if fix_result:
            fixed += 1

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
            warn('Unknown filter: {}'.format(
                ', '.join(sub for sub in query if sub not in cli_utils.FILTERS)))
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
                if line:
                    warn('Remaining {}'.format(len(cached_search) - line))
                break
            line += 1
            print('{}'.format(cached.get('path') or cached.get('name') or pid))
    success('Found: {}, Total: {}'.format(len(cached_search), len(project_cache)))


def cmd_show(namespace):
    cached_search, project_cache = filter_cache(namespace.query, True)

    if not cached_search:
        return error('Nothing found')
    if len(cached_search) > 1:
        warn('Found: {}'.format(', '.join(cached.get('path') or cached.get('name') or pid for pid, cached in cached_search.items())))

    pid, cached = cached_search.popitem()

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

    if namespace.all:
        pprint.pprint(cached)


def cmd_requirements(namespace):
    cached_search, project_cache = filter_cache(
        namespace.query, namespace.exact)

    if not cached_search:
        return error('Nothing found')
    if not namespace.all and len(cached_search) > 1:
        warn('Found {}: {}'.format(len(cached_search), _limit_str(', '.join(
            cached['path'] for cached in cached_search.values()
        ), 100)))
        warn('Use --all to repair them all.')
        return

    for pid, cached in cached_search.items():
        success(cached['name'])
        if cli_utils.filter_is_broken(cached):
            warn('Package is broken, call `repair` to fix it.')
            continue
        if not cli_utils.filter_have_reqs(cached):
            warn('Package have no requirements')
            continue

        # TODO: build requirements tree

        success(
            'package{}dep\tversion\tcomment'.format(' ' * (32 - len('package'))))
        for req in cached['package_data']['install_requires']:
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
        self_name = cached['package_data'].get('name', cached['name'])
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

        if project['package_data'].get('name'):
            project_name = '{} ({})'.format(project['package_data'].get('name'), project['path'])
        else:
            project_name = project['path']

        comment_addt = ''
        if project['archived']:
            comment_addt += ' :archived'

        for req in project['package_data']['install_requires']:
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
        success('Found reversed dependencies:')
        success('version\tdep\tproject{}comment'.format(' ' * (48 - len('project'))))
        for project_name, dep_mode, version, comment in dep_for:
            warn('{}\t{}\t{}{}# {}'.format(
                version, dep_mode, project_name,
                ' ' * (48 - len(project_name)),
                comment,
            ))
    else:
        warn('No strict reversed dependencies found')

    if dep_for_mb:
        warn('Found similar reversed dependencies:')
        for name, similar in dep_for_mb.items():
            success('{}: '.format(name))
            success('version\tdep\tproject')
            for project_name, dep_mode, version, comment in similar:
                success('{}\t{}\t{}{}# {}'.format(
                    version, dep_mode, project_name,
                    ' ' * (48 - len(project_name)),
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
        if 'all' in args:
            parser.add_argument(
                '-a', '--all',
                action='store_true', help='proceed with all found entries')
        if 'force' in args:
            parser.add_argument(
                '-F', '--force', action='store_true',
                help='force proceed')
        return parser
    return init_parser


def main():
    parser = argparse.ArgumentParser()
    init_parser = parser_factory(
        parser.add_subparsers(help='sub-command help'))

    init_parser('init', cmd_info, help='init new config')
    init_parser('info', cmd_info, help='get info (config/app)')
    init_parser(
        'total', cmd_total,
        args=('all',),
        help='get total info about all collected projects')

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
        args=('query', 'all'),
        help='show project info from cache')

    parser_requirements = init_parser(
        'reqs', cmd_requirements,
        args=('query', 'exact', 'all'),
        help='show project info from cache')
    parser_requirements.add_argument('-i', '--index-url', help='show all info')

    init_parser(
        'reverse', cmd_reverse,
        args=('query', 'exact', 'force'),
        help='main feature! get list of packages, requiring this one')

    init_parser(
        'repair', cmd_repair,
        args=('query', 'exact', 'exclude', 'all', 'force'),
        help='retrieve data from gitlab if missing something',
    )

    parser_list = init_parser(
        'list', cmd_list,
        args=('query', 'exact', 'exclude'),
        help='list cached projects')
    parser_list.add_argument(
        '-t', '--total', action='store_true',
        help='print only total on filter')
    parser_list.add_argument('-l', '--limit', type=int, help='output limit')

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
