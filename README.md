# RepIn - Repository Inspector.

Tool for collect information about amount repositories from gitlab. Can build dependencies for python projects.

## Installation

```
pip install repin
```

## Initialize

Init gitlab data
```
repin init
```

Get tool info
```
repin info
```

Collect all repositories in fast mode
```
repin collect all -f
```

View total info
```
repin total
```

Repair all broken repos
```
repin repair :broken -a
```

View total info after collect
```
repin total
```

## Examples

List all repos in group `site`
```
repin list site/
```

List all repos in with name `pages`
```
repin list pages
```

Get common data about `site/py` repo
```
repin show site/py
```

Get full data about `site/py` repo
```
repin show site/py -a
```

Get all python projects from gitlab, for which your access token have permissions, that requires `appserverlib`
```
repin reverse appserverlib
```

Get all python projects, requiring `aiohttp`
```
repin reverse aiohttp -f
```