# HX1 step 0 - kernel golden harness characterization (OI-41), BEFORE any harness change

Known-good commit: **ac427ba344cd0bbcb69eb46892a3597694aac00a** (branch arch/full-modularization-v1, E4 accepted). Measured
2026-10-17 by the HX1 implementer. The goldens are `tests/golden/kernel/*.json` (39 scenarios) checked by
`tests/state_machine/test_sm_kernel_golden.py` (39 parametrized scenario tests + 1 meta test = 40 collected).
Status labels follow handover/ENGINEERING_RULES.md §2. Measurement tooling (scratch, not committed): a git-archive
sandbox runner, a `sys.addaudithook` file-access probe (open / sqlite3.connect / os.mkdir / os.listdir / os.scandir,
attributed per test through PYTEST_CURRENT_TEST, in the controller and every xdist worker), a content-dump differ over
`KRIYA_KERNEL_GOLDEN_CONTENT` and a leaf differ over `KRIYA_WRITE_KERNEL_GOLDEN` forms.

## a. Canonical checkout passes - MEASURED
`.venv/bin/pytest -q -p no:cacheprovider tests/state_machine/test_sm_kernel_golden.py -n 3` in the canonical checkout:
**40 passed** (21.4 s).

## b. The same committed source at another absolute path fails - MEASURED
`git archive ac427ba` extracted to a scratch sandbox (no untracked files, so no `./memory`), run from the sandbox root with
`PYTHONPATH=<sandbox>`, own TMPDIR / KRIYA_STATE_DIR / basetemp, `-n 3`: **34 failed, 6 passed**. Import identity
MEASURED in the controller and in each xdist worker (gw0-gw2) by a probe plugin: `kriya.__file__` and
`build_info.PACKAGE_DIR` lie under the sandbox root (the editable finder in `.venv` is a `sys.meta_path` hook consulted after
the path finder, so the sandbox copy on `sys.path` wins).
The 5 that pass are exactly the 5 scenarios with no `run.opened` record (`direct_workspace_refused`,
`legacy_workspace_refused`, the 3 `milestone_*`).

## c. The cause is exactly `run.opened -> kriya_build.install_path` - CONFIRMED
- Leaf diff of the sandbox's written forms against the committed goldens: every one of the 34 failing files differs ONLY at
  `evidence[*].records[0].payload` (the `run.opened` payload digest; `legacy_resume_after_failure` has two stores, so two
  leaves); the other 5 files are BYTE-IDENTICAL.
- Leaf diff of the decoded content dumps (canonical vs sandbox): the only differing leaf in all 39 scenarios is
  `run.opened <payload> .kriya_build.install_path` (canonical `<checkout>/kriya` vs `<sandbox>/kriya`). No other semantic
  field differs.
- Producer TRACED: `kriya/core/attempt_evidence/scope.py::_run_manifest` -> `kriya/foundation/build_info.py::version_report`
  (`"install_path": PACKAGE_DIR`, the imported package directory).
- Correction to the slice wording (MEASURED): the field is a plain nested key of the run.opened payload DICT
  (`payload["kriya_build"]["install_path"]`); there is no embedded JSON text level in the evidence record. The content dump
  shows it as JSON text only because the dump serializes each payload with `json.dumps(sort_keys=True)`, and the golden's
  payload digest is `digest()` over the normalized dict (`sort_keys=True, separators=(",", ":")`). So the canonicalization
  is a typed replacement of that one nested key before normalization; no re-serialization is involved and the digest
  function is unchanged.

## d. `provider_contract.deadline.elapsed_request_ms` is load-sensitive - CONFIRMED (deterministic reproducer)
- Producer TRACED: `kriya/core/llm.py::LLMClient._finish` writes `deadline["elapsed_request_ms"] = int(result.elapsed_seconds * 1000)`
  (wall clock, `time.time() - started`). In the scripted runs the call takes < 1 ms, so the committed goldens carry `0`.
- Reproducer: a test-only plugin wraps `LLMClient._finish` so it sees `started - 0.005` (exactly a scripted call taking
  5 ms longer). Result: the 3 scenarios whose model calls go through `LLMClient._request_once`
  (`enforce_approval_refused`, `enforce_success_with_verification_unit`, `enforce_verification_only_failed`) change, and
  ONLY at that field: content leaves `model.result <payload> .protocol.provider_contract.deadline.elapsed_request_ms` and
  `mirror.event event .details.model_use.protocol.provider_contract.deadline.elapsed_request_ms`; form leaves
  `evidence[0].records[21|30|37|81].payload`, `evidence[0].records[24|84].content.event` and
  `run_events[0|2].events[12].details` (the traces.db copy of the same model_use). The other 36 forms are BYTE-IDENTICAL.
  This is the E3 flake mechanism (`enforce_success_with_verification_unit` under `-n 4` load).

## e. The untracked `./memory` dependency - 29 A, 10 B, 0 C - MEASURED
Conditions compared (content dumps, decoded leaf by leaf, ignoring only `kriya_build.install_path`):
present (canonical checkout, the developer's `./memory` - read by the code under test only; its files were never copied,
opened or inspected by this measurement), absent (sandbox, no `./memory`), empty (sandbox, empty `./memory`):
**no difference in any scenario** -> no scenario consumes `./memory` content (no class C; no fixture needed).
Sensitivity control: sandbox with `./memory/vector_index.db` present as an EMPTY file (Graph RAG switch): exactly the 10 B
scenarios change (hundreds of leaves), the 29 A scenarios do not.
Further MEASURED facts:
- When `./memory` is absent the B scenarios CREATE `./memory/dependency_graph.db` and `./memory/knowledge_cache.db`
  (schema only, 0 rows) in the working directory; in the canonical checkout the developer's `memory/` directory mtime
  changed to the run time (the harness writes into personal untracked state).
- Running ALL 39 scenarios from an isolated `<tmp_path>/cwd` holding an empty `./memory` and no `./skills`: **40 passed**
  against the unchanged goldens, content dumps identical to canonical, zero access to the checkout's `memory/`.
- The `skills/` reads the probe shows (14 scenarios) are NOT cwd-relative: they persist from the tmp cwd, i.e. they are the
  install skills next to the package (tracked files; present in a git-archive copy).

| # | scenario | ./memory class | ./memory access (audit probe, canonical) | vector_index.db presence changes content | run.opened install_path records | elapsed_request_ms occurrences |
|---|---|---|---|---|---|---|
| 1 | direct_success | B | memory, memory/dependency_graph.db, memory/knowledge_cache.db | yes | 1 | 0 |
| 2 | direct_workspace_refused | A | - | no | 0 | 0 |
| 3 | enforce_approval_refused | A | - | no | 1 | 3 |
| 4 | enforce_plan_invalid | A | - | no | 1 | 0 |
| 5 | enforce_success_with_verification_unit | A | - | no | 1 | 3 |
| 6 | enforce_verification_only_failed | A | - | no | 1 | 5 |
| 7 | legacy_approval_diff_size_trigger | A | - | no | 1 | 0 |
| 8 | legacy_approval_refused | B | memory, memory/dependency_graph.db, memory/knowledge_cache.db | yes | 1 | 0 |
| 9 | legacy_approval_required_no_callback | A | - | no | 1 | 0 |
| 10 | legacy_architect_file_list_rejected | A | - | no | 1 | 0 |
| 11 | legacy_autonomous_success | A | - | no | 1 | 0 |
| 12 | legacy_budget_stop | B | memory, memory/dependency_graph.db, memory/knowledge_cache.db | yes | 1 | 0 |
| 13 | legacy_commit_revision_conflict | A | - | no | 1 | 0 |
| 14 | legacy_dependency_skill_accrued | A | - | no | 1 | 0 |
| 15 | legacy_fallback_success | B | memory, memory/dependency_graph.db, memory/knowledge_cache.db | yes | 1 | 0 |
| 16 | legacy_final_review_deadline_refused | A | - | no | 1 | 0 |
| 17 | legacy_final_review_refused | B | memory, memory/dependency_graph.db, memory/knowledge_cache.db | yes | 1 | 0 |
| 18 | legacy_gates_exhausted | B | memory, memory/dependency_graph.db, memory/knowledge_cache.db | yes | 1 | 0 |
| 19 | legacy_knowledge_gap | A | - | no | 1 | 0 |
| 20 | legacy_knowledge_gap_confirmed | A | - | no | 1 | 0 |
| 21 | legacy_lessons_staged | A | - | no | 1 | 0 |
| 22 | legacy_no_progress | B | memory, memory/dependency_graph.db, memory/knowledge_cache.db | yes | 1 | 0 |
| 23 | legacy_planner_output_incomplete | A | - | no | 1 | 0 |
| 24 | legacy_planner_schema_invalid | A | - | no | 1 | 0 |
| 25 | legacy_planner_unauthorized_path | A | - | no | 1 | 0 |
| 26 | legacy_rejected_candidate_review | A | - | no | 1 | 0 |
| 27 | legacy_repeated_failure_exhausted | B | memory, memory/dependency_graph.db, memory/knowledge_cache.db | yes | 1 | 0 |
| 28 | legacy_resume_after_failure | B | memory, memory/dependency_graph.db, memory/knowledge_cache.db | yes | 2 | 0 |
| 29 | legacy_stage2a_dependency_rejected | A | - | no | 1 | 0 |
| 30 | legacy_stage_context_success | A | - | no | 1 | 0 |
| 31 | legacy_static_analysis_blocked | A | - | no | 1 | 0 |
| 32 | legacy_static_analysis_passed | A | - | no | 1 | 0 |
| 33 | legacy_static_analysis_unavailable | A | - | no | 1 | 0 |
| 34 | legacy_static_analysis_unknown | A | - | no | 1 | 0 |
| 35 | legacy_success | B | memory, memory/dependency_graph.db, memory/knowledge_cache.db | yes | 1 | 0 |
| 36 | legacy_workspace_refused | A | - | no | 0 | 0 |
| 37 | milestone_chain_completed | A | - | no | 0 | 0 |
| 38 | milestone_rerun_reuses_completed | A | - | no | 0 | 0 |
| 39 | milestone_resume_after_recovered_crash | A | - | no | 0 | 0 |

B = resolves the relative default `paths.memory` (`./memory`, AppConfig default kept by `_scripted_run.engine`) and opens
its sqlite files, but no content reaches any observable. A = never touches the cwd's `./memory` (the E3/E4 scenarios
point `paths.memory` at tmp, the enforce harness likewise, the milestone and refusal scenarios make no such call).

## Golden sha256 at ac427ba (before HX1)
| file | sha256 |
|---|---|
| direct_success.json | `0165530e8d5befdbfb335fc20193cf5f505c8ee9c855bdfa1618dc6b8ad12d4e` |
| direct_workspace_refused.json | `b7b4b1c15560ee68959b1044886b36583d4c5cea283c8f6465b9489ed560985a` |
| enforce_approval_refused.json | `2f20f4755301fb65c3567c2142ef640a12be2362247eef8f48c16e051e4b6f35` |
| enforce_plan_invalid.json | `b34bf30ec4c1a8d734681973a84250e6dd30e542a0e0a747b5b6232bb5916e13` |
| enforce_success_with_verification_unit.json | `67570dd9a55f7ecf9b8f6185a0c0aea23af210e68e9e57a166c8b386223f88cd` |
| enforce_verification_only_failed.json | `f6afb1be0a9e135defb115f4ee290194fe363a23ca3c2b8541ed558054bae926` |
| legacy_approval_diff_size_trigger.json | `d17cb9b69e8c58f69506188f29967ca9801ecf5c5291ddc1d2ac4ab94f947b56` |
| legacy_approval_refused.json | `20eb2b6723be0222f7afbb3a3a6a085ddc6c7981933705aecdbbd6fabf0bcef9` |
| legacy_approval_required_no_callback.json | `9eff6603121bb07099ab8e250ec6f748bb3b1acf0890a04543e6773057a903df` |
| legacy_architect_file_list_rejected.json | `a0ace8ef823d389743d4abc0656410fc1e13fccb2c83d5ff0f6283d83924bbda` |
| legacy_autonomous_success.json | `d475446341210d122fb6a6cbb332639bbd4d26fa53c1a774802cf26d09c731d3` |
| legacy_budget_stop.json | `568ac80690c4e0d65d84372d83b26b07e369fcca87a8e42b4978431c429c98ba` |
| legacy_commit_revision_conflict.json | `e2b9f6576c48206f4fad4087cb5a3b7fc19199253a44f7d4e54737cd26f659c5` |
| legacy_dependency_skill_accrued.json | `a4996621e2733664369ff48f3a42d2c951f179e20f080eb5d003180b2d9afaee` |
| legacy_fallback_success.json | `9001816aea040738549c7d33660217e7ced7a2e3c4000cb7eee14602858961a6` |
| legacy_final_review_deadline_refused.json | `f92881fd312e86d894f1ca46d4ca014394589fc4de7c42ae081f3f325402b12b` |
| legacy_final_review_refused.json | `5df635e50062df3c316330058dc4d3177590bf3e2599fdc2808789386bce9037` |
| legacy_gates_exhausted.json | `2e6693c5cec8944315c474611ed8ac898c3b033449fa424fc174a081ea1285fd` |
| legacy_knowledge_gap_confirmed.json | `7da6615cb8c4e185ec329d186f0ec7a96b563488724253fe37f88d450082d2dc` |
| legacy_knowledge_gap.json | `80b8c55dc5fcf7ba1619182191e98d331a3cdf31be0aaf207ad0eec9bdb9aa95` |
| legacy_lessons_staged.json | `6c8aa0ad5694a428cb625db74b1591890319b25b0e7758203fc1d456edf23ddd` |
| legacy_no_progress.json | `076ed317831f2d7c9a7ec2b9bea124b25b95c65ab911e9ea077319a46b789cee` |
| legacy_planner_output_incomplete.json | `b7fffa872d3d1f816d6bc70cc0edf09109bef04a32b22fe7c8664abfa05b8bfb` |
| legacy_planner_schema_invalid.json | `cd5f0fb57030e4ce9352ff9906c346190585e5510f3207dbd4c9debaa5de3053` |
| legacy_planner_unauthorized_path.json | `24caa0ec275fefc0e6b9ea26b1946342c95569c21dd8ce9a022ddd0474224707` |
| legacy_rejected_candidate_review.json | `e3a9dbecd2852a982af8a10af1b610eaea68a9ab9693f1c3a0c1db49cbccf507` |
| legacy_repeated_failure_exhausted.json | `087b0d88b3f26fc599f924994447dd37758426294111e1c0725fad6ded479e36` |
| legacy_resume_after_failure.json | `2bb5a6e73abd27bd826e9758eae26544194a166f995004961af9b0accb677916` |
| legacy_stage_context_success.json | `ab8b3ed1472b066f29fba942910fd2eaaf0a3d9313ef21f2bdd4a7843ab294c5` |
| legacy_stage2a_dependency_rejected.json | `8dd8a84546650e4d1e454f9b5d17b494476bbdc900fe0329f755189072df9fd3` |
| legacy_static_analysis_blocked.json | `3637ae7dec63078a3a8ded41e822314f4b5369247f2ce2f448af233d019a7c88` |
| legacy_static_analysis_passed.json | `f62e8b1053b5a7df460fe0f177b2ced8ca03eca0f96ef4420bfa37da4567507c` |
| legacy_static_analysis_unavailable.json | `4a5d7a2a5c66ee10596f257efed6d83044609cc9e3dccb9359203055b1fe8164` |
| legacy_static_analysis_unknown.json | `adb005c1f8baf5e9747c8fe49dc3fc0add97eb8e0dfc318f6ba299bf577d0b6a` |
| legacy_success.json | `e5d77ab3fcb58a6fb9edcbcf0e30ce38c304cd53b1eb55fcfe2de9d94e1fc889` |
| legacy_workspace_refused.json | `297553227e0965a13e6f90844b008061e094cbd1637586f3975647556ee47399` |
| milestone_chain_completed.json | `61ad77be991df4a547c2bca1e3ffd589e6fa245646a5945bd4f51e32d0d74753` |
| milestone_rerun_reuses_completed.json | `cfb5bce2080e0667fbf6bf7569eb15820b945027fc24689a28135271cef8b781` |
| milestone_resume_after_recovered_crash.json | `1348b58a2c094f2da26438c281179d36a27dba7eb95dcc6b2c8bfe6ab3eee22a` |

## Out of HX1 scope, observed (ledgered as an OPEN-ITEMS row)
`run.opened -> kriya_build` also embeds `python` (3.14.6) and `version` (from the untracked `kriya.egg-info` metadata of the
editable install) and probes the untracked `kriya/_build_info.json` (absent here: `build_provenance: unavailable`). A
different interpreter patch release, a local `python setup.py build` that writes `_build_info.json`, or a different
installed version would change all 34 `run.opened` digests. These are environment identity, not path portability, and HX1
does not normalize them.
