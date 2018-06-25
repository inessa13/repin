#!/usr/bin/env python3
import argparse
import base64
import gitlab
import logging
import pprint
import re
import yaml
import Levenshtein
import mock

logger = logging.getLogger()

CLR_END = '\033[0m'
GL_PER_PAGE = 10
MIN_PYTHON_PERCENT = 10


def error(message, *args, **kwargs):
    CLR_FAIL = '\033[91m'
    if args or kwargs:
        message = message.format(*args, **kwargs)
    logger.error(CLR_FAIL + message + CLR_END)


def warn(message, *args, **kwargs):
    CLR_WARNING = '\033[93m'
    if args or kwargs:
        message = message.format(*args, **kwargs)
    logger.warn(CLR_WARNING + message + CLR_END)


def success(message, *args, **kwargs):
    CLR_OKGREEN = '\033[92m'
    if args or kwargs:
        message = message.format(*args, **kwargs)
    logger.warn(CLR_OKGREEN + message + CLR_END)


def get_project_data():
    try:
        with open('.python-packages', 'r') as f:
            return yaml.load(f)
    except IOError:
        return None


def save_project_data(data):
    with open('.python-packages', 'w') as f:
        yaml.dump(data, f)


def load_python_module(project, path):
    try:
        version_file = project.files.get(file_path=path, ref='master')
    except gitlab.exceptions.GitlabGetError:
        try:
            version_file = project.files.get(file_path=path + '/__init__.py', ref='master')
        except gitlab.exceptions.GitlabGetError:
            return None

    locals_ = {}
    exec(base64.b64decode(version_file.content).decode(), globals(), locals_)
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
            version = load_python_module(project, version_path)
            if version:
                if m.group(3):
                    setup_locals[m.group(3)] = version
                elif '.' not in m.group(1):
                    setup_locals[m.group(1)] = version
                else:
                    path = m.group(1).split('.')
                    part = path.pop()
                    dx = setup_locals[part] = mock.Mock()
                    while path:
                        dx = setattr(dx, part, mock.Mock())
                        part = path.pop()
                    setattr(dx, part, version)
                    error('TODO: can\'t parse `import some.path` statements')
                continue

        eval_content.append(line)

    try:
        exec('\n'.join(eval_content), globals(), setup_locals)
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


def _load_python_percent(project):
    try:
        python_percent = project.languages().get('Python', 0)
    except:
        logging.exception('python_percent failed')
        python_percent = 'n/a'

    return python_percent


def add_cache(project, project_cache=None, force=False, save=True):
    project_cache = project_cache or get_project_data() or {}

    cached = project_cache.setdefault(project.id, {})

    if not force and cached.get('skip'):
        return cached

    if not cached.get('python_percent') or cached['python_percent'] == 'n/a':
        python_percent = _load_python_percent(project)
        if not force and isinstance(python_percent, float) and python_percent < MIN_PYTHON_PERCENT:
            cached['skip'] = True
            return cached

        cached['python_percent'] = python_percent

    if cached.get('req_sources') is None:
        req_sources = []
        for file in ('setup.py', 'requirements.txt', 'reqs.txt'):
            try:
                project.files.get(file_path=file, ref='master')
                req_sources.append(file)
            except gitlab.exceptions.GitlabGetError:
                pass
            except gitlab.exceptions.GitlabError:
                req_sources = 'n/a'
                break

        cached['req_sources'] = req_sources or 'empty'

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


def fix_cache(gl, project_cache, pid, cached):
    if cached.get('skip'):
        return

    is_broken = _is_broken(cached)
    missing_package_data = _unknown(cached.get('package_data'))
    if not is_broken and not missing_package_data:
        return False

    name = cached.get('name') or pid
    try:
        project = gl.projects.get(pid)
    except gitlab.exceptions.GitlabGetError:
        error('{}: unknown broken project'.format(
            cached.get('name') or pid))
        return False

    if is_broken:
        add_cache(project, project_cache)
        is_broken = _is_broken(cached)
        name = cached.get('name') or pid
        if not is_broken:
            success('{}: package fixed', name)
            save_project_data(project_cache)

    if not is_broken and missing_package_data:
        _collect_requirements(project, cached)
        missing_package_data = _unknown(cached.get('package_data'))
        if not missing_package_data:
            success('{}: package data collected', name)
            save_project_data(project_cache)

    if is_broken:
        error('{}: is broken yet', name)
    if missing_package_data:
        error('{}: missing package_data yet', name)

    return not is_broken and not missing_package_data


def _collect_requirements(project, cached):
    if 'setup.py' in cached['req_sources']:
        cached = parse_setup(project, cached)
    elif 'reqs.txt' in cached['req_sources']:
        cached = parse_requirements(project, cached, 'reqs.txt')
    elif 'requirements.txt' in cached['req_sources']:
        cached = parse_requirements(project, cached, 'requirements.txt')
    else:
        return None
    return cached


def _unknown(value):
    return value is None or value == 'n/a'


def _is_broken(cached):
    return (
        _unknown(cached.get('python_percent')) or
        _unknown(cached.get('req_sources'))
    )


def cmd_collect(namespace):
    gl = gitlab.Gitlab.from_config('exness', ['./.python-gitlab.cfg'])

    project_cache = get_project_data() or {}

    if namespace.project == 'all':
        page = 1
        projects = gl.projects.list(page=page, per_page=GL_PER_PAGE)
        while projects:
            for project in projects:
                print(project.name)
                add_cache(project, project_cache, force=False, save=True)

            page += 1
            projects = gl.projects.list(page=page, per_page=GL_PER_PAGE)

    elif namespace.project == 'broken':
        fixed, skipped = 0, 0
        for pid, cached in project_cache.items():
            if cached.get('skip'):
                skipped += 1
            fixed += fix_cache(project_cache, pid, cached)

        warn(
            'fixed: {}, python: {}, total: {}',
            fixed, len(project_cache) - skipped, len(project_cache)
        )

    else:
        projects = gl.projects.list(search=namespace.project)
        if len(projects) == 1:
            project = projects[0]
            print(project.name)
            cached = add_cache(project, project_cache, force=True, save=True)
            if _collect_requirements(project, cached):
                save_project_data(project_cache)
            pprint.pprint(cached)
        else:
            for project in projects:
                print(project.name)
                add_cache(project, project_cache, force=False, save=True)

    # for cached in project_cache.values():
    #     if cached.get('skip'):
    #         continue
    #     print("{name}\t\t{python_percent}%\t{last_activity_at}\t{reqs}".format(**cached))
    success('total {}'.format(len(project_cache)))


def cmd_repair(namespace):
    gl = gitlab.Gitlab.from_config('exness', ['./.python-gitlab.cfg'])
    project_cache = get_project_data() or {}

    if namespace.project == 'all':
        fixed, skipped = 0, 0
        for pid, cached in project_cache.items():
            if cached.get('skip'):
                skipped += 1
            fixed += fix_cache(gl, project_cache, pid, cached)

        warn(
            'fixed: {}, python: {}, total: {}',
            fixed, len(project_cache) - skipped, len(project_cache)
        )

    else:
        cached_search = {
            pid: cached for pid, cached in project_cache.items()
            if cached.get('name') and namespace.project in cached['name']
        }
        if len(cached_search) == 1:
            pid, cached = cached_search.items()
            if fix_cache(gl, project_cache, pid, cached):
                pprint.pprint(cached)
        else:
            warn('Found: {}'.format(', '.join(cached['name'] for cached in cached_search.values())))


def cmd_show(namespace):
    project_cache = get_project_data() or {}
    cached = [
        cached for pid, cached
        in project_cache.items()
        if cached.get('name') == namespace.project
    ]

    if not cached:
        return error('Unknown project')
    cached = cached[0]

    if not cached.get('package_data'):
        return error('Missing package data for project. Call `collect` on this project or for all projects.')

    cached['dep_for'] = []
    cached['dep_for_mb'] = {}
    for project in project_cache.values():
        if project.get('name') == cached['name']:
            continue
        if project.get('package_data'):
            project_name = project['package_data'].get('name') or project['name']
            for req in project['package_data'].get('install_requires', []):
                self_name, dep_mode, version = _split_requirement_package_version(req)
                if self_name == cached['package_data']['name']:
                    cached['dep_for'].append((project_name, dep_mode, version))
                elif Levenshtein.distance(self_name, cached['package_data']['name']) <= 2:
                    cached['dep_for_mb'].setdefault(self_name, []).append((project_name, dep_mode, version))

    pprint.pprint(cached)


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
    parser_collect = subparsers.add_parser('collect', help="...")
    parser_collect.add_argument('-r', '--refresh-cache', action='store_true', help='...')
    parser_collect.set_defaults(func=cmd_collect)
    parser_collect.add_argument('project', help='...')
    parser_show = subparsers.add_parser('show', help="...")
    parser_show.add_argument('project', help='...')
    parser_show.set_defaults(func=cmd_show)
    parser_repair = subparsers.add_parser('repair', help="...")
    parser_repair.add_argument('project', help='...')
    parser_repair.add_argument('-e', '--exact', action='store_true', help='...')
    parser_repair.set_defaults(func=cmd_repair)

    namespace = parser.parse_args()

    if getattr(namespace, 'func', None):
        try:
            return namespace.func(namespace)
        except KeyboardInterrupt:
            return success('KeyboardInterrupt')
        except Exception as e:
            logging.exception('!!!')
            return error(e.args[0])

    parser.print_help()


if __name__ == '__main__':
    main()
