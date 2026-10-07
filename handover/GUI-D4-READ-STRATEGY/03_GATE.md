# 03 — Owner gate: stable-snapshot acquisition and inspection

Owner: Sriram.
Date: 2026-10-04.
Recorded by: ChatGPT, from the owner's explicit approval in this chat: “yes I approve.”
Repository at approval: `codex/fix-demo1-attribution` @ `db16acb`.

## Decision: APPROVED WITH CONDITIONS

Approve the stable-snapshot strategy specified in `ui/docs/D4_STABLE_SNAPSHOT_READ_STRATEGY.md`, subject to the six binding conditions below. This approval follows ChatGPT's assessment and six conditions in the owner conversation. It does not approve the withdrawn guarded-immutable Q-1 proposal in `01_PROPOSAL.md`.

Inputs: `01_PROPOSAL.md`, `02_REVIEW.md`, `02a_PROPOSER_RESPONSE.md`, the stable-snapshot specification and fixture probe, and the owner's approval of the six conditions. This gate wins over conflicting recommendations in those inputs, including reuse of snapshots solely because source metadata matches.

## Authorized scope

Phase C may begin: implement the Kriya-owned acquisition operation, snapshot-backed KUP inspection, and the necessary schema/host-command additions. Keep Kriya changes in separate self-contained local `KUP:` commits, as required by the original GUI gate and implementation instructions.

Acquisition is a separately authorized operation. It may read the resolved live history store, permit SQLite's required creation/modification of that store's `-wal`/`-shm`, and create/publish/manage snapshot artifacts inside the authorized state directory. It must not write database rows or main database pages, checkpoint the source, change the source journal mode, or modify source/workspace/configuration files. Snapshot construction may use destination-only temporary SQLite artifacts and destination journal-mode conversion inside its own staging directory.

Inspection remains strictly write-free and reads only a published snapshot. No implicit acquisition, live-store query fallback, `immutable=1`, or inspection-side SHM exception is authorized. Acquisition copies sensitive data already in the store, including prompts; protect the snapshot directory (`0700`) and published files (`0400`), preserve prompt exclusion from normal KUP output, and restrict cleanup to owned snapshot artifacts.

## Binding conditions

### C-1 — Acquisition writes and UI intent are explicit

The UI distinguishes **Acquire new snapshot** from **Refresh displayed snapshot**. A query or display refresh never silently acquires. The host calls only the approved Kriya command grammar; Kriya owns authorization, store access, acquisition and cleanup.

Trace the snapshot directory's SEC-009/state-path authority in implementation, rather than assuming it inherits permission by name. No trust-file injection or broader-permission retry. Follow the specification's default policy of refusing acquisition when RUN_ACTIVE is observed for the selected workspace; that policy check is not proof that all writers to a store shared by other workspaces are absent. SQLite supplies acquisition consistency.

### C-2 — Browsing is pinned to a snapshot; concurrent lifecycle is safe

Acquisition returns an explicit snapshot ID. List, pagination, detail and prompt requests for that browsing session use the same ID, rather than independently selecting the newest snapshot. A user-requested snapshot switch invalidates prior responses and cursors.

Retention/prune handles snapshots being viewed safely and does not delete another active acquisition's temporary directory. If a snapshot is no longer available, return a typed error; never silently substitute another snapshot. Only proven abandoned, owned staging artifacts may be cleaned up. Define concurrent acquisition/prune behavior and test it.

### C-3 — Consistency and freshness labels are honest

Use `consistency.kind = snapshot_copy`: one consistent committed database image captured by SQLite backup. Record acquisition start/completion and inspection observation times; do not pretend a wall-clock timestamp identifies an exact commit unless that boundary is explicitly established.

Display **snapshot acquired at …**. Metadata differences may support **source metadata change detected**. Metadata equality does not establish unchanged contents, freshness or quiescence. Never label matching metadata as verified unchanged/current/latest, and never skip a requested new acquisition solely because the fingerprint matches. The snapshot is historical data; do not infer live run status or current qualification from it.

### C-4 — Acquisition size, time and storage are bounded

Enforce a bound on actual destination growth, not just the source main-file size, and an overall acquisition deadline including backup retries. Respect the existing 60-second host timeout and leave interrupted staging output unpublished.

Resolve the specification's 2 GiB per-snapshot proposal versus 1 GiB retention proposal before implementing defaults. Publish a coherent documented policy covering staging/temporary peak space, retained bytes, pinned snapshots, insufficient space and a single oversized snapshot. Refuse typed when the approved resource bounds cannot be met; do not delete active artifacts to force an acquisition through.

### C-5 — Acceptance proves the selected mechanism

Use fixture writers with known committed generations and cross-table invariants, plus deterministic barrier-forced interleavings. Each published snapshot must represent one valid committed generation or acquisition must refuse; row count and quick_check alone are insufficient. Test checkpoint/backup interaction, file replacement, side-file changes, writer completion, subsequent writer operation, concurrent acquisition, prune, interruption and cleanup.

The acquisition sandbox explicitly permits only approved writes to source sidecars and owned snapshot staging/storage; writes to the source main database or other paths fail acceptance. Inspection runs under complete filesystem-write denial and has no attributed write attempts. Distinguish expected rejected states from successful reads, and preserve raw evidence.

### C-6 — Existing protections and handoff rules remain binding

D-9 matrix protection remains in force until the owner explicitly lifts it. Phase C implementation and testing now proceed **on fixtures/fake processes only**. No real Kriya CLI invocation, protected-store access, live model run, or writes to the main repository while those restrictions remain. The first real acquisition/integration measurement happens only after explicit release and uses the approved SQLite-aware procedure, not an ad-hoc file copy.

Preserve existing non-KUP CLI behavior and the original regression requirements. No source checkpoints, source journal changes, or immutable reads. Never push to GitHub. Existing local commit, low-parallelism, separate KUP commit, owner-coordinated merge and stop-point rules continue.

## Disposition of the review findings

| Finding | Owner disposition |
|---|---|
| R-1 BLOCKER: guarded immutable assumption | Q-1 not approved. Adopt SQLite-consistent acquisition followed by snapshot inspection, subject to C-1/C-3/C-5. |
| R-2 MAJOR: consistency/freshness and race criteria | C-3 defines the guarantee and labels; C-5 binds deterministic cross-generation acceptance. |
| R-3 MINOR: side files versus RUN_ACTIVE | Do not infer run activity from sidecars. Existing workspace assessment remains the activity source. |
| R-4 MAJOR: safe acquisition of a real-store copy | Only the approved SQLite-aware procedure, after D-9 release and under C-6; no uncoordinated main-file copy. |

These are design dispositions with implementation acceptance still outstanding, not claims that the new adapter has passed tests.

## Next instruction to the coding agent

Proceed with Phase C on fixtures under C-1 through C-6. Update the provisional KUP fields and new snapshot-operation grammar to match this gate, then wire the UI's explicit acquisition and pinned inspection flows. Preserve the completed UI/memory work and the already recorded owner-confirmed accessibility/navigation/memory acceptances. Produce the required acquisition/inspection, concurrency and regression evidence, and hand off the separate `KUP:` commit IDs at the existing Phase C stop point. No main-repository merge or real-store test is authorized by this gate while D-9 remains active.

This topic is closed after this gate. No second review. A later change to these authority or consistency decisions starts a new topic folder.
