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
# Run Cockpit v1.1 (handover/GUI-RUN-COCKPIT-V1_1/04): additive operations, error codes, one consistency kind and feature
# flags inside schema version 1, discoverable through ``capabilities``. A host that only knows the M1 set ignores them.
CAPABILITY_SET = "1.1"

OPERATIONS = (
    "capabilities",
    "history.list",
    "history.detail",
    "history.prompt",
    "workspace.status",
    "workspace.describe",  # read-only facts Kriya reports about a folder the desktop host picked (GUI M5)
    "execution.preflight",  # what a run in a folder would be: configuration, state, models, authority, observability (GUI M6)
    "launch.status",  # one desktop launch read back from durable records (GUI M6)
    "workspace.create",  # create ONE new empty project folder (end-user experience); the only operation that makes a user-visible folder
    "execution.context",  # Skills, tools and per-role model routing a run in a folder would have; configuration/file-derived, starts nothing
    "execution.model_probe",  # one bounded local GET /models per distinct endpoint: is the model server answering and does it serve the model
    "skills.list",  # Demo V2 (read-only catalog): Skills a run in a folder would load, as SkillDescriptors; starts nothing
    "skills.describe",  # one SkillDescriptor by id (name), incl. provenance summary; never rule text
    "tools.list",  # configured MCP servers and enabled plugins as ToolDescriptors; starts nothing
    "tools.describe",  # one ToolDescriptor by id (mcp:<server> / plugin:<name>); no secret material
    "tools.approval_state",  # what the durable invocation-approval store can say about one tool without starting it (never APPROVED)
    "configuration.approval_status",  # which operator-controlled settings of a folder need approval, and whether a current approval covers them; names only, never values
    "configuration.approve",  # record the approval for exactly the configuration the person reviewed (a typed {workspace, fingerprint}); the same write the CLI makes
    "change_approval.review",  # the EXACT proposed change a pending (or decided) review asks the person to approve, verified against the review's digests; approval-scoped
    "snapshot.acquire",
    "snapshot.list",
    "snapshot.prune",
    "snapshot.verify",  # SHA-256 of exactly one published snapshot (digest verified at pin; metadata checked per query)
    "evidence.manifest",  # evidence-store metadata: capture mode, seal, gaps, DERIVED absence reason
    "evidence.verify",  # chain, blobs and seal of one run's evidence store, recomputed now
    "evidence.records",  # paged, filtered, verbatim records of a verified store
    "evidence.change_content",  # ONE authorized, capped part (diff/before/after) of ONE recorded candidate change
    "evidence.call_content",  # ONE authorized, capped part (messages/tools/wire body) of ONE recorded model request
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
EVIDENCE_STORE_UNREADABLE = "EVIDENCE_STORE_UNREADABLE"      # permission or I/O failure on the store (extra: path_kind)
EVIDENCE_SCHEMA_UNSUPPORTED = "EVIDENCE_SCHEMA_UNSUPPORTED"  # a manifest/record schema this reader does not support (extra: schema_seen)
EVIDENCE_STORE_NOT_FOUND = "EVIDENCE_STORE_NOT_FOUND"        # no store for the run (operations that need records, not a manifest)
EVIDENCE_VERIFY_FAILED = "EVIDENCE_VERIFY_FAILED"            # status other than VERIFIED/UNSEALED (extra: status, broken_seq, blob_problems, detail)
EVIDENCE_HEAD_CHANGED = "EVIDENCE_HEAD_CHANGED"              # a cursor's chain head is no longer the store's head (extra: head_then, head_now)
EVIDENCE_CONTENT_POLICY = "EVIDENCE_CONTENT_POLICY"          # content refused by Kriya's visibility policy or class table (extra: content_class, reason)
EVIDENCE_CONTENT_UNAVAILABLE = "EVIDENCE_CONTENT_UNAVAILABLE"  # authorized, but the store holds no bytes for that part (extra: reason)
WORKSPACE_UNAVAILABLE = "WORKSPACE_UNAVAILABLE"              # a folder to open is missing, not a directory or unreadable (extra: reason)
LAUNCH_NOT_FOUND = "LAUNCH_NOT_FOUND"                        # no launch record with that id for the folder (extra: reason)
EVIDENCE_BLOB_TOO_LARGE = "EVIDENCE_BLOB_TOO_LARGE"          # the part is over the response cap (extra: bytes_total)
REVIEW_NOT_FOUND = "REVIEW_NOT_FOUND"                        # the launch has no review with that id (extra: reason)
REVIEW_DIFF_UNAVAILABLE = "REVIEW_DIFF_UNAVAILABLE"          # the review exists but its diff text was not stored, or the document is missing/foreign/corrupt: it cannot be inspected
REVIEW_DIFF_UNVERIFIED = "REVIEW_DIFF_UNVERIFIED"            # the stored diff text does not match the digests the review is bound to: it is never served (extra: reason)
APPROVAL_STALE = "APPROVAL_STALE"                            # the configuration is not the one the person reviewed; nothing was approved (extra: reason)
APPROVAL_UNAVAILABLE = "APPROVAL_UNAVAILABLE"                # an approval cannot be recorded here: trust file in effect, store inside the folder, store unwritable (extra: reason)
ERROR_CODES = (
    UNSUPPORTED_SCHEMA_VERSION, INVALID_RESPONSE, INVALID_REQUEST, STORE_BUSY, READ_ONLY_UNAVAILABLE,
    RESPONSE_TOO_LARGE, CONFIG_AUTHORITY_REFUSED, CONFIG_LOAD_FAILED, SNAPSHOT_MISSING, SNAPSHOT_UNAVAILABLE, SNAPSHOT_FAILED,
    SNAPSHOT_TOO_LARGE, SNAPSHOT_CORRUPT, ACQUISITION_IN_PROGRESS, ACQUISITION_REFUSED_RUN_ACTIVE,
    EVIDENCE_STORE_UNREADABLE, EVIDENCE_SCHEMA_UNSUPPORTED, EVIDENCE_STORE_NOT_FOUND, EVIDENCE_VERIFY_FAILED, EVIDENCE_HEAD_CHANGED,
    EVIDENCE_CONTENT_POLICY, EVIDENCE_CONTENT_UNAVAILABLE, EVIDENCE_BLOB_TOO_LARGE, WORKSPACE_UNAVAILABLE, LAUNCH_NOT_FOUND,
    APPROVAL_STALE, APPROVAL_UNAVAILABLE, REVIEW_NOT_FOUND, REVIEW_DIFF_UNAVAILABLE, REVIEW_DIFF_UNVERIFIED,
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
# sealed_store: returned only by an operation that verified the chain, every referenced blob and the seal of a SEALED store in
# the same invocation, for the head_digest it reports (04 section 5 rule 0). Never for an unsealed or failing store.
CONSISTENCY_SEALED_STORE = "sealed_store"
CONSISTENCY_KINDS = (CONSISTENCY_SNAPSHOT_COPY, CONSISTENCY_LIVE_OBSERVATION, CONSISTENCY_NOT_APPLICABLE, CONSISTENCY_SEALED_STORE)

# Evidence-store verification states in the order the capabilities list them (the reader's own statuses, 04 section 3).
EVIDENCE_VERIFICATION_STATES = ("VERIFIED", "UNSEALED", "NOT_FOUND", "CHAIN_BROKEN", "TRUNCATED_TAIL", "BLOB_CORRUPT", "SEAL_MISMATCH")
# evidence.manifest absence reasons (DERIVED from the S1 evidence.attempt_store event; 03 matrix row 27).
EVIDENCE_ABSENCE_REASONS = ("pruned", "capture_off", "recorder_unavailable", "pre_recorder", "unknown")

# Limits
LIST_DEFAULT = 50
LIST_MAX = 200
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
RUN_ID_MAX = 256
CURSOR_MAX = 1024
EVIDENCE_PAGE_DEFAULT = 200
EVIDENCE_PAGE_MAX = 1000
EVIDENCE_PAGE_MAX_BYTES = MAX_RESPONSE_BYTES // 2  # a page stops (truncated: true) before the serialized records exceed this
EVIDENCE_FILTER_VALUE_MAX = 256
EVIDENCE_BLOB_MAX_BYTES = 1024 * 1024  # one content part per response; larger is refused from its recorded size, never truncated

# Content classes (SECURITY_AUTHORITY). The table covers EVERY blob key the current producers emit for a candidate change
# (attempt.py: before/after/diff; scope.py refused candidates: proposed/attempted_edits), enforced by a producer-scanning test.
# A key that is not listed is `unclassified` and is never served, whatever the visibility setting says.
CONTENT_CLASS_CANDIDATE = "candidate_content"
CONTENT_CLASS_MODEL_INPUT = "model_input"  # what a model request carried (messages, tools, wire body); never the model's answer
CONTENT_CLASS_UNCLASSIFIED = "unclassified"
BLOB_CLASSES = {
    "before": CONTENT_CLASS_CANDIDATE, "after": CONTENT_CLASS_CANDIDATE, "diff": CONTENT_CLASS_CANDIDATE,
    "proposed": CONTENT_CLASS_CANDIDATE, "attempted_edits": CONTENT_CLASS_CANDIDATE,
    # scope.py model.request producers: record_wire_request (messages, tools, wire_body) and record_undispatched_call (messages, tools)
    "messages": CONTENT_CLASS_MODEL_INPUT, "tools": CONTENT_CLASS_MODEL_INPUT, "wire_body": CONTENT_CLASS_MODEL_INPUT,
}
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
    "evidence_page_default": EVIDENCE_PAGE_DEFAULT,
    "evidence_page_max": EVIDENCE_PAGE_MAX,
    "evidence_page_max_bytes": EVIDENCE_PAGE_MAX_BYTES,
    "evidence_content_max_bytes": EVIDENCE_BLOB_MAX_BYTES,
    "max_snapshot_bytes": MAX_SNAPSHOT_BYTES,
    "retain_snapshots": RETAIN_SNAPSHOTS,
    "retained_bytes_ceiling": RETAINED_BYTES_CEILING,
    "directory_ceiling_bytes": DIRECTORY_CEILING_BYTES,
    "acquisition_deadline_seconds": ACQUISITION_DEADLINE_SECONDS,
    "busy_timeout_seconds": BUSY_TIMEOUT_SECONDS,
}

# display_goal (PRODUCT-UX-COMPLETION): the header Kriya's compiled unit prompt starts with. It is used ONLY as a discriminator to WITHHOLD a row's own goal
# from display when that row is a compiled unit goal with no resolvable link to its original request; the original request is never extracted from it.
# A test pins this equal to kriya.agents.contracts.AUTHORITATIVE_GOAL_SECTION_HEADER.
COMPILED_GOAL_HEADER = "=== Authoritative Goal ==="
DISPLAY_GOAL_OWN = "own"
DISPLAY_GOAL_ENFORCE_ROW = "enforce_row"
DISPLAY_GOAL_UNAVAILABLE = "unavailable"
