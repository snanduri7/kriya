"""Facade (full modularization, slice D2): the run trace store (``TraceLogger``, the ``runs``/``milestone_plans`` tables of
``<state dir>/traces.db``) moved unchanged to its STATE owner ``kriya/control/trace.py``. This old import path is kept for its importers
(17 test files and fixtures); every name is the owner's object. Removed in P1."""
from kriya.control.trace import TraceLogger as TraceLogger
