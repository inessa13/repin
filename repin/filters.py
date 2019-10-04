import datetime

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
    'default_branch',
)
REQUIRED_KEYS_PYTHON = (
    ':requirements',
)


def unknown_value(value):
    return value is None or value == 'n/a'


def filter_is_lost(cached):
    return cached.get(':lost')


def filter_is_empty(cached):
    return cached.get('default_branch') == ':none'


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

    if filter_language_na(cached):
        return True


def filter_is_outdated(cached):
    return cached.get(':last_upgrade_activity') != cached['last_activity_at']


def inactive_days(cached, value=None):
    days = (datetime.date.today() - datetime.datetime.strptime(
        cached['last_activity_at'][:10], '%Y-%m-%d').date()).days
    if value is None:
        return days
    return days > value


def filter_have_reqs(cached):
    return cached.get(
        ':requirements', {}) and cached.get(':requirements', {}).get('list')


def filter_no_reqs(cached):
    return filter_lang_python(cached) and not cached.get(':requirements')


def filter_is_active(cached):
    return cached.get('archived') is False


def filter_is_archived(cached):
    return cached.get('archived') is True


def filter_language_na(cached):
    return unknown_value(
        cached.get(':languages')) or cached.get(':languages') == {}


def filter_language_no(cached):
    return cached.get(':languages') is False


def filter_lang_python(cached):
    if filter_language_na(cached) or filter_language_no(cached):
        return False

    return cached[':languages'].get('Python', 0) >= MIN_LANG_PERCENT


def filter_lang_factory(*codes):
    def _filter_lang(cached):
        if filter_language_na(cached) or filter_language_no(cached):
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
    return not unknown_value(
        cached.get('docker_data')) and bool(cached.get('docker_data'))


def filter_have_gl_ci(cached):
    return cached.get('gitlab_ci_data')


def filter_is_type_service(cached):
    return get_type_tag(cached) == 'py:service'


def filter_is_type_lib(cached):
    return get_type_tag(cached) == 'py:lib'


def filter_is_type_unknown(cached):
    return get_type_tag(cached) == 'py:na'


def tag_is_warn(tag):
    if tag in (':archived', ':lost', ':empty', ':outdated'):
        return True

    split = set(tag.split(':'))
    for key in ('na',):
        if key in split:
            return True


def filter_is(tag, cached):
    return FILTERS[tag](cached)


FILTERS = {
    ':all': lambda c: True,
    ':none': lambda c: False,
    ':active': filter_is_active,
    ':archived': filter_is_archived,
    ':outdated': filter_is_outdated,
    ':lost': filter_is_lost,
    ':empty': filter_is_empty,
    ':broken': filter_is_broken,

    'old:month': lambda c: inactive_days(c, 30),
    'old:3month': lambda c: inactive_days(c, 30 * 3),
    'old:6month': lambda c: inactive_days(c, 30 * 6),
    'old:year': lambda c: inactive_days(c, 365),
    'old:2year': lambda c: inactive_days(c, 365 * 2),
    'old:4year': lambda c: inactive_days(c, 365 * 4),

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
    'lang:na': filter_language_na,
    'lang:no': filter_language_no,

    ':py': filter_lang_python,
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
    'ci:gitlab': filter_have_gl_ci,
}
