import argparse


class Command:
    name = None
    parser_kw = None

    def __init__(self, func, name=None, **parser_kw):
        self._func = func
        self.name = name or func.__name__
        self.update(name, **parser_kw)
        self._args = []

    def update(self, name=None, **parser_kw):
        if name:
            self.name = name
        self.parser_kw = parser_kw

    def add_arg(self, arg_):
        self._args.append(arg_)

    def __call__(self, *args, **kwargs):
        return self._func(*args, **kwargs)

    def init_parser(self, subparsers):
        parser = subparsers.add_parser(self.name, **self.parser_kw)
        parser.set_defaults(func=self._func)
        for arg_ in self._args:
            parser.add_argument(*arg_.args, **arg_.kwargs)
        return parser


class CliArgument:
    _func = None

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def __call__(self, func):
        self._func = func

        if isinstance(func, Command):
            wrap = func
            func.add_arg(self)
        else:
            wrap = Command(func)
            wrap.add_arg(self)
        return wrap


class VerboseAction(argparse.Action):
    def __init__(self, *args, **kwargs):
        super(VerboseAction, self).__init__(*args, **kwargs)
        self.values = 0

    def __call__(self, parser, namespace, values, option_string=None):
        if values is None:
            self.values += 1
        else:
            try:
                self.values = int(values)
            except ValueError:
                self.values = values.count('v') + 1
        setattr(namespace, self.dest, self.values)


def command(**kwargs):
    def _wrap(func):
        if isinstance(func, Command):
            func.update(**kwargs)
            return func
        return Command(func, **kwargs)
    return _wrap


def query(default=None):
    argument = CliArgument('query', help='project name/path/tag')
    if default is not None:
        argument.kwargs.update(nargs='?', default=default)
    return argument


def exclude(default=':archived'):
    help_ = 'exclude from query'
    if default:
        help_ += '; by default: {}'.format(default)

    return CliArgument(
        '-x', '--exclude',
        default=default,
        help=help_)


arg = CliArgument
exact = CliArgument(
    '-e', '--exact',
    action='store_true', help='exact query match project name/path')

all = CliArgument(
    '-a', '--all', action='store_true', help='proceed with all found entries')
limit = CliArgument('-l', '--limit', type=int, help='output limit')
force = CliArgument('-F', '--force', action='store_true', help='force proceed')
quiet = CliArgument('-q', '--quiet', action='store_true', help='quiet output')
verbose = CliArgument(
    '-v', '--verbose',
    nargs='?', action=VerboseAction, help='verbose output')
