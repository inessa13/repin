MIN_LANG_PERCENT = 10

REQUIRED_KEYS_BASE = (
    'name',
    'path',
    'created_at',
    'last_activity_at',
    'web_url',
    'archived',
    ':languages',
    'gitlab_ci_data',
    'docker_data',
)
REQUIRED_KEYS_PYTHON = (
    ':requirements',
)


def unknown_value(value):
    return value is None or value == 'n/a'


def filter_is_skipped(cached):
    return cached.get('skip')


def filter_is_broken(cached):
    for key in REQUIRED_KEYS_BASE:
        if unknown_value(cached.get(key)):
            return True

    if filter_lang_python(cached):
        for key in REQUIRED_KEYS_PYTHON:
            if unknown_value(cached.get(key)):
                return True
        if filter_is_broken_package(cached):
            return True

    if filter_language_no(cached):
        return True


def filter_have_reqs(cached):
    return cached.get(':requirements', {}) and cached.get(':requirements', {}).get('list')


def filter_no_reqs(cached):
    return filter_lang_python(cached) and not cached.get(':requirements')


def filter_is_active(cached):
    return cached.get('archived') is False


def filter_is_archived(cached):
    return cached.get('archived') is True


def filter_language_na(cached):
    return unknown_value(cached.get(':languages'))


def filter_language_no(cached):
    return cached.get(':languages') == {}


def filter_lang_python(cached):
    if filter_language_na(cached):
        return False

    return cached[':languages'].get('Python', 0) >= MIN_LANG_PERCENT


def filter_lang_factory(*codes):
    def _filter_lang(cached):
        if filter_language_na(cached):
            return False

        return sum(
            cached[':languages'].get(code, 0)
            for code in codes
        ) >= MIN_LANG_PERCENT
    return _filter_lang


def filter_is_package(cached):
    return filter_lang_python(cached) and not unknown_value(
        cached.get(':setup.py')) and cached.get(':setup.py')


def get_flit_metadata(cached):
    return cached.get('pyproject.toml', {}).get(
        'tool', {}).get('flit', {}).get('metadata', {})


def filter_is_python_pipfile(cached):
    return filter_lang_python(cached) and cached.get(':Pipfile')


def filter_is_python_pyproject(cached):
    return filter_lang_python(cached) and cached.get('pyproject.toml')


def filter_is_broken_package(cached):
    return filter_lang_python(cached) and cached.get(':setup.py') == 'n/a'


def get_type_tag(cached):
    if not filter_lang_python(cached):
        return 'no:python'
    if cached.get('docker_data'):
        if (cached['docker_data'].get('entrypoint')
                or cached['docker_data'].get('cmd')):
            return 'py:service'
    if cached.get('gitlab_ci_data') and cached['gitlab_ci_data'].get('nexus'):
        return 'py:lib'
    return 'py:na'


def filter_have_dockerfile(cached):
    return not unknown_value(cached.get('docker_data')) and bool(cached.get('docker_data'))


def filter_have_gl_ci(cached):
    return cached.get('gitlab_ci_data')


def filter_is_type_service(cached):
    return get_type_tag(cached) == 'py:service'


def filter_is_type_lib(cached):
    return get_type_tag(cached) == 'py:lib'


def filter_is_type_unknown(cached):
    return get_type_tag(cached) == 'py:na'


FILTERS = {
    ':all': lambda c: True,
    ':none': lambda c: False,
    ':active': filter_is_active,
    ':archived': filter_is_archived,

    'lang:python': filter_lang_python,
    'lang:c++': filter_lang_factory('C++'),
    'lang:go': filter_lang_factory('Go'),
    'lang:erlang': filter_lang_factory('Erlang'),
    'lang:java': filter_lang_factory('Java'),
    'lang:js': filter_lang_factory('JavaScript'),
    'lang:php': filter_lang_factory('PHP'),
    'lang:html': filter_lang_factory('HTML'),
    'lang:docker': filter_lang_factory('Dockerfile'),
    'lang:templates': filter_lang_factory('Smarty', 'HTML'),
    'lang:shell': filter_lang_factory('Shell'),
    'na:lang': filter_language_na,
    'no:lang': filter_language_no,

    'py:package': filter_is_package,
    'py:package:na': filter_is_broken_package,
    'py:lib': filter_is_type_lib,
    'py:pipfile': filter_is_python_pipfile,
    'py:pyproject': filter_is_python_pyproject,
    'py:reqs:has': filter_have_reqs,
    'py:reqs:no': filter_no_reqs,
    'py:service': filter_is_type_service,
    'py:na': filter_is_type_unknown,

    ':docker': filter_have_dockerfile,
    'gl:ci': filter_have_gl_ci,

    ':broken': filter_is_broken,
}