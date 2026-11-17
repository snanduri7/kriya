"""S8 (C-08): a test that writes an earlier run's state by hand (a ControlState, a checkpoint) instead of running that run must also write the work ledger the
run would have left - a requested resume continues that ledger or is refused (``resume_refused``), and state without one is pre-ledger state: not resumable."""
from kriya.control.persistence import save_work_ledger
from kriya.control.work_ledger import WorkLedger


def seed_work_ledger(workspace, run_id: str) -> None:
    """The ledger of an earlier run ``run_id`` that consumed nothing."""
    save_work_ledger(str(workspace), WorkLedger.new(run_id).to_dict())
