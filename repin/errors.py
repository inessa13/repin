class Base(Exception):
    pass


class Abort(Base):
    pass


class Client(Base):
    pass


class Success(Client):
    pass


class Info(Client):
    pass


class Error(Client):
    pass


class Warn(Client):
    pass


class NothingFound(Error):
    args = ('Nothing found',)
