# M3 real-evidence run (2026-10-10)

Store: the M2 copy `~/kriya-gui-m2/state` (the owner's `~/.kriya/state` and the frozen experiment stores untouched). Isolated HOME `~/kriya-gui-m2/home`
(its own `~/.kriya/authority`), so the SEC-009 approval for the content-on configuration never touches the operator's own authority store.
Config dirs: `config` (kriya.yaml with `evidence.attempt_recorder.content_visibility: "on"`, approved with `kriya authority approve --confirm` in the isolated HOME)
and `config-off` (no file: Kriya's default, off).

Real runs used (trace row -> evidence store, state in the copy):
- f8eeec67 -> 20261006T164431-9d216cb0: sealed, VERIFIED, 44 records. Real `candidate.change` (NumberUtils.java, +7/-0, blobs before/after/diff, diff 452 B), real mirrored gates `compile` and `regression_test`.
- a5f13320 -> 20261006T163143-1f1002d3 (seal removed in the copy: unsealed): the real failed run. Real mirrored gate `context_edit_protocol_unsatisfiable` (failed), real `diagnosis` (evidence_class DERIVED_DETERMINISTIC, SOURCE_DEFECT / judge / medium), real `recovery.decision` (stop_environment), real `fallback.decision`.
- 030f1894 (store removed in the copy): pruned, shown as the semantic state with the recorded path behind "Recorded detail".

Measured (Electron production bundle, real kriya, this machine; ms): acquire snapshot to first Runs 2434-2494; select a verified run to a populated Timeline 2461-2491 (three serial
spawns, ~0.78 s each, Python start-up); Validation first render 16; Changes metadata first render 16 (both read the already-loaded run evidence); authorized diff retrieval 812 (one spawn: verification,
class and size checks, blob read and re-hash); WHY first render 17. Files: `results-content-on.json`, `results-content-off.json`.
