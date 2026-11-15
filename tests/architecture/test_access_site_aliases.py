"""FINDER-ALIAS-BLIND-001: an aliased import must not evade an access-site finder.

MEASURED (registry, a7d1f1a; reproduced on ef89dcd before the fix): ``import os as _o; _o.getcwd()`` planted in a strict CONTEXT
file passed the ownership tripwire, because the finders matched the plain module name only. One planted control per finder: the
plain spelling (already found), the ``import x as y`` spelling and, where the finder is about a class, the ``from x import C as D``
and ``x.C`` spellings. A negative control per finder proves an unrelated name with the same attribute is not a site.
"""
from __future__ import annotations

import ast

import pytest

from . import access_sites as sites

CASES = [
    # finder, source, expected line numbers
    (sites.cwd_sites, "import os\nos.getcwd()\n", {2}),
    (sites.cwd_sites, "import os as _o\n_o.getcwd()\n", {2}),
    (sites.cwd_sites, "from pathlib import Path as P\nP.cwd()\n", {2}),
    (sites.cwd_sites, "import pathlib as pl\npl.Path.cwd()\n", {2}),
    (sites.cwd_sites, "import pathlib\npathlib.Path.cwd()\n", {2}),
    (sites.environ_sites, "import os as _o\n_o.environ['X']\n_o.getenv('X')\n", {2, 3}),
    (sites.home_sites, "from pathlib import Path as P\nP.home()\n", {2}),
    (sites.home_sites, "import pathlib as pl\npl.Path.home()\n", {2}),
    (sites.subprocess_sites, "import subprocess as sp\nsp.run(['true'])\n", {2}),
    (sites.sqlite_sites, "import sqlite3 as sq\nsq.connect(':memory:')\n", {2}),
    (sites.http_sites, "import urllib as u\nu.request.urlopen('http://x')\n", {2}),
    (sites.http_sites, "import httpx as h\nh.get('http://x')\n", {2}),
]
NOT_SITES = [
    (sites.cwd_sites, "class Other:\n    pass\nother = Other()\nother.getcwd()\nother.cwd()\n"),
    (sites.environ_sites, "config = object()\nconfig.environ\n"),
    (sites.home_sites, "class Repo:\n    pass\nRepo.home()\n"),
    (sites.subprocess_sites, "import json as sp\nsp.dumps({})\n"),
    (sites.sqlite_sites, "import json as sq\nsq.loads('1')\n"),
]


@pytest.mark.parametrize("finder, source, expected", CASES, ids=[f"{c[0].__name__}:{c[1].splitlines()[0]}" for c in CASES])
def test_a_planted_alias_is_a_site(finder, source, expected):
    assert finder(ast.parse(source)) == expected


@pytest.mark.parametrize("finder, source", NOT_SITES, ids=[f"{c[0].__name__}:{c[1].splitlines()[0]}" for c in NOT_SITES])
def test_an_unrelated_name_with_the_same_attribute_is_not_a_site(finder, source):
    assert finder(ast.parse(source)) == set()
