"""B1 (owner walkthrough 2026-11-07): the cockpit's "Project folder" row has one row per recovery status Kriya reports. A new backend ``STATUS_*`` must not ship without a UI row, or the UI would
show it as "State not recognised" (the safe default) instead of its meaning. The UI package may not read the repository (ui/shared is host-independent), so the parity is pinned here."""
import re
from pathlib import Path

from kriya.control import recovery

READINESS = Path(__file__).resolve().parent.parent / "ui" / "shared" / "src" / "cockpit" / "modules" / "workspace" / "Readiness.tsx"


def _ui_rows() -> set:
    source = READINESS.read_text(encoding="utf-8")
    block = source[source.index("export const FOLDER_FACTS"):source.index("export function folderFact")]
    return set(re.findall(r"^  ([A-Z_]+): \{ status:", block, re.M))


def test_every_recovery_status_the_backend_defines_has_exactly_one_ui_row():
    backend = {value for name, value in vars(recovery).items() if name.startswith("STATUS_") and isinstance(value, str)}
    assert len(backend) >= 5
    assert _ui_rows() == backend


def test_the_parity_check_reads_real_rows():
    assert {"CLEAN", "RUN_ACTIVE"} <= _ui_rows()
