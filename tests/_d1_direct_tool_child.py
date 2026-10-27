"""Child process of tests/test_d1_direct_tool.py: runs `kriya tools execute` and dies with os._exit(137) at the requested window (no finalizer runs: a real kill).
usage: _d1_direct_tool_child.py <base-dir> <window>"""
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _d1_direct_tool_support as S  # noqa: E402

base, window = pathlib.Path(sys.argv[1]), sys.argv[2]
workspace, plugins, effects = base / "ws", base / "plugins", base / "effects.log"
if window == "after_effect_before_completed":
    from kriya import cli
    from kriya.control.run_record import RunLifecycle
    real = cli.transition_mutating_run

    def transition(context, state, **kwargs):
        if state == RunLifecycle.SUCCESS:
            os._exit(137)                                       # the effect finished; its COMPLETED was never written
        return real(context, state, **kwargs)
    cli.transition_mutating_run = transition
S.execute_tool(workspace, plugins, effects, window=window if window in ("before_effect", "during_effect") else "")
sys.exit(0)
