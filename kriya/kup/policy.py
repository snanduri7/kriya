"""KUP constants: protocol identity, error codes, consistency kinds and the resource policy (gate C-4).

Resource policy (coherent set, documented here and in ui/docs/D4_STABLE_SNAPSHOT_READ_STRATEGY.md §4/§8):

* ``MAX_SNAPSHOT_BYTES`` = 512 MiB: the bound on the DESTINATION file's actual growth, checked after every backup
  step, plus an up-front refusal when the source's page_count x page_size (read inside the same transaction the
  backup uses) already exceeds it.
* Staging peak: at most ONE acquisition runs at a time (exclusive lock), so staging never holds more than one
  snapshot-in-progress: peak extra space = MAX_SNAPSHOT_BYTES.
* ``RETAIN_SNAPSHOTS`` = 3 newest published snapshots are kept by automatic retention (run at the end of a
  successful acquisition); ``RETAINED_BYTES_CEILING`` = 3 x 512 MiB = 1.5 GiB is therefore the retained maximum and
  ``DIRECTORY_CEILING_BYTES`` = 2 GiB (retained + one staging peak) the directory maximum. Acquisition refuses
  (``SNAPSHOT_FAILED``/``insufficient_space``) when the free space on the snapshot volume is below the expected
  destination size plus ``FREE_SPACE_MARGIN_BYTES``; it never deletes published snapshots to make room beyond the
  normal retention of the newest three, and never deletes anything that is not its own artifact.
* A single oversized snapshot: refused typed (``SNAPSHOT_TOO_LARGE``), staging removed, nothing published.
* "Pinned" snapshots: the host pins a browsing session to the snapshot id it acquired or chose; retention keeps the
  newest three, so a pinned snapshot survives two further acquisitions. A pinned snapshot that retention has removed
  yields the typed ``SNAPSHOT_UNAVAILABLE`` - never a silent substitute.
* ``ACQUISITION_DEADLINE_SECONDS`` = 45 (inside the host's 60 s process timeout), checked between backup steps;
  ``BUSY_TIMEOUT_SECONDS`` = 2 for the source connection.
"""
from __future__ import annotations

KUP_SCHEMA_VERSION = 1
PROTOCOL_IMPLEMENTATION = "kriya-kup/1"

OPERATIONS = (
    "capabilities",
    "history.list",
    "history.detail",
    "history.prompt",
    "workspace.status",
    "snapshot.acquire",
    "snapshot.list",
    "snapshot.prune",
)

# Error codes (closed vocabulary; the host shows an unknown code literally).
UNSUPPORTED_SCHEMA_VERSION = "UNSUPPORTED_SCHEMA_VERSION"
INVALID_RESPONSE = "INVALID_RESPONSE"
INVALID_REQUEST = "INVALID_REQUEST"
STORE_BUSY = "STORE_BUSY"
READ_ONLY_UNAVAILABLE = "READ_ONLY_UNAVAILABLE"
RESPONSE_TOO_LARGE = "RESPONSE_TOO_LARGE"
CONFIG_AUTHORITY_REFUSED = "CONFIG_AUTHORITY_REFUSED"
CONFIG_LOAD_FAILED = "CONFIG_LOAD_FAILED"      # configuration failed to load for a reason other than SEC-009
SNAPSHOT_MISSING = "SNAPSHOT_MISSING"          # no published snapshot exists at all
SNAPSHOT_UNAVAILABLE = "SNAPSHOT_UNAVAILABLE"  # the requested snapshot id is not published (pruned, never existed)
SNAPSHOT_FAILED = "SNAPSHOT_FAILED"
SNAPSHOT_TOO_LARGE = "SNAPSHOT_TOO_LARGE"
SNAPSHOT_CORRUPT = "SNAPSHOT_CORRUPT"
ACQUISITION_IN_PROGRESS = "ACQUISITION_IN_PROGRESS"
ACQUISITION_REFUSED_RUN_ACTIVE = "ACQUISITION_REFUSED_RUN_ACTIVE"
ERROR_CODES = (
    UNSUPPORTED_SCHEMA_VERSION, INVALID_RESPONSE, INVALID_REQUEST, STORE_BUSY, READ_ONLY_UNAVAILABLE,
    RESPONSE_TOO_LARGE, CONFIG_AUTHORITY_REFUSED, CONFIG_LOAD_FAILED, SNAPSHOT_MISSING, SNAPSHOT_UNAVAILABLE, SNAPSHOT_FAILED,
    SNAPSHOT_TOO_LARGE, SNAPSHOT_CORRUPT, ACQUISITION_IN_PROGRESS, ACQUISITION_REFUSED_RUN_ACTIVE,
)

# database_state vocabulary (closed) reported with STORE_BUSY / READ_ONLY_UNAVAILABLE / SNAPSHOT_* errors.
DB_STATE_MISSING = "missing"
DB_STATE_HOT_JOURNAL = "hot_journal"
DB_STATE_LOCKED = "locked"
DB_STATE_READONLY_DIRECTORY = "readonly_directory"
DB_STATE_UNREADABLE = "unreadable"
DB_STATE_NOT_A_DATABASE = "not_a_database"
DATABASE_STATES = (DB_STATE_MISSING, DB_STATE_HOT_JOURNAL, DB_STATE_LOCKED, DB_STATE_READONLY_DIRECTORY,
                   DB_STATE_UNREADABLE, DB_STATE_NOT_A_DATABASE)

# consistency.kind (closed vocabulary, gate C-3)
CONSISTENCY_SNAPSHOT_COPY = "snapshot_copy"      # history ops and snapshot.acquire: one committed image via backup
CONSISTENCY_LIVE_OBSERVATION = "live_observation"  # workspace.status, snapshot.list, snapshot.prune: a point observation
CONSISTENCY_NOT_APPLICABLE = "not_applicable"    # capabilities
CONSISTENCY_KINDS = (CONSISTENCY_SNAPSHOT_COPY, CONSISTENCY_LIVE_OBSERVATION, CONSISTENCY_NOT_APPLICABLE)

# Limits
LIST_DEFAULT = 50
LIST_MAX = 200
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
RUN_ID_MAX = 256
CURSOR_MAX = 1024
MAX_SNAPSHOT_BYTES = 512 * 1024 * 1024
RETAIN_SNAPSHOTS = 3
RETAINED_BYTES_CEILING = RETAIN_SNAPSHOTS * MAX_SNAPSHOT_BYTES
DIRECTORY_CEILING_BYTES = RETAINED_BYTES_CEILING + MAX_SNAPSHOT_BYTES
FREE_SPACE_MARGIN_BYTES = 64 * 1024 * 1024
ACQUISITION_DEADLINE_SECONDS = 45.0
BUSY_TIMEOUT_SECONDS = 2.0
BACKUP_STEP_PAGES = 256

SNAPSHOT_DIRNAME = "kup-snapshots"
SNAPSHOT_FILENAME = "traces.snapshot.db"
MANIFEST_FILENAME = "manifest.json"
STAGING_PREFIX = "staging-"
LOCK_FILENAME = ".acquire.lock"
MANIFEST_VERSION = 1

LIMITS = {
    "list_default": LIST_DEFAULT,
    "list_max": LIST_MAX,
    "max_response_bytes": MAX_RESPONSE_BYTES,
    "run_id_max_chars": RUN_ID_MAX,
    "cursor_max_chars": CURSOR_MAX,
    "max_snapshot_bytes": MAX_SNAPSHOT_BYTES,
    "retain_snapshots": RETAIN_SNAPSHOTS,
    "retained_bytes_ceiling": RETAINED_BYTES_CEILING,
    "directory_ceiling_bytes": DIRECTORY_CEILING_BYTES,
    "acquisition_deadline_seconds": ACQUISITION_DEADLINE_SECONDS,
    "busy_timeout_seconds": BUSY_TIMEOUT_SECONDS,
}
