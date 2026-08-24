"""Central execution-policy layer (MA4 of the control-plane implementation
plan; see kriya/workflow/triage.py's docstring for MA1, kriya/workflow/
process_profile.py's for MA2, session history / docs/design.md for the full
MA1-MA6 sequencing).

MA4's own principle, load-bearing for everything under this package: policy
DECIDES whether an action is allowed. It never becomes the mechanism that
performs or enforces the action - that responsibility stays exactly where it
already lives (kriya/core/llm.py's egress guard, ProcessController,
edit_safety.py, worktree.py, tools/web.py). Nothing in kriya/policy/ may
replace or weaken one of those existing guards; it is a new authorization
seam placed in front of them, always defense-in-depth, never a substitute.
"""
