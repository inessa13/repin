import getpass

from .. import errors, log
from ..config import config


def init(namespace):
    config.prepare('.' if namespace.local else '~')

    if config.has_profile(namespace.profile):
        raise errors.Warn(
            'profile `{}` already exists'.format(namespace.profile))

    url = input('url: ')
    token = getpass.getpass('token: ')

    config.add_profile(namespace.profile, url, token)
    config.switch_profile(namespace.profile)
    config.flush()
    log.success('inited')


def profile(namespace):
    config.load()

    if namespace.switch:
        config.switch_profile(namespace.switch)
        config.flush()
        raise errors.Success('set config to {}'.format(namespace.switch))

    if namespace.quiet:
        log.info(config.current_profile())
    else:
        log.info('Config root: {}'.format(config.root))
        log.info('Profile: {}'.format(config.current_profile()))
    if namespace.verbose:
        log.info('Available profiles:')
        for profile_, url in config.iter_profiles():
            log.info(' ', profile_, url)
