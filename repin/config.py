import configparser
import os

from . import errors

CONFIG_DIR = '.repin'
CONFIG_FILE_NAME = 'repin.yml'


class Config:
    root = None
    path = None

    def __init__(self):
        self.parser = configparser.ConfigParser()

    def load(self):
        if self.path:
            return

        roots = (
            os.path.abspath(os.path.join('.', CONFIG_DIR)),  # local
            os.path.abspath(os.path.join(os.path.expanduser('~'), CONFIG_DIR)),
        )
        for root in roots:
            path = os.path.join(root, CONFIG_FILE_NAME)
            if os.path.isfile(path):
                self.root = root
                self.path = path
                break
        else:
            raise errors.Error('missing config, make `init` first')

        self.parser.read(self.path)

    def prepare(self, path):
        if path == '~':
            path = os.path.expanduser('~')

        self.root = os.path.abspath(os.path.join(path, CONFIG_DIR))
        if not os.path.isdir(self.root):
            os.makedirs(self.root)

        self.path = os.path.join(self.root, CONFIG_FILE_NAME)
        if os.path.isfile(self.path):
            self.parser.read(self.path)
        else:
            self.parser.add_section('global')
            self.parser.set('global', 'ssl_verify', 'true')
            self.parser.set('global', 'timeout', '60')

    def has_profile(self, name):
        return self.parser.has_option(name, 'url')

    def add_profile(self, name, url, token):
        if self.has_profile(name):
            raise Exception('profile already exists')

        self.parser.add_section(name)
        self.parser.set(name, 'url', url)
        self.parser.set(name, 'private_token', token)
        self.parser.set(name, 'api_version', '4')

    def switch_profile(self, name):
        if not self.has_profile(name):
            raise Exception('invalid profile')

        if self.parser.get('global', 'profile', fallback='None') == name:
            raise Exception('Config already set to {}'.format(name))

        self.parser.set('global', 'profile', name)

    def current_profile(self):
        return self.parser.get('global', 'profile')

    def profile_root(self):
        return os.path.join(self.root, self.current_profile())

    def profile_url(self):
        return self.parser.get(self.current_profile(), 'url', fallback=None)

    def iter_profiles(self):
        for key, opt in self.parser.items():
            if key not in ('DEFAULT', 'global'):
                yield key, opt.get('url')

    def flush(self):
        with open(self.path, 'w') as file:
            self.parser.write(file)


config = Config()
