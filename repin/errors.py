class Base(Exception):
    pass


class Error(Base):
    pass


class Warn(Base):
    pass


class Abort(Base):
    pass


class NothingFound(Error):
    args = ('Nothing found',)
