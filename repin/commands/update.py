import concurrent.futures
import time

from .. import cli_args, errors, helpers, log, utils
from ..cache import cache
from ..config import config


@cli_args.command(
    aliases=('up',), help='retrieve data from gitlab if missing something')
@cli_args.query(default=':outdated')
@cli_args.exact
@cli_args.exclude()
@cli_args.all
@cli_args.force
def update(namespace):
    config.load()

    return repair(namespace, default=':outdated')


@cli_args.command(help='retrieve data from gitlab if missing something')
@cli_args.query(default=':broken')
@cli_args.exact
@cli_args.exclude()
@cli_args.all
@cli_args.force
def repair(namespace, default=':broken'):
    config.load()

    cached_search = cache.filter_map(
        namespace.query, namespace.exact, namespace.exclude)

    utils.check_found(
        namespace, cached_search, namespace.query == default or namespace.all)

    fixed = modified = 0
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=5)
    retry_timeout = 2

    tasks = {
        pool.submit(
            helpers.fix_cache,
            pid, cached, namespace.force, default,
        ): pid
        for pid, cached in cached_search.items()}
    while tasks:
        retry = set()
        retry_step = 1
        try:
            for i, feature in enumerate(concurrent.futures.as_completed(tasks)):
                pid = tasks[feature]

                try:
                    cached = feature.result()
                    log.info('{}: package updated'.format(cached['name']))

                except errors.Client as exc:
                    log.catch(exc)

                except KeyError as exc:
                    if exc.args[0] == 'retry-after':
                        retry.add(pid)
                    else:
                        log.exception(
                            '{}: package fix failed'.format(
                                cache.select(pid, {}).get('name') or pid))

                except Exception:
                    log.exception(
                        '{}: package fix failed'.format(
                            cache.select(pid, {}).get('name') or pid))

                else:
                    fixed += 1

                pid_modified = cache.select(pid).pop(':modified', False)
                if pid_modified:
                    modified += 1

                if modified and not i % 10:
                    cache.flush()
        except KeyboardInterrupt:
            log.warn('Interrupted')
            break

        if retry:
            log.warn('need to retry {} entries'.format(len(retry)))
            time.sleep(retry_timeout * retry_step)
            tasks = {
                pool.submit(
                    helpers.fix_cache,
                    pid, cached, namespace.force, default,
                ): pid
                for pid, cached in cached_search.items()
                if pid in retry
            }
            retry_step += 1
        else:
            break

    if modified:
        cache.flush()

    log.success('Fixed: {}, Modified: {}, Found: {}, Total: {}'.format(
        fixed, modified, len(cached_search), cache.total()))
