import importlib
import base64
import logging
import re
import functools
import os

import gitlab
import mock
import toml

from . import cli_utils


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

    return languages_data


_collect_languages.cache_key = (':languages',)


def collect_file_data(filename, *cache_keys):
    def decorator(func):
        @functools.wraps(func)
        def wrap(project, cached):
            try:
                file = project.files.get(file_path=filename, ref='master')
            except gitlab.exceptions.GitlabGetError:
                if len(cache_keys) > 1:
                    return [False] * len(cache_keys)
                else:
                    return False
            except gitlab.exceptions.GitlabError:
                if len(cache_keys) > 1:
                    return ['n/a'] * len(cache_keys)
                else:
                    return 'n/a'

            data = {'file': filename}
            raw = base64.b64decode(file.content).decode()
            return func(project, data, raw)

        wrap.cache_key = cache_keys

        return wrap
    return decorator


@collect_file_data('setup.py', ':setup.py', ':requirements')
def _collect_setup_py(project, data, raw_content):
    setup_result = {}
    setup_locals = {
        'setup': lambda **kw: setup_result.update(**kw),
        'find_packages': lambda *a, **kw: None,
    }
    setup_globals = {
        'open': _fake_open(project),
        '__file__': 'setup.py',
        'os': _fake_os,
    }

    eval_content = []
    for line in raw_content.split('\n'):
        if re.match('from setuptools import', line):
            continue
        eval_content.append(line)

    eval_content = _preload_imports(project, '', eval_content, setup_locals)

    try:
        exec('\n'.join(eval_content), setup_globals, setup_locals)
    except KeyboardInterrupt:
        raise
    except:
        logging.exception('setup.py parse failed')
        # print('\n'.join(eval_content))
        # print(setup_locals)
        return 'n/a', 'n/a'

    if not setup_result:
        return False, 'n/a'

    # python classes are breaking cache read
    setup_result.pop('cmdclass', None)

    data.update(setup_result)
    requirements = {}
    if 'install_requires' in setup_result:
        requirements['main'] = setup_result['install_requires']
    if setup_result.get('extras_require'):
        for k, v in setup_result['extras_require'].items():
            requirements[k] = v

    requirements = {
        'file': 'setup.py',
        'list': [r for reqs in requirements.values() for r in reqs],
    }
    return data, requirements


def _collect_requirements(data, raw_content):
    # data.setdefault('req_sources', set()).add('requirements.txt')
    data['list'] = [line for line in raw_content.split('\n') if line]
    return data


@collect_file_data('requirements.txt', ':requirements')
def _collect_requirements_1(project, data, raw_content):
    return _collect_requirements(data, raw_content)


@collect_file_data('reqs.txt', ':requirements')
def _collect_requirements_2(project, data, raw_content):
    return _collect_requirements(data, raw_content)


@collect_file_data('requirements_base.txt', ':requirements')
def _collect_requirements_3(project, data, raw_content):
    return _collect_requirements(data, raw_content)


@collect_file_data('requirements/prod.txt', ':requirements')
def _collect_requirements_4(project, data, raw_content):
    return _collect_requirements(data, raw_content)


@collect_file_data('requirements/live.txt', ':requirements')
def _collect_requirements_5(project, data, raw_content):
    return _collect_requirements(data, raw_content)


@collect_file_data('requirements/dev.txt', ':requirements')
def _collect_requirements_6(project, data, raw_content):
    return _collect_requirements(data, raw_content)


@collect_file_data('requirements/test.txt', ':requirements')
def _collect_requirements_7(project, data, raw_content):
    return _collect_requirements(data, raw_content)


@collect_file_data('requirements/tests.txt', ':requirements')
def _collect_requirements_8(project, data, raw_content):
    return _collect_requirements(data, raw_content)


@collect_file_data('Pipfile', ':Pipfile', ':requirements')
def _collect_pip_file(project, data, raw_content):
    pip_file = toml.loads(raw_content)
    data.update(pip_file)

    data['packages'] = reqs = [
        _parse_pipfile_req(package, value)
        for package, value
        in pip_file.get('packages', {}).items()
    ]

    if reqs:
        requirements = {
            'file': 'Pipfile',
            'list': reqs,
        }
    else:
        requirements = {}

    return data, requirements


@collect_file_data('pyproject.toml', 'pyproject.toml', ':requirements')
def _collect_pyproject(project, data, raw_content):
    content = toml.loads(raw_content)
    data.update(content)

    reqs = set()

    poetry = content.get('tool', {}).get('poetry', {})
    if poetry:
        if poetry.get('dependencies'):
            reqs |= {
                _parse_poetry_req(package, value)
                for package, value
                in poetry['dependencies'].items()
            }
        if poetry.get('dev-dependencies'):
            reqs |= {
                _parse_poetry_req(package, value)
                for package, value
                in poetry['dev-dependencies'].items()
            }

    flit = content.get('tool', {}).get('flit', {}).get('metadata', {})
    if flit:
        reqs |= set(flit.get('requires', []))

    if reqs:
        requirements = {
            'file': 'pyproject.toml',
            'list': list(reqs),
        }
    else:
        requirements = {}

    return data, requirements


@collect_file_data('Dockerfile', 'docker_data')
def _collect_dockerfile(project, data, raw_content):
    for line in raw_content.split('\n'):
        m = re.match(r'ENTRYPOINT\s+\[([\'"])(.*)\1\]$', line)
        if m:
            data['entrypoint'] = m.group(2)
        m = re.match(r'CMD\s+\[([\'"])(.*)\1\]$', line)
        if m:
            data['cmd'] = '{}{}{}'.format(m.group(1), m.group(2), m.group(1))
    return data


@collect_file_data('.gitlab-ci.yml', 'gitlab_ci_data')
def _collect_gitlab_ci(project, data, raw_content):
    if 'nexus' in raw_content:
        data['nexus'] = 'mentioned'
    return data


def _load_python_module(project, path):
    try:
        version_file = project.files.get(file_path=path + '.py', ref='master')
    except gitlab.exceptions.GitlabGetError:
        try:
            version_file = project.files.get(
                file_path=path + '/__init__.py', ref='master')
        except gitlab.exceptions.GitlabGetError:
            return None

    setup_globals = {
        'open': _fake_open(project),
        '__file__': path,
        '__name__': os.path.basename(path),
        'os': _fake_os,
    }
    locals_ = {}
    file_content = base64.b64decode(version_file.content).decode()

    eval_content = _preload_imports(
        project, path, file_content.split('\n'), locals_)

    try:
        exec('\n'.join(eval_content), setup_globals, locals_)
    except KeyboardInterrupt:
        raise
    except Exception:
        logging.exception('Load module %s failed!', path)
        return 'n/a'

    return type('PythonModule', (), locals_)


def _preload_imports(project, path, lines, setup_locals):
    eval_content = []
    for line in lines:
        m = re.match(r'import\s+([._\w]+)(:?\s+as\s+([.\w]+))?', line)
        if m:
            try:
                importlib.import_module(m.group(1))
            except ImportError:
                pass
            except TypeError:
                pass
            else:
                eval_content.append(line)
                continue

            version_path = m.group(1).replace('.', '/')
            try:
                version = _load_python_module(project, version_path)
            except KeyboardInterrupt:
                raise
            except:
                logging.exception('setup.py parse failed (import1)')
                return 'n/a', 'n/a'

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

        m = re.match(
            r'from\s+([._\w]+)\s+import\s+([_\w*]+)(:?\s+as\s+([.\w]+))?', line)
        if m:
            try:
                importlib.import_module(m.group(1))
            except ImportError:
                pass
            except TypeError:
                pass
            else:
                eval_content.append(line)
                continue

            version_path = m.group(1).replace('.', '/')
            if version_path.startswith('/'):
                version_path = path + version_path
            try:
                version = _load_python_module(project, version_path)
            except KeyboardInterrupt:
                raise
            except:
                logging.exception('setup.py parse failed (import2)')
                return 'n/a', 'n/a'

            if m.group(2) == '*':
                setup_locals[os.path.basename(version_path)] = version
                if getattr(version, '__all__', None):
                    for k in version.__all__:
                        setup_locals[k] = getattr(version, k, None)
                else:
                    for k, v in version.__dict__.items():
                        setup_locals[k] = v
            else:
                version = getattr(version, m.group(2), None)
                if m.group(3):
                    setup_locals[m.group(3)] = version
                else:
                    setup_locals[m.group(2)] = version
            continue

        eval_content.append(line)
    return eval_content


def _parse_pipfile_req(package, data):
    if data == '*':
        return package

    elif isinstance(data, str):
        # TODO:
        if ',' in data:
            data = data.split(',', 1)[0]

        m = re.match(r'(>|<|>=|==|<=|~=)([\w\d.]+)$', data)
        if m:
            return package + data

    elif isinstance(data, dict) and data.get('version'):
        return package + '==' + data['version']

    else:
        # TODO:
        return package + '==(complex)'


def _parse_poetry_req(package, data):
    package = str(package)
    if data == '*':
        return package

    if isinstance(data, str):
        return package + '==' + data

    if isinstance(data, dict) and data.get('version'):
        return package + '==' + data['version']

    return package + '==(complex)'


class _fake_open:
    def __init__(self, project):
        self.project = project

    def __call__(self, path, mode='r'):
        self.path = path
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def readlines(self):
        return self.read().split('\n')

    def read(self, n=0):
        try:
            file = self.project.files.get(
                file_path=self.path, ref='master')
        except gitlab.exceptions.GitlabGetError:
            return None

        return base64.b64decode(file.content).decode()


class _fake_os:
    path = os.path

    @staticmethod
    def getcwd():
        return ''


CACHE_COLLECTORS = (
    (_collect_languages, None),
    (_collect_dockerfile, None),
    (_collect_gitlab_ci, None),
    (_collect_setup_py, cli_utils.filter_lang_python),
    (_collect_requirements_1, cli_utils.filter_lang_python),
    (_collect_requirements_2, cli_utils.filter_lang_python),
    (_collect_requirements_3, cli_utils.filter_lang_python),
    (_collect_requirements_4, cli_utils.filter_lang_python),
    (_collect_requirements_5, cli_utils.filter_lang_python),
    (_collect_requirements_6, cli_utils.filter_lang_python),
    (_collect_requirements_7, cli_utils.filter_lang_python),
    (_collect_requirements_8, cli_utils.filter_lang_python),
    (_collect_pip_file, cli_utils.filter_lang_python),
    (_collect_pyproject, cli_utils.filter_lang_python),
)


def collect(project, cached, force):
    collected = {}
    for collector, condition in CACHE_COLLECTORS:
        if not force and not any(cli_utils.unknown_value(
                cached.get(cache_key)) for cache_key in collector.cache_key):
            continue
        if condition and not condition({**cached, **collected}):
            continue

        data = collector(project, cached)
        if len(collector.cache_key) == 1:
            data = [data]

        if len(data) != len(collector.cache_key):
            raise Exception(
                'Invalid keys count', len(data), len(collector.cache_key))

        def empty(value):
            return value is False or value == 'n/a' or value is None

        for cache_key, d in zip(collector.cache_key, data):
            if empty(collected.get(cache_key)):
                collected[cache_key] = d
            elif not empty(d):
                if cache_key == ':requirements':
                    reqs = set(collected[cache_key].get('list', []))
                    reqs |= set(d.get('list', []))
                    collected[cache_key]['list'] = list(reqs)
                    files = collected[cache_key].setdefault('files', set())
                    files.add(d.get('file'))
                    if collected[cache_key].get('file'):
                        files.add(collected[cache_key]['file'])
                        del collected[cache_key]['file']
                    continue

                raise ValueError(
                    'multiple data for save setting', collector.cache_key, d, collected[cache_key])

    cached.update(collected)
