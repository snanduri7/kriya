"""KUP - the Kriya UI Protocol read path (GUI M1 Phase C; handover/GUI-D4-READ-STRATEGY/03_GATE.md).

Two operations with two different authorities, never merged:

* **Acquisition** (``kriya traces --json --snapshot``): explicit, Kriya-owned. Opens the resolved live history store
  read-only and copies it with SQLite's online backup API inside ONE read transaction into a staging directory under
  the state directory, converts the COPY to a rollback journal, verifies it, writes a manifest and publishes the
  directory atomically. SQLite may create/modify the source's ``-wal``/``-shm`` while it reads (gate: permitted);
  acquisition never writes a row or a main-file page of the source, never checkpoints it, never changes its
  journal mode, and never touches the workspace or configuration.
* **Inspection** (``kriya traces --json --snapshot-id ID ...``): strictly write-free. Reads ONLY a published
  snapshot with ``file:...?mode=ro`` (A1 case C01: zero write attempts under ``(deny file-write*)``). No implicit
  acquisition, no live-store fallback, no ``immutable=1``, no ``-shm`` exception (D-4 as written).

Authority of the snapshot directory (gate C-1, TRACED rather than assumed): the snapshot directory is
``<state dir>/kup-snapshots``. The state directory is resolved only by ``kriya/core/state_paths.py::
resolve_state_directory`` (``KRIYA_STATE_DIR`` > ``paths.state`` > ``~/.kriya/state``). A configured ``paths.state``
is canonicalized once at config load (``kriya/config/config.py::resolve_config_state``), classified by SEC-009
(``kriya/config/authority.py::path_field_classification``: REPOSITORY_SAFE only while it stays inside the config
file's own directory, SECURITY_AUTHORITY - approval required - otherwise) and, inside a workspace, confined beneath
``<workspace>/.kriya/`` (``state_paths.require_workspace_local_state_under_kriya_dir``). Kriya already writes
``traces.db`` into exactly that directory (``kriya/core/trace.py::TraceLogger``). A subdirectory of it carries the
same, already-decided authority; nothing here resolves a path from configuration or the CWD on its own, reads a
trust file, or retries with broader permission.
"""
