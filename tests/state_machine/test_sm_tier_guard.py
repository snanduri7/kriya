"""The state-machine tier stays fast and deterministic: `pytest -m
state_machine` replaces the default `-m` expression, so no tier test may
carry a live marker, and every tier file named in conftest.py exists."""
import ast
import pathlib

import pytest
from conftest import STATE_MACHINE_TIER_FILES

pytestmark = pytest.mark.state_machine

TESTS = pathlib.Path(__file__).resolve().parent.parent
LIVE_MARKERS = {"live_model", "live_target", "live_certification", "live_static_analysis"}


def _tier_files():
    yield from sorted((TESTS / "state_machine").glob("test_*.py"))
    yield from sorted(TESTS / name for name in STATE_MACHINE_TIER_FILES)


def test_every_tier_file_exists():
    assert all((TESTS / name).is_file() for name in STATE_MACHINE_TIER_FILES)


def test_no_tier_test_carries_a_live_marker():
    offenders = []
    for path in _tier_files():
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Attribute) and node.attr in LIVE_MARKERS \
                    and isinstance(node.value, ast.Attribute) and node.value.attr == "mark":
                offenders.append(f"{path.name}: {node.attr}")
    assert not offenders
