import base64

import gitlab

from .. import apis, cli_args, log, utils
from ..cache import cache
from ..config import config


@cli_args.command(help='cat file (list dir) from repo')
@cli_args.query()
@cli_args.all
@cli_args.exact
@cli_args.exclude(default=None)
@cli_args.arg('file', help='file path to cat')
@cli_args.arg('-b', '--branch', help='cat from specified brach')
def cat(namespace):
    config.load()

    cached_search = cache.filter_map(
        namespace.query, namespace.exact, False)

    utils.check_found(namespace, cached_search)

    for pid, cached in cached_search.items():
        try:
            project = apis.get().projects.get(pid)
        except gitlab.exceptions.GitlabGetError:
            log.error('{}: missing'.format(cached.get('name') or pid))
            continue

        if namespace.file[-1] == '/':
            try:
                files = project.repository_tree(
                    path=namespace.file,
                    ref=namespace.branch or project.default_branch)
            except gitlab.exceptions.GitlabGetError:
                continue
            for file in files:
                if file['type'] == 'tree':
                    log.info(file['path'] + '/')
                else:
                    log.info(file['path'])
        else:
            try:
                file = project.files.get(
                    file_path=namespace.file,
                    ref=namespace.branch or project.default_branch)
            except gitlab.exceptions.GitlabGetError:
                continue
            log.info(base64.b64decode(file.content).decode())
