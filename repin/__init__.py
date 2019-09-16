from collections import namedtuple

VersionInfo = namedtuple('VersionInfo', ('major', 'minor', 'micro'))

VERSION = VersionInfo(0, 2, 3)

__version__ = '{0.major}.{0.minor}.{0.micro}'.format(VERSION)
