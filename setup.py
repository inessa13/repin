import os
from setuptools import setup

CLASSIFIERS = [
    'Development Status :: 4 - Beta',
    'Programming Language :: Python',
    'Programming Language :: Python :: 3',
    'Programming Language :: Python :: 3.6',
    'Programming Language :: Python :: 3.7',
    'Topic :: Software Development',
    'Topic :: Utilities',
]

install_requires = [
    'mock',
    'python-gitlab==1.5.1',
    'python-Levenshtein==0.12.0',
    'PyYAML',
    'toml',
]

install_requires_test = [
    'pytest',
    'coverage',
]

HERE = os.path.abspath(os.path.dirname(__file__))
with open(os.path.join(HERE, 'README.md')) as file:
    README = file.read()

setup(
    name='repin',
    version='0.2.3',
    author='David Jhanyan aka inessa13',
    author_email='davo.fastcall@gmail.com',
    description='Repository Inspector',
    long_description=README,
    long_description_content_type='text/markdown',
    url='https://github.com/inessa13/repin',
    license='MIT',
    platforms=CLASSIFIERS,
    install_requires=install_requires,
    extras_require={
        'tests': install_requires_test,
    },
    entry_points={'console_scripts': [
        'repin = repin.cli:main',
    ]},
    packages=['repin'],
    include_package_data=True,
    test_suite='tests',
    python_requires='>=3.0',
)
