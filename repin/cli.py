#!/usr/bin/env python3
import argparse

from . import commands, errors, log


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(help='sub-command help')

    cmd = (
        commands.config.init,
        commands.info.info,
        commands.config.profile,
        commands.config.profile,
        commands.cache.total,
        commands.collect.collect,
        commands.cache.clear,
        commands.cache.details,
        commands.python.requirements,
        commands.python.reverse,
        commands.update.repair,
        commands.update.update,
        commands.cache.list_,
        commands.repo.cat,
    )
    for cmd in cmd:
        cmd.init_parser(subparsers)

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
