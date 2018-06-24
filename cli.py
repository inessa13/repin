#!/usr/bin/env python3
import argparse
import base64
import gitlab
import logging
import pprint
import re
import yaml
import Levenshtein

logger = logging.getLogger()

CLR_END = '\033[0m'
GL_PER_PAGE = 10
MIN_PYTHON_PERCENT = 10

def error(message):
    CLR_FAIL = '\033[91m'
    logger.error(CLR_FAIL + message + CLR_END)


def warn(message):
    CLR_WARNING = '\033[93m'
    logger.warn(CLR_WARNING + message + CLR_END)


def success(message):
    CLR_OKGREEN = '\033[92m'
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

    setup_result = {}
    setup_locals = {
        'setup': lambda **kw: setup_result.update(**kw),
        'find_packages': lambda *a, **kw: None,
    }

    eval_content = []
    for line in content:
        if re.match('from setuptools import', line):
            continue

        m = re.match(r'import\s+([._\w]+)(:?\s+as\s+([.\w]+))?', line)
        if m:
            version_path = m.group(1).replace('.', '/')
            version = load_python_module(project, version_path)
            if not version:
                version = {'__version__': 'n/a'}

            if m.group(3):
                setup_locals[m.group(3)] = version
            elif '.' not in m.group(1):
                setup_locals[m.group(1)] = version
            else:
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
        'name': 'n/a',
        'install_requires': [l for l in raw.split('\n') if l],
    }
    return cached


def add_cache(project, project_cache=None, force=False, save=True):
    project_cache = project_cache or get_project_data() or {}

    cached = project_cache.setdefault(project.id, {})

    if not force and cached.get('skip'):
        return cached

    if not cached.get('python_percent') or cached['python_percent'] == 'n/a':
        try:
            python_percent = project.languages().get('Python', 0)
        except:
            logging.exception('python_percent failed')
            python_percent = 'n/a'

        if not force and isinstance(python_percent, float) and python_percent < MIN_PYTHON_PERCENT:
            cached['skip'] = True
            return cached

        cached['python_percent'] = python_percent

    if cached.get('reqs') is None:
        cached['reqs'] = []
        for file in ('setup.py', 'requirements.txt', 'reqs.txt'):
            try:
                project.files.get(file_path=file, ref='master')
                cached['reqs'].append(file)
            except gitlab.exceptions.GitlabGetError:
                pass

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

    else:
        projects = gl.projects.list(search=namespace.project)
        if len(projects) == 1:
            project = projects[0]
            print(project.name)
            cached = add_cache(project, project_cache, force=True, save=True)
            if 'setup.py' in cached['reqs']:
                parse_setup(project, cached)
                save_project_data(project_cache)
            elif 'reqs.txt' in cached['reqs']:
                parse_requirements(project, cached, 'reqs.txt')
                save_project_data(project_cache)
            elif 'requirements.txt' in cached['reqs']:
                parse_requirements(project, cached, 'requirements.txt')
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
    for project in project_cache.values():
        if project.get('name') == cached['name']:
            continue
        if project.get('package_data'):
            for req in project['package_data'].get('install_requires', []):
                req = _split_requirement_package_version(req)
                if req[0] == cached['package_data']['name']:
                    cached['dep_for'].append(req)
                elif Levenshtein.distance(req[0], cached['package_data']['name']) < 2:
                    cached['dep_for_mb'].append(req)



    pprint.pprint(cached)


def _split_requirement_package_version(req):
    for d in ('==', '~=', '>='):
        if d in req:
            req = req.split(d)
            break
    else:
        req = (req, None)
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
