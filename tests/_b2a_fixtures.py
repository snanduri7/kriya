"""FS-1C2 B2-a fixtures: candidate projects the operator acceptance file runs on.

``freezegun_project`` materializes the A1 base (spulec/freezegun @ 92d61b3,
byte-identical, tests/fixtures/b2a/NOTICE.txt) as a flat package, with the
candidate variant applied:

- ``base``: unchanged (``freeze_time(0)`` raises TypeError);
- ``helper_only``: the live A1 candidate Kriya refused at the FS-1C1 sentinel
  (and applied as a false success before FS-1C1): the int/float branch in
  ``_parse_time_to_freeze`` only - its bytes equal the live candidate's
  recorded after-digest;
- ``correct``: the same plus int/float accepted by ``freeze_time`` itself.

python-dateutil (freezegun's only import outside the stdlib) is not in Kriya's
test environment; a minimal stand-in package beside freezegun provides exactly
what freezegun imports.
"""
import hashlib
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "b2a"
A1_GOAL = (Path(__file__).resolve().parent / "fixtures" / "fs1c1" / "a1_goal.txt").read_text()
A1_ACCEPTANCE = (FIXTURES / "a1_acceptance.py.txt").read_text()
LIVE_BASE_DIGEST = "f0d67017e0fd5da0641586aa35eabcb6172654fd7ec22f2dad72667db0b857d6"
LIVE_CANDIDATE_DIGEST = "10e7878233a2cc68a5b5471264dc8618fc222dd7eeecaabd6000714e78c3f003"

_TIMEDELTA_BRANCH = (
    "    elif isinstance(time_to_freeze_str, datetime.timedelta):\n"
    "        time_to_freeze = datetime.datetime.now(datetime.timezone.utc) + time_to_freeze_str\n"
)
_HELPER_BRANCH = (
    "    elif isinstance(time_to_freeze_str, (int, float)):\n"
    "        time_to_freeze = datetime.datetime.fromtimestamp(time_to_freeze_str, tz=datetime.timezone.utc)\n"
)
_ACCEPTABLE = ("    acceptable_times: Any = (type(None), str, datetime.date, datetime.timedelta,\n"
               "             types.FunctionType, types.GeneratorType)\n")
_ACCEPTABLE_NUMBERS = ("    acceptable_times: Any = (type(None), str, datetime.date, datetime.timedelta,\n"
                       "             types.FunctionType, types.GeneratorType, int, float)\n")

DATEUTIL_STAND_IN = {
    "dateutil/__init__.py": '"""Test stand-in for python-dateutil: what freezegun imports."""\nfrom . import parser, tz  # noqa: F401\n',
    "dateutil/tz.py": ("import datetime\n\nUTC = datetime.timezone.utc\n\n\n"
                       "def tzoffset(name, offset):\n    return datetime.timezone(datetime.timedelta(seconds=offset), name)\n\n\n"
                       "def tzlocal():\n    return datetime.timezone.utc\n"),
    "dateutil/parser.py": "import datetime\n\n\ndef parse(text):\n    return datetime.datetime.fromisoformat(text)\n",
}


def freezegun_api(variant: str) -> str:
    source = (FIXTURES / "freezegun-92d61b3" / "api.py.txt").read_text()
    assert hashlib.sha256(source.encode()).hexdigest() == LIVE_BASE_DIGEST
    if variant in ("helper_only", "correct"):
        assert source.count(_TIMEDELTA_BRANCH) == 1
        source = source.replace(_TIMEDELTA_BRANCH, _TIMEDELTA_BRANCH + _HELPER_BRANCH)
    if variant == "correct":
        assert source.count(_ACCEPTABLE) == 1
        source = source.replace(_ACCEPTABLE, _ACCEPTABLE_NUMBERS)
    return source


def write_files(root: Path, files: dict) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def freezegun_project(root: Path, variant: str) -> Path:
    files = {f"freezegun/{name}": (FIXTURES / "freezegun-92d61b3" / f"{name}.txt").read_text()
             for name in ("__init__.py", "_async.py", "config.py")}
    files["freezegun/api.py"] = freezegun_api(variant)
    files["tests/__init__.py"] = ""
    return write_files(root, {**files, **DATEUTIL_STAND_IN})


# A small flat package for the generic cases.
CALC_GOAL = "Add a double(x) function to calc/__init__.py that returns 2 * x.\n"
CALC = {True: "def double(x):\n    return 2 * x\n", False: "def double(x):\n    return x + 2\n"}
CALC_ACCEPTANCE = (
    "import pytest\n\nfrom calc import double\n\n\n"
    "@pytest.mark.kriya_requirement(\"REQ-1\")\n"
    "def test_double_doubles():\n"
    "    assert double(5) == 10\n"
)


def calc_project(root: Path, correct: bool, extra: dict = None) -> Path:
    return write_files(root, {"calc/__init__.py": CALC[correct], **(extra or {})})
