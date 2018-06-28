MIN_PYTHON_PERCENT = 10

REQUIRED_KEYS_BASE = (
    'name',
    'path',
    'created_at',
    'last_activity_at',
    'web_url',
    'archived',
    'python_percent',
)
REQUIRED_KEYS_PYTHON = (
    'gitlab_ci_data',
    'docker_data',
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

    if filter_is_python(cached):
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


def filter_is_python(cached):
    return not unknown_value(cached.get('python_percent')) and cached[
               'python_percent'] >= MIN_PYTHON_PERCENT


def filter_is_no_python(cached):
    return not unknown_value(cached.get('python_percent')) and cached[
               'python_percent'] < MIN_PYTHON_PERCENT


def filter_is_package(cached):
    return filter_is_python(cached) and not unknown_value(
        cached.get('package_data'))


def filter_is_package_na(cached):
    return filter_is_python(cached) and unknown_value(
        cached.get('package_data'))


def filter_is_req_unknown(cached):
    return filter_is_python(cached) and cached.get('req_sources') == 'empty'


def get_type_tag(cached):
    if not filter_is_python(cached):
        return 'no:python'
    if cached.get('docker_data') and cached['docker_data'].get('entrypoint'):
        return 'type:service'
    if cached.get('gitlab_ci_data') and cached['gitlab_ci_data'].get('nexus'):
        return 'type:lib'
    return 'na:type'


def filter_is_type_service(cached):
    return get_type_tag(cached) == 'type:service'


def filter_is_type_lib(cached):
    return get_type_tag(cached) == 'type:lib'


def filter_is_type_unknown(cached):
    return get_type_tag(cached) == 'na:type'


def filter_is_python_unknown(cached):
    return unknown_value(cached.get('python_percent'))


FILTERS = {
    ':all': lambda c: True,
    ':python': filter_is_python,
    ':active': filter_is_active,
    ':archived': filter_is_archived,
    'no:python': filter_is_no_python,
    'na:python': filter_is_python_unknown,
    'na:req_sources': filter_is_req_unknown,
    ':package': filter_is_package,
    'na:package': filter_is_package_na,
    'have:reqs': filter_have_reqs,
    'no:reqs': filter_no_reqs,
    'type:service': filter_is_type_service,
    'type:lib': filter_is_type_lib,
    'na:type': filter_is_type_unknown,
    ':broken': filter_is_broken,
}