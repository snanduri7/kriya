# PROVIDER-CONTRACT-001 Batch B - verification record (2026-09-30)

Base: b029e7e (Batch A) + Batch B working tree.

## Full pytest (once) - full_pytest.txt
7525 passed, 0 failed, 72 deselected (28m50s).

Before it: Batch B adjacent suites (53 files) 1364 passed, 2 skipped. 17 failures on the first
adjacent run were classified before any change:
- fixtures modelling UNPINNED served models (no num_ctx PARAMETER) now correctly refused by
  QUALIFICATION_IDENTITY_UNVERIFIED -> fixtures pinned (server_parameters num_ctx = requested);
- identity pins (inference settings /3, policy /4) -> re-pinned, superseded digests kept;
- capacity-case doubles that read the served window back from the request's options.num_ctx
  (the measured P0-1 false premise) -> the fake server now serves its own window; the case is
  asserted to send no provider options.
One regression of my own found by the adjacent run and corrected before commit: removing the
half-window output cap starved small windows (a 4096-token window with the default 4096-token
output left no prompt room; test_context_edit_protocol_001 small-window case). A configured
output is a ceiling, not the protocol need; the cap stays, applied to the now role- and
identity-specific reserve.

## Mutations (mutate_b.py.txt) - all KILLED
measured reasoning reserve; request carries it; reviewer/planner role budgets; server_config in
identity; qualification identity check (whole, and the unverified clause); capacity served-window
check; tokenizer ceiling; policy version; ceiling aggregated by max across records.

## Static
ruff (kriya tests plugins scripts) clean; pylint clean.
