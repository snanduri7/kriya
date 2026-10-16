"""REPOSITORY (full modularization; MODULE-CONTRACTS §11, TARGET-MODULE-MAP §1 row 10).

The repository write/commit mechanism: ``repository.atomic_write`` (the atomic replacement of one file),
``repository.commit`` (the revision-grounded batch commit and its durable commit evidence) and
``repository.snapshot`` (byte-exact file snapshots and raw-byte revision identity), moved from
kriya/workflow/edit_safety.py and kriya/workflow/file_integrity.py in slice M1; the worktree lifecycle, git
metadata and git helpers arrive in M2, ``RepositoryPort`` in M3. Import the submodules directly: this package
module re-exports nothing, so importing one submodule never loads the others.
"""
