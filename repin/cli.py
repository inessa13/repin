#!/usr/bin/env python3
import argparse

from . import commands, errors, log, utils


def add_args(parser, args=()):
    if not isinstance(args, (tuple, list)):
        args = (args,)

    if 'query' in args:
        parser.add_argument('query', help='project name/path/tag')

    if 'query_all' in args:
        parser.add_argument(
            'query',
            nargs='?',
            default=':all',
            help='project name/path/tag')
    if 'exact' in args:
        parser.add_argument(
            '-e', '--exact', action='store_true',
            help='exact query match project name/path')
    if 'exclude' in args:
        parser.add_argument(
            '-x', '--exclude',
            default=':archived',
            help='exclude from query; by default excluding archived')
    if 'exclude2' in args:
        parser.add_argument(
            '-x', '--exclude',
            help='exclude from query; by default excluding archived')
    if 'all' in args:
        parser.add_argument(
            '-a', '--all',
            action='store_true', help='proceed with all found entries')
    if 'force' in args:
        parser.add_argument(
            '-F', '--force', action='store_true',
            help='force proceed')
    if 'quiet' in args:
        parser.add_argument(
            '-q', '--quiet', action='store_true',
            help='quiet output')
    if 'verbose' in args:
        parser.add_argument(
            '-v', '--verbose',
            nargs='?', action=utils.VerboseAction, help='verbose output')
    return parser


def parser_factory(subparsers):
    def init_parser(name, func, args=(), **kwargs):
        if not isinstance(args, (tuple, list)):
            args = (args,)
        parser = subparsers.add_parser(name, **kwargs)
        parser.set_defaults(func=func)
        add_args(parser, args)
        return parser
    return init_parser


def main():
    parser = argparse.ArgumentParser()
    init_parser = parser_factory(
        parser.add_subparsers(help='sub-command help'))

    parser_init = init_parser(
        'init', commands.config.init, help='init new config')
    parser_init.add_argument(
        '-l', '--local', action='store_true', help='init in cwd')
    parser_init.add_argument(
        '-p', '--profile', default='default', help='profile name')

    init_parser(
        'version', commands.info.info,
        args='verbose', help='get app info')

    parser_config = init_parser(
        'profile',
        commands.config.profile,
        args=('quiet', 'verbose'),
        help='get/switch profile')
    parser_config.add_argument(
        '-s', '--switch', help='config alias')

    init_parser(
        'total', commands.cache.total,
        args=('all', 'query_all', 'exclude'),
        help='get total info about all collected projects')

    parser_collect = init_parser(
        'collect', commands.collect,
        args=('query_all', 'exclude', 'force', 'verbose'),
        help='collect new projects')
    parser_collect.add_argument(
        '--update', action='store_true',
        help='update after collect')
    parser_collect.add_argument(
        '-S', '--skip-membership',
        action='store_true',
        help='skip membership check on project search')
    parser_collect.add_argument(
        '-n', '--no-store',
        action='store_true',
        help='only find and output')
    parser_collect.add_argument(
        '-l', '--limit', type=int, help='output limit')

    init_parser(
        'clear', commands.cache.clear,
        args=('query', 'exact', 'exclude', 'all', 'force'),
        help='clear projects from cache')

    init_parser(
        'details', commands.cache.details,
        aliases=('det',),
        args=('query', 'all', 'exact', 'exclude2', 'force'),
        help='show project info from cache')

    parser_requirements = init_parser(
        'requirements', commands.python.requirements,
        aliases=('reqs',),
        args=('query', 'exact', 'all', 'quiet'),
        help='show project info from cache')
    parser_requirements.add_argument('-i', '--index-url', help='show all info')

    init_parser(
        'reverse', commands.python.reverse,
        args=('query', 'exact', 'force', 'quiet'),
        help='main feature! get list of packages, requiring this one')

    parser_repair = init_parser(
        'repair', commands.update.repair,
        args=('exact', 'exclude', 'all', 'force'),
        help='retrieve data from gitlab if missing something',
    )
    parser_repair.add_argument(
        'query', nargs='?', default=':broken', help='project name/path/tag')

    parser_update = init_parser(
        'update', commands.update.update,
        aliases=('up',),
        args=('exact', 'exclude', 'all', 'force'),
        help='retrieve data from gitlab if missing something',
    )
    parser_update.add_argument(
        'query', nargs='?', default=':outdated', help='project name/path/tag')

    parser_list = init_parser(
        'list', commands.cache.list_,
        args=('exact', 'exclude', 'quiet'),
        help='list cached projects')
    parser_list.add_argument(
        'query', nargs='?', default=None, help='project name/path/tag')
    parser_list.add_argument(
        '-t', '--total', action='store_true',
        help='print only total on filter')
    parser_list.add_argument('-l', '--limit', type=int, help='output limit')

    parser_cat = init_parser(
        'cat', commands.repo.cat,
        args=('query', 'all', 'exact', 'exclude2'),
        help='cat')
    parser_cat.add_argument('file', help='file path to cat')
    parser_cat.add_argument(
        '-b', '--branch',
        help='cat from specified brach')

    namespace = parser.parse_args()
    if getattr(namespace, 'func', None):
        try:
            return namespace.func(namespace)
        except KeyboardInterrupt:
            return log.error('Interrupted')
        except errors.Abort:
            return
        except errors.Client as exc:
            return log.catch(exc)
        except Exception:  # noqa
            return log.exception('Unhandled exception')

    parser.print_help()


if __name__ == '__main__':
    main()
