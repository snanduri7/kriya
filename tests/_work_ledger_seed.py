"""S8 (C-08): a test that writes an earlier run's state by hand (a ControlState, a checkpoint) instead of running that run must also write the work ledger the
run would have left - a requested resume continues that ledger or is refused (``resume_refused``), and state without one is pre-ledger state: not resumable."""
import contextlib

from kriya.control.persistence import save_work_ledger
from kriya.control.run_coordinator import begin_mutating_run
from kriya.control.work_ledger import WorkLedger


def seed_work_ledger(workspace, run_id: str) -> None:
    """The ledger of an earlier run ``run_id`` that consumed nothing."""
    save_work_ledger(str(workspace), WorkLedger.new(run_id).to_dict())


@contextlib.contextmanager
def earlier_run(workspace):
    """State a test writes inside this block (a checkpoint) is written the way a run writes it - inside a mutating run, whose record the
    production writer stamps on it - and the ledger that run would have left (it consumed nothing) is seeded beside it when the block ends."""
    with begin_mutating_run(str(workspace)) as saving_run:
        yield saving_run
    seed_work_ledger(workspace, saving_run.run_id)
