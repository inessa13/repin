# RepIn - Repository Inspector.

[![build status](https://git.exness.io/utils/repin/badges/master/build.svg)](https://git.exness.io/utils/repin/commits/master)
[![coverage report](https://git.exness.io/utils/repin/badges/master/coverage.svg)](https://git.exness.io/utils/repin/commits/master)

Tool for collect information about amount repositories from gitlab. Can build dependencies for python projects.

## Installation

pip install repin

## Examples

repin init
repin collect all -f
repin total
repin repair :broken -a
repin list site/
repin show site/py
repin show site/py -a
repin reverse appserverlib
repin reverse aiohttp -f
