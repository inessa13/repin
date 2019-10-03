import pprint

from .. import errors, helpers, apis
from ..cache import cache
from ..config import config

GL_PER_PAGE = 100


def iter_all(namespace, **kwargs):
    kwargs['per_page'] = GL_PER_PAGE

    projects = apis.get().projects.list(as_list=False, **kwargs)
    for project in projects:
        if namespace.exclude and namespace.exclude in '{}/{}'.format(
                project.namespace['full_path'], project.path):
            continue
        yield project


def iter_path(namespace, **kwargs):
    group_name, project_name = namespace.query.split('/', 1)
    groups = apis.get().groups.list(search=group_name, **kwargs)
    for group in groups:
        print(group.name + '...')
        opt = {}
        if project_name:
            opt['search'] = project_name

        projects = group.projects.list(**opt)
        for project in projects:
            yield project


def iter_search(namespace, **kwargs):
    projects = apis.get().projects.list(search=namespace.query, **kwargs)
    for project in projects:
        yield project


def cmd_collect(namespace):
    if ':' in namespace.query and namespace.query != ':all':
        raise errors.Error('Collect cant use filters beside :all')

    if namespace.exclude != ':archived' and ':' in namespace.exclude:
        raise errors.Error('Collect cant use filters in exclude')

    config.load()

    # TODO: 'visibility': 'private',
    list_options = {}
    if 'gitlab.com' in config.profile_url() and not namespace.skip_membership:
        list_options['membership'] = True

    if namespace.limit:
        list_options['per_page'] = namespace.limit

    if namespace.query == ':all':
        it = iter_all
    elif '/' in namespace.query:
        it = iter_path
    else:
        it = iter_search

    index = 0
    for index, project in enumerate(it(namespace, **list_options)):
        if namespace.limit and index + 1 > namespace.limit:
            print('limit reached')
            break

        if not namespace.verbose:
            print(project.name)
        elif namespace.verbose == 1:
            print('{} ({})'.format(project.path_with_namespace, index + 1))
        else:
            pprint.pprint(project.attributes)

        try:
            helpers.add_cache(
                project,
                force=namespace.force,
                save=False,
                update=namespace.update)
        except KeyboardInterrupt:
            print('Interrupted')
            break

        if not namespace.no_store and not index % 10:
            cache.flush()

    if not namespace.no_store:
        cache.flush()

    print('found {}. total {}'.format(index, cache.total()))
