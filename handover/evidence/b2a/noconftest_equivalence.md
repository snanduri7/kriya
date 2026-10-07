# Mutant `candidate-conftest-trusted` (remove `--noconftest`): SURVIVED -> EQUIVALENT (measured)

Run 1 on `7a39256`: the only survivor of 25. Discriminating measurement (fresh clone of `7a39256` with exactly that
mutation, calc fixture, conftest.py files that write a marker when imported at the candidate root, in `calc/`, in
`.kriya/` and in `.kriya/acceptance-runs/`): judgment `ACCEPTANCE_PASSED`, no marker file written, no conftest among the
registered plugins.

Why (TRACED, pytest): conftest files are collected from each argument's directory up to `confcutdir`, which defaults to
the rootdir; Kriya sets the rootdir (and the `-c` ini's directory) to the fresh per-run staging directory
`.kriya/acceptance-runs/<uuid>/`, which holds only Kriya's four files and which no candidate write can reach. So no
candidate conftest is in reach with or without `--noconftest`. Independently, any conftest that did load would register
as a plugin and `judge_acceptance` refuses any plugin outside `_pytest.*` (mutant `foreign-plugin-ignored`: KILLED).

Decision: keep `--noconftest` as explicit defense in depth (the owner's boundary names candidate conftest files; the
flag costs nothing and stays correct if the staging layout ever changes). Not removed, recorded as equivalent.
