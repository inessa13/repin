MIN_PYTHON_PERCENT = 10

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
    'req_sources',
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


def filter_have_reqs(cached):
    return filter_is_package(cached) and cached['package_data'].get(
        'install_requires')


def filter_no_reqs(cached):
    return filter_is_package(cached) and not cached['package_data'].get(
        'install_requires')


def filter_is_active(cached):
    return cached.get('archived') is False


def filter_is_archived(cached):
    return cached.get('archived') is True


def filter_language_na(cached):
    return unknown_value(cached.get(':languages'))


def filter_lang_python(cached):
    if filter_language_na(cached):
        return False

    return cached[':languages'].get('Python', 0) >= MIN_PYTHON_PERCENT


def filter_lang_cpp(cached):
    if filter_language_na(cached):
        return False

    return cached[':languages'].get('C++', 0) >= MIN_PYTHON_PERCENT


def filter_lang_go(cached):
    if filter_language_na(cached):
        return False

    return cached[':languages'].get('Go', 0) >= MIN_PYTHON_PERCENT


def filter_lang_erlang(cached):
    if filter_language_na(cached):
        return False

    return cached[':languages'].get('Erlang', 0) >= MIN_PYTHON_PERCENT


def filter_lang_java(cached):
    if filter_language_na(cached):
        return False

    return cached[':languages'].get('Java', 0) >= MIN_PYTHON_PERCENT


def filter_lang_js(cached):
    if filter_language_na(cached):
        return False

    return cached[':languages'].get('JavaScript', 0) >= MIN_PYTHON_PERCENT


def filter_lang_php(cached):
    if filter_language_na(cached):
        return False

    return cached[':languages'].get('PHP', 0) >= MIN_PYTHON_PERCENT


def filter_lang_templates(cached):
    if filter_language_na(cached):
        return False

    return cached[':languages'].get('Smarty', 0) >= MIN_PYTHON_PERCENT or cached[':languages'].get('HTML', 0) >= MIN_PYTHON_PERCENT


def filter_is_package(cached):
    return filter_lang_python(cached) and not unknown_value(
        cached.get('package_data'))


def filter_is_package_na(cached):
    return filter_lang_python(cached) and unknown_value(
        cached.get('package_data'))


def filter_is_req_unknown(cached):
    return filter_lang_python(cached) and cached.get('req_sources') == 'empty'


def get_type_tag(cached):
    if not filter_lang_python(cached):
        return 'no:python'
    if cached.get('docker_data') and cached['docker_data'].get('entrypoint'):
        return 'type:service'
    if cached.get('gitlab_ci_data') and cached['gitlab_ci_data'].get('nexus'):
        return 'type:lib'
    return 'na:type'


def filter_have_dockerfile(cached):
    return not unknown_value(cached.get('docker_data')) and bool(cached.get('docker_data'))


def filter_is_type_service(cached):
    return get_type_tag(cached) == 'type:service'


def filter_is_type_lib(cached):
    return get_type_tag(cached) == 'type:lib'


def filter_is_type_unknown(cached):
    return get_type_tag(cached) == 'na:type'


FILTERS = {
    ':all': lambda c: True,
    ':none': lambda c: False,
    ':active': filter_is_active,
    ':archived': filter_is_archived,

    'lang:python': filter_lang_python,
    'lang:c++': filter_lang_cpp,
    'lang:go': filter_lang_go,
    'lang:erlang': filter_lang_erlang,
    'lang:java': filter_lang_java,
    'lang:js': filter_lang_js,
    'lang:php': filter_lang_php,
    'lang:templates': filter_lang_templates,
    'na:language': filter_language_na,

    'no:python': lambda c: not filter_lang_python(c),
    'na:req_sources': filter_is_req_unknown,
    'python:package': filter_is_package,
    'na:package': filter_is_package_na,
    'have:reqs': filter_have_reqs,
    'no:reqs': filter_no_reqs,

    'type:service': filter_is_type_service,
    'type:lib': filter_is_type_lib,
    'na:type': filter_is_type_unknown,
    ':docker': filter_have_dockerfile,

    ':broken': filter_is_broken,
}