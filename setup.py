#!/usr/bin/env python
# -*- encoding: utf-8 -*-
from setuptools import find_packages, setup
import deparse as project

CLASSIFIERS = (
    'Development Status :: 4 - Beta',
    'Environment :: Console',
    'Intended Audience :: Developers',
    'Operating System :: POSIX',
    'Programming Language :: Python',
    'Programming Language :: Python :: 3.6',
    'Topic :: Software Development',
    'Topic :: Utilities',
)

install_requires = (
    'mock',
    'python-gitlab==1.5.1',
    'python-Levenshtein==0.12.0',
    'PyYAML',
    'toml',
)

install_requires_test = (
    'pytest',
    'coverage',
)

setup(
    author='exness',
    author_email='dev@exness.com',
    name='deparse',
    description='DeParse Tool',
    version=project.__version__,
    url='https://git.exness.io/utils/deparse',
    platforms=CLASSIFIERS,
    install_requires=install_requires,
    extras_require={
        'tests': install_requires_test,
    },
    packages=find_packages('.', exclude=('tests',)),
    scripts=['deparse/cli.py'],
    include_package_data=True,
    package_data={'deparse': '.python-package'},
    zip_safe=False,
    test_suite='tests',
)
