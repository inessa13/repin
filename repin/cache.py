import os
import shutil
import threading

import toml.decoder
import yaml
import yaml.parser
import yaml.representer

from . import cli_utils, config

CACHE_FILE_NAME = '.repin-cache'
CACHE_FILE_BACK_NAME = '.repin-cache-back'


class Base:
    root = None
    path = None
    _data = None

    def __init__(self):
        self._lock = threading.RLock()

    def ensure(self):
        if self._data is not None:
            return self._data

        self.root = config.config.profile_root()
        self.path = os.path.join(self.root, CACHE_FILE_NAME)

        if os.path.exists(self.path):
            self._data = self._read()
        else:
            self._data = {}

    def filter_map(self, query, exact, exclude=None):
        self.ensure()

        if query == exclude:
            exclude = ':none'

        filter_finally = filter_ = _parse_query(query, exact, all, any)
        if not filter_:
            return {}

        if exclude:
            exclude = _parse_query(exclude, False, any, all)
            if not exclude:
                return {}
            filter_finally = lambda c: filter_(c) and not exclude(c)

        return dict(cache.items(filter_finally))

    def _read(self):
        raise NotImplementedError

    def setdefault(self, pid, value):
        raise NotImplementedError

    def select(self, pid):
        raise NotImplementedError

    def update(self, pid, data):
        raise NotImplementedError

    def delete(self, pid):
        raise NotImplementedError

    def total(self):
        raise NotImplementedError

    def items(self, filter_=None):
        raise NotImplementedError

    def flush(self):
        raise NotImplementedError


class Yaml(Base):
    root = None
    path = None
    _data = None

    def _read(self):
        try:
            with open(self.path, 'r') as f:
                return yaml.load(f)
        except yaml.parser.ParserError:
            if os.path.exists(self._backup_path):
                shutil.move(self._backup_path, self.path)
                return self._read()
            raise

    def select(self, pid, default=None):
        self.ensure()
        return self._data.get(pid, default)

    def update(self, pid, data):
        self.ensure()

        self._lock.acquire()
        try:
            self._data.setdefault(pid, {}).update(data)
            return self._data[pid]
        finally:
            self._lock.release()

    def delete(self, pid):
        self.ensure()

        self._lock.acquire()
        try:
            del self._data[pid]
        finally:
            self._lock.release()

    def total(self):
        self.ensure()
        return len(self._data)

    def items(self, filter_=None):
        self.ensure()

        for pid, data in self._data.items():
            if filter_ is not None and not filter_(data):
                continue
            yield pid, data

    def flush(self):
        self.ensure()

        if not os.path.exists(self.root):
            os.makedirs(self.root)

        if os.path.exists(self.path):
            shutil.copy(self.path, self._backup_path)

        for sub in toml.decoder.InlineTableDict.__subclasses__():
            yaml.add_representer(
                sub, yaml.representer.SafeRepresenter.represent_dict)

        self._lock.acquire()
        try:
            with open(self.path, 'w') as f:
                yaml.dump(self._data, f)
        except yaml.representer.RepresenterError:
            shutil.copy(self._backup_path, self.path)
            raise
        finally:
            self._lock.release()

    @property
    def _backup_path(self):
        return os.path.join(self.root, CACHE_FILE_BACK_NAME)


def _parse_query(query, exact=False, mode=all, mode_inverse=any):
    # TODO: replace inverse symbol from '.'
    # if '.' in query and ',' not in query:
    #     mode = mode_inverse
    #     query = query.replace('.', ',')

    key = 'path' if '/' in query else 'name'
    if ':' in query:
        query = query.split(',')
        filters = [cli_utils.FILTERS.get(sub) for sub in query]
        if not all(filters):
            print('Unknown filter: {}'.format(', '.join(
                sub for sub in query if sub not in cli_utils.FILTERS)))
            return None
        return lambda cached: mode(sub(cached) for sub in filters)

    elif exact:
        return lambda c: query == c.get(key)

    return lambda c: query in c.get(key, '')


cache = Yaml()
