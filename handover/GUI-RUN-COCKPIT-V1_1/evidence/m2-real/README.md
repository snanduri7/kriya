# M2 real-evidence integration: what was used (2026-10-10)

Store: a COPY at `~/kriya-gui-m2/state` (the owner's `~/.kriya/state` and the frozen experiment stores were only read to make the copy;
nothing was written to them). Contents: `traces.db` (SQLite online backup, 354 trace rows, 10,186,752 bytes) and `attempt-evidence/` (5 stores).
Config directory `~/kriya-gui-m2/config` (empty: packaged defaults); Electron user data `~/kriya-gui-m2/userdata`; executable `.venv/bin/kriya`.
Launch: `KRIYA_UI_ALLOW_REAL_KRIYA=1 KRIYA_STATE_DIR=~/kriya-gui-m2/state electron . --user-data-dir=~/kriya-gui-m2/userdata` on a `build:cockpit` (production, no fixtures) bundle.

| trace row | evidence store (copy) | state in the copy |
|---|---|---|
| f8eeec67 (success) | 20261006T164431-9d216cb0, 44 records, 24 blobs, 188 KB | sealed, VERIFIED (the verified case) |
| a5f13320 (failure) | 20261006T163143-1f1002d3, 36 records | seal.json REMOVED in the copy: unsealed |
| 030f1894 (failure) | 20261006T163156-82553464 | store directory REMOVED in the copy: pruned (the row still records its path) |
| older rows (348) | none | no evidence.attempt_store event: pre-recorder |

Measured (this machine): every `kriya` process costs about 0.77-0.79 s and `kriya --version` alone costs 0.78 s, so the latency is Python
start-up, not verification: capabilities 771 ms, history.list(200) 788 ms (73,807 B), history.detail 776 ms (51,760 B), evidence.manifest
775 ms, evidence.verify (44 records, 24 blobs) 787 ms, evidence.records (44 records, 42,424 B) 779 ms (min of 3). In Electron: Acquire
snapshot to first Runs render 2.47 s (acquire, verify, list = three spawns); selecting a verified run to a populated Timeline 2.5 s (run detail
runs in parallel with manifest, verify, records = three serial spawns). No caching was added.
