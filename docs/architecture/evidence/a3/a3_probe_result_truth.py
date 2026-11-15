"""Slice A3 probe P8: what a direct run REPORTS when its only requirement stays open under the default record policy.

It drives the committed scripted scenario of tests/test_project_continuity_two_run.py (control F: the candidate writes a
substitute app.py beside an unchanged main.py) through the real direct pipeline, model-free, and prints the result fields
the committed test does not assert. It runs under pytest so tests/conftest.py isolates state exactly as for the committed test:

    <python> -m pytest -q -p no:cacheprovider -s --rootdir <closure worktree> -c <closure worktree>/pyproject.toml \
        --confcutdir <closure worktree> docs/architecture/evidence/a3/a3_probe_result_truth.py

It asserts nothing about what the values should be: it prints them. Read the printed lines, not the pass mark.
"""
from tests.conftest import *  # noqa: F401,F403 - the committed isolation fixtures (autouse) for this probe module
from tests.test_project_continuity_two_run import _wrong_file_run

FIELDS = ("quality_gates_passed", "candidate_gates_passed", "terminal_regression_passed", "overall_attempt_passed", "failure_category", "files")


def _show(label, workspace, result):
    print(f"\nP8 {label}")
    for field in FIELDS:
        print(f"  {field} = {result.get(field)!r}")
    print(f"  requirements.outcomes = {(result.get('requirements') or {}).get('outcomes')!r}")
    truth = result.get("requirement_truth") or {}
    print(f"  requirement_truth = status {truth.get('status')!r}, verified {truth.get('verified')!r}, accepted {truth.get('accepted')!r}, total {truth.get('total')!r}")
    print(f"  app.py in workspace = {(workspace / 'app.py').exists()} | main.py unchanged = {(workspace / 'main.py').read_text() == 'print(' + chr(34) + 'Hello World' + chr(34) + ')' + chr(10)}")


def test_p8_record_policy(tmp_path):
    workspace, _, result = _wrong_file_run(tmp_path)
    _show("default policies (requirement_unverified_policy=record)", workspace, result)


def test_p8_block_policy(tmp_path):
    workspace, _, result = _wrong_file_run(tmp_path, requirement_unverified_policy="block")
    _show("requirement_unverified_policy=block", workspace, result)
