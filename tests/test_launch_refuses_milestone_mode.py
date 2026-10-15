"""Demo V2 Phase 9 (milestones.py replay classified UNREACHABLE FROM THE DESKTOP): the replay of persisted milestone commands runs only under ``generate --from-milestones``, and a desktop launch
(``generate --json --launch-id``) refuses every option that selects another entry point - this one included. The classification rests on this refusal; it is pinned here so it cannot silently
change (MILESTONE-REPLAY-UNAPPROVED-COMMANDS-001)."""
import pytest
from click.testing import CliRunner

from kriya.cli import main

LAUNCH_ID = "20261010T101010-abcdef01"


@pytest.mark.parametrize("option", [["--from-milestones", "plan.json"], ["--resume"], ["--yes"], ["--file", "goal.md"], ["--acceptance", "a.json"], ["--ack-knowledge-gap", "kg1"]])
def test_a_desktop_launch_refuses_the_options_that_select_another_entry_point(tmp_path, option):
    for name in ("plan.json", "goal.md", "a.json"):   # click checks that a path option exists before the command body runs
        (tmp_path / name).write_text("{}")
    argv = [str(tmp_path / a) if a in ("plan.json", "goal.md", "a.json") else a for a in option]
    result = CliRunner().invoke(main, ["generate", "--json", "--launch-id", LAUNCH_ID, *argv], input="Create a small app\n")
    assert result.exit_code == 2, result.output
    assert "cannot be combined with" in result.output and option[0] in result.output
