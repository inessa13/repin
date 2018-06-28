#!/usr/bin/env python3
import argparse
import base64
import logging
import os
import pprint
import re

import gitlab
import Levenshtein
import mock
import toml
import yaml

from degitlab.cli_utils import (
    filter_is_broken,
    filter_is_no_python,
    filter_is_package,
    filter_is_package_na,
    filter_is_python,
    filter_is_python_unknown,
    filter_is_req_unknown,
    filter_is_requirer,
    filter_is_skipped,
    filter_is_type_lib,
    filter_is_type_service,
    filter_is_type_unknown,
    get_type_tag,
    unknown_value
)

CLR_END = '\033[0m'
GL_PER_PAGE = 10
MIN_PYTHON_PERCENT = 10
CONFIG_DIR = '.degitlab'
CACHE_FILE_NAME = '.degitlab-cache'
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


FILTERS = {
    ':all': lambda c: True,
    ':skipped': filter_is_skipped,
    'na:python': filter_is_python_unknown,
    'no:python': filter_is_no_python,
    ':broken': filter_is_broken,
    ':requirer': filter_is_requirer,
    ':python': filter_is_python,
    ':package': filter_is_package,
    ':req_unknown': filter_is_req_unknown,
    'na:package': filter_is_package_na,
    'type:service': filter_is_type_service,
    'type:lib': filter_is_type_lib,
    'type:unknown': filter_is_type_unknown,
}


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
    base_path = get_config_dir()
    success('root')
    print(base_path)

    success('available filters:')
    for f in FILTERS.keys():
        print(f)


def cmd_total(namespace):
    project_cache = get_project_data() or {}
    counts = {}
    for pid, cached in project_cache.items():
        for filter_tag, filter_ in FILTERS.items():
            counts.setdefault(filter_tag, 0)
            if filter_(cached):
                counts[filter_tag] += 1

    for filter_tag, count in counts.items():
        if namespace.all or count:
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
    base_path = get_config_dir()
    try:
        with open(os.path.join(base_path, CACHE_FILE_NAME), 'r') as f:
            return yaml.load(f)
    except IOError:
        return None


def save_project_data(data):
    base_path = get_config_dir()
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


def _load_python_percent(project):
    try:
        python_percent = project.languages().get('Python', 0)
    except KeyboardInterrupt:
        raise
    except gitlab.exceptions.GitlabGetError as e:
        if e.response_code == 500:
            python_percent = 0
        else:
            logging.exception('python_percent failed')
            python_percent = 'n/a'
    except:
        logging.exception('python_percent failed')
        python_percent = 'n/a'

    return python_percent


def _load_req_sources(project, cached):
    req_sources = []
    for file in ('setup.py', 'requirements.txt', 'reqs.txt', 'Pipfile'):
        try:
            project.files.get(file_path=file, ref='master')
            req_sources.append(file)
        except gitlab.exceptions.GitlabGetError:
            pass
        except gitlab.exceptions.GitlabError:
            req_sources = 'n/a'
            break

    cached['req_sources'] = req_sources or 'empty'


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
    return True


@collect_file_data('.gitlab-ci.yml', 'gitlab_ci_data')
def _collect_gitlab_ci(data, raw_content):
    if 'nexus' in raw_content:
        data['nexus'] = 'mentioned'
    return True


def add_cache(project, project_cache=None, force=False, save=True, fast=False):
    project_cache = project_cache or get_project_data() or {}

    cached = project_cache.setdefault(project.id, {})

    if not force and cached.get('skip'):
        return cached

    if not fast and unknown_value(cached.get('python_percent')):
        python_percent = _load_python_percent(project)
        if not force and isinstance(python_percent, float) and python_percent < MIN_PYTHON_PERCENT:
            cached['skip'] = True
            return cached

        cached['python_percent'] = python_percent

    if not fast and unknown_value(cached.get('req_sources')):
        _load_req_sources(project, cached)

    if not fast and (force or unknown_value(cached.get('docker_data'))):
        _collect_dockerfile(project, cached)

    if not fast and (force or unknown_value(cached.get('gitlab_ci_data'))):
        _collect_gitlab_ci(project, cached)

    cached.update({
        'name': project.name,
        'path': '{}/{}'.format(project.namespace['full_path'], project.path),
        'created_at': project.created_at,
        'last_activity_at': project.last_activity_at,
        'web_url': project.web_url,
    })

    if save:
        save_project_data(project_cache)

    return cached


def fix_skip(cached):
    if isinstance(cached.get('python_percent'), (float, int)) and cached['python_percent'] < MIN_PYTHON_PERCENT:
        cached['skip'] = True
    return cached


def fix_cache(gl, project_cache, pid, cached, force=False):
    if cached.get('skip'):
        return False

    name = cached.get('name') or pid

    fix_skip(cached)
    if cached.get('skip'):
        warn('{}: skip fixed'.format(name))
        save_project_data(project_cache)
        return True

    is_broken = filter_is_broken(cached)
    # if not _is_broken(cached):

    missing_package_data = unknown_value(cached.get('package_data'))
    if not is_broken and not missing_package_data:
        warn('{}: already repaired'.format(name))
        return False

    name = cached.get('name') or pid
    try:
        project = gl.projects.get(pid)
    except gitlab.exceptions.GitlabGetError:
        error('{}: unknown broken project'.format(
            cached.get('name') or pid))
        return False

    if is_broken:
        add_cache(project, project_cache, force=force)
        is_broken = filter_is_broken(cached)
        name = cached.get('name') or pid
        if not is_broken:
            fix_skip(cached)
            success('{}: package fixed'.format(name))
            save_project_data(project_cache)
            if cached.get('skip'):
                return True

    if not is_broken and missing_package_data:
        _collect_requirements(project, cached, force=force)
        missing_package_data = unknown_value(cached.get('package_data'))
        if not missing_package_data:
            success('{}: package data collected'.format(name))
            save_project_data(project_cache)

    if is_broken:
        error('{}: is broken yet'.format(name))
    if missing_package_data:
        error('{}: missing package_data yet'.format(name))

    return not is_broken and not missing_package_data


def _collect_requirements(project, cached, force=False):
    if force or unknown_value(cached.get('req_sources')):
        _load_req_sources(project, cached)

    if 'setup.py' in cached['req_sources']:
        cached = parse_setup(project, cached)
    elif 'reqs.txt' in cached['req_sources']:
        cached = parse_requirements(project, cached, 'reqs.txt')
    elif 'requirements.txt' in cached['req_sources']:
        cached = parse_requirements(project, cached, 'requirements.txt')
    elif 'Pipfile' in cached['req_sources']:
        cached = parse_pipfile(project, cached)
    else:
        return None
    return cached


def cmd_collect(namespace):
    gl = get_api()
    project_cache = get_project_data() or {}

    if namespace.fast and namespace.force:
        return error('Cannot use --fast with --force')

    if namespace.project == 'all':
        page = 1
        projects = gl.projects.list(page=page, per_page=GL_PER_PAGE)
        interrupted = False
        while projects:
            for index, project in enumerate(projects):
                warn('{}: collecting... ({})'.format(
                    project.name, index + 1 + (page - 1) * GL_PER_PAGE))
                try:
                    add_cache(project, project_cache, force=namespace.force, save=True, fast=namespace.fast)
                except KeyboardInterrupt:
                    warn('Interrupted')
                    interrupted = True
                    break
                # TODO: save on finally
            if interrupted:
                break

            page += 1
            projects = gl.projects.list(page=page, per_page=GL_PER_PAGE)

    else:
        projects = gl.projects.list(search=namespace.project)
        if len(projects) == 1:
            project = projects[0]
            warn('{}: collecting...'.format(project.name))
            cached = add_cache(project, project_cache, force=namespace.force, save=True)
            if _collect_requirements(project, cached, force=namespace.force):
                save_project_data(project_cache)
            pprint.pprint(cached)
        else:
            for index, project in enumerate(projects):
                warn('{}: collecting... ({})'.format(project.name, index + 1))
                add_cache(project, project_cache, force=namespace.force, save=True)

    success('total {}'.format(len(project_cache)))


def cmd_repair(namespace):
    gl = get_api()
    cached_search, project_cache = filter_cache(
        namespace.query, False, namespace.exact)

    if not cached_search:
        return error('Nothing found')

    if not namespace.all and len(cached_search) > 1:
        return warn('Found: {}'.format(', '.join(cached['path'] for cached in cached_search.values())))

    fixed = 0
    for pid, cached in cached_search.items():
        try:
            if fix_cache(gl, project_cache, pid, cached, force=namespace.force):
                fixed += 1
                success('{}: repaired'.format(cached.get('name') or pid))
        except KeyboardInterrupt:
            warn('Interrupted')
            break
        finally:
            save_project_data(project_cache)

    warn('Fixed: {}, Found: {}, Total: {}'.format(
        fixed, len(cached_search), len(project_cache)))


def filter_gen(query, path, exact):
    key = 'path' if path or '/' in query else 'name'

    def f(cached):
        filter_ = FILTERS.get(query)
        if callable(filter_):
            return filter_(cached)
        elif cached.get(key):
            if exact:
                return query == cached[key]
            return query in cached[key]
        else:
            return False
    return f


def filter_cache(query, path, exact, project_cache=None):
    project_cache = project_cache or get_project_data() or {}
    f = filter_gen(query, path, exact)
    return {
        pid: cached for pid, cached in project_cache.items() if f(cached)
    }, project_cache


def cmd_list(namespace):
    cached_search, project_cache = filter_cache(namespace.query, False, False)
    for pid, cached in cached_search.items():
        print('{}'.format(cached.get('path') or cached.get('name') or pid))
    success('Found: {}, Total: {}'.format(len(cached_search), len(project_cache)))


def cmd_show(namespace):
    cached_search, project_cache = filter_cache(namespace.query, False, True)

    if not cached_search:
        return error('Nothing found')
    if len(cached_search) > 1:
        warn('Found: {}'.format(', '.join(cached.get('path') or cached.get('name') or pid for pid, cached in cached_search.items())))

    pid, cached = cached_search.popitem()

    if cached.get('skip'):
        pprint.pprint(cached)
        return success('{}: is not a python package')

    if filter_is_python(cached):
        success(':python')
        success(get_type_tag(cached))
    if filter_is_package(cached):
        success(':package')
    if filter_is_broken(cached):
        warn(':broken')
    if filter_is_req_unknown(cached):
        warn(':req_unknown')
    if filter_is_requirer(cached):
        success(':requirer')
    else:
        error('Missing package data for project. Call `collect` on this project or for all projects.')

    if namespace.all:
        pprint.pprint(cached)


def cmd_reverse(namespace):
    cached_search, project_cache = filter_cache(namespace.query, False, True)

    if not cached_search:
        if namespace.force:
            cached_search = {None: {'name': namespace.query, 'python_percent': 100, 'req_sources': 'empty'}}
        else:
            return error('Nothing found')

    if len(cached_search) > 1:
        warn('Found: {}'.format(', '.join(cached.get('path') or cached.get('name') or pid for pid, cached in cached_search.items())))

    pid, cached = cached_search.popitem()

    if cached.get('skip'):
        return success('{}: is not a python package')

    if filter_is_package(cached):
        self_name = cached['package_data'].get('name', cached['name'])
    else:
        error('Missing package data for project. Call `collect` on this project or for all projects.')
        if namespace.force:
            self_name = cached['name']
        else:
            return

    dep_for = []
    dep_for_mb = {}
    for project in project_cache.values():
        # skip self
        if project.get('name') == cached['name']:
            continue
        # skip projects without requirements
        if not filter_is_requirer(project):
            continue

        if project['package_data'].get('name'):
            project_name = '{} ({})'.format(project['package_data'].get('name'), project['path'])
        else:
            project_name = project['path']

        for req in project['package_data']['install_requires']:
            reverse_name, dep_mode, version = _split_requirement_package_version(req)
            if reverse_name == self_name:
                dep_for.append((project_name, dep_mode, version))
            elif not namespace.exact and Levenshtein.distance(
                    reverse_name, self_name) <= 2:
                dep_for_mb.setdefault(reverse_name, []).append(
                    (project_name, dep_mode, version))

    if dep_for:
        success('Found reversed dependencies:')
        success('version\tdep_type\tproject')
        for project_name, dep_mode, version in dep_for:
            warn('{}\t{}\t{}'.format(version, dep_mode, project_name))
    else:
        warn('No strict reversed dependencies found')

    if dep_for_mb:
        warn('Found similar reversed dependencies:')
        for name, similar in dep_for_mb.items():
            success('{}: '.format(name))
            success('version\tdep_type\tproject')
            for project_name, dep_mode, version in similar:
                success('{}\t{}\t{}'.format(version, dep_mode, project_name))


def _split_requirement_package_version(req):
    if ',' in req:
        # TODO:
        req = req.split(',', 1)[0]

    for d in ('==', '~=', '>=', '>', '<'):
        if d in req:
            req = req.split(d, 1)
            req = req[0], d, req[1]
            break
    else:
        req = (req, None, None)
    return req


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(help='sub-command help')
    parser_init = subparsers.add_parser('init', help='init new config')
    parser_init.set_defaults(func=cmd_init)
    parser_info = subparsers.add_parser('info', help='get info')
    parser_info.set_defaults(func=cmd_info)
    parser_total = subparsers.add_parser(
        'total', help='get total info about all collected projects')
    parser_total.set_defaults(func=cmd_total)
    parser_total.add_argument(
        '-a', '--all', action='store_true', help='show all info')
    parser_collect = subparsers.add_parser(
        'collect', help='collect new projects')
    parser_collect.set_defaults(func=cmd_collect)
    parser_collect.add_argument('project', help='project name/path')
    parser_collect.add_argument(
        '-F', '--force', action='store_true', help='force to recollect data')
    parser_collect.add_argument(
        '-f', '--fast', action='store_true',
        help='fast mode, dont do additional API calls')
    parser_show = subparsers.add_parser(
        'show', help='show project info from cache')
    parser_show.set_defaults(func=cmd_show)
    parser_show.add_argument('query', help='project name/path or tag')
    parser_show.add_argument(
        '-a', '--all', action='store_true', help='show all info')
    parser_reverse = subparsers.add_parser(
        'reverse',
        help='main feature! get list of packages, requiring this one')
    parser_reverse.set_defaults(func=cmd_reverse)
    parser_reverse.add_argument('query', help='project name/path or tag')
    parser_reverse.add_argument(
        '-e', '--exact', action='store_true',
        help='exact match project name/path')
    parser_reverse.add_argument(
        '-F', '--force', action='store_true',
        help='force get reverse requirements, even query is not valid package')
    parser_repair = subparsers.add_parser(
        'repair', help='retrieve data from gitlab if missing something')
    parser_repair.set_defaults(func=cmd_repair)
    parser_repair.add_argument('query', help='project name/path or tag')
    parser_repair.add_argument(
        '-e', '--exact', action='store_true',
        help='exact match project name/path')
    parser_repair.add_argument(
        '-a', '--all', action='store_true',
        help='do repair for multiple matched')
    parser_repair.add_argument(
        '-F', '--force', action='store_true', help='force to recollect data')
    parser_list = subparsers.add_parser('list', help='list cached projects')
    parser_list.set_defaults(func=cmd_list)
    parser_list.add_argument('query', help='project name/path or tag')

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
