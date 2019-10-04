import datetime

import gitlab

from . import collectors, errors, filters, apis
from .cache import cache


def add_cache(project, force=False, save=True, update=True):
    cached = cache.update(project.id, {
        'name': project.name,
        'path': '{}/{}'.format(project.namespace['full_path'], project.path),
        'created_at': project.created_at,
        'last_activity_at': project.last_activity_at,
        'web_url': project.web_url,
        ':last_update_at': datetime.datetime.now(),
        ':modified': True
    })

    try:
        cached['archived'] = project.archived
    except AttributeError:
        cached['archived'] = False

    try:
        cached['default_branch'] = project.default_branch or ':none'
    except AttributeError:
        cached['default_branch'] = ':none'

    if update:
        collected = collectors.collect(project, cached, force)
        if collected:
            cache.update(project.id, collected)

    if save:
        cache.flush()

    return cached


def fix_cache(pid, cached, force, default):
    force = force or filters.filter_is(default, cached)
    if not force:
        raise errors.Warn('{}: not {}'.format(cached['name'], default))

    try:
        project = apis.get().projects.get(pid)
    except gitlab.exceptions.GitlabGetError:
        if not cached.get(':lost'):
            cached[':lost'] = True
            cached[':modified'] = True
        raise errors.Warn('{}: lost'.format(cached.get('name') or pid))

    cached = add_cache(project, force=force, save=False, update=True)

    if filters.filter_is_broken(cached):
        raise errors.Error('{}: package not updated'.format(
            cached.get('name') or pid))

    return cached
