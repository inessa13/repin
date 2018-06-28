# TODO:
MIN_PYTHON_PERCENT = 10


def unknown_value(value):
    return value is None or value == 'n/a'


def filter_is_skipped(cached):
    return cached.get('skip')


def filter_is_broken(cached):
    if filter_is_skipped(cached):
        return False
    return unknown_value(cached.get('python_percent')) or \
           unknown_value(cached.get('req_sources')) or \
           unknown_value(cached.get('gitlab_ci_data')) or \
           unknown_value(cached.get('docker_data'))


def filter_is_requirer(cached):
    return filter_is_package(cached) and cached['package_data'].get(
        'install_requires')


def filter_is_python(cached):
    return not filter_is_skipped(cached) and not unknown_value(
        cached.get('python_percent')) and cached[
               'python_percent'] >= MIN_PYTHON_PERCENT


def filter_is_no_python(cached):
    return not filter_is_skipped(cached) and not unknown_value(
        cached.get('python_percent')) and cached[
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
    return 'type:unknown'


def filter_is_type_service(cached):
    return get_type_tag(cached) == 'type:service'


def filter_is_type_lib(cached):
    return get_type_tag(cached) == 'type:lib'


def filter_is_type_unknown(cached):
    return get_type_tag(cached) == 'type:unknown'


def filter_is_python_unknown(cached):
    return not filter_is_skipped(cached) and unknown_value(
        cached.get('python_percent'))
