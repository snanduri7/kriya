# Evidence for slice A5

Canonical runtime baseline: commit `3eded93c0cfb50f8855d04510e3f9aa2cd5208f7`. Nothing here changes runtime code,
tests, goldens or the registry. No model was called and no network request was made (the index is built with the
deterministic `tests/_fake_embedding.py::StaticEmbedder`). No MCP server and no container was started.

| File | What it is | Result |
|---|---|---|
| `a5_probe.py` | Model-free probe P1 to P8, calling the real functions over scratch projects | `a5_probe_output.txt` |
| `a5_probe_output.txt` | Output on this baseline | P1: two unconfigured folders and a Project Setup folder resolve the same `paths.memory` and `paths.skills`, both in the installation directory; the Setup file has no `paths` section and needs approval for six fields. P2 to P4: the index stores have no project column and the manifest no root; after project A is indexed, project B's readers return A's paths, the "already indexed" test is true, retrieval matches A's file and A's chunk text is in `retrieved_chunks`; `current_view()` readers and `developer_goal_facts` see only B; indexing B deletes A's index. P5: the write gate allows `kriya.yaml`, `kriya.yml`, workspace skill files, a CI workflow, `.gitignore`, `.gitattributes`; denies control paths, `.env` and a path outside the root. P6: a repository file may set the run budget to 999999 or `null` with no violation; the production profile refuses 7200 and `null` and accepts 2400; `risk_threshold_lines` and `autonomy.mode` are violations. P7: two projects have different identities and approval stores, the same state directory, trace database and learned-knowledge store. P8: a scaffold written for project A closes project B's knowledge gate; the engine's shared-install refusal exists and the scaffold writer does not consult it |
| `run_a5.sh` | Runs `a5_probe.py`, then `check_citations_a5.py` | probe exit 0, citations exit 0 |
| `check_citations_a5.py` | Checks that every `path::symbol` cited by the A5 rows exists at the checked-out tree, and runs the negative-control searches | `check_citations_a5_output.txt`: 80 files, 330 symbols, 34 controls, 0 problems |
| `coverage_overrides_a5.json` | The A5 registry coverage entries (26 A5 rows and the five rows handed over by A3 and A4) | merged into `../a1/coverage_overrides.json`: 106 entries; `REGISTRY-COVERAGE.md` regenerated: 101 of 141 reconciled, 5 `NEEDS_CHARACTERIZATION`, 35 pending (slice A6) |

How it was run (from the closure worktree root; interpreter of the `Kriya-product-72h` checkout; isolated state):

```
sh docs/architecture/evidence/a5/run_a5.sh <venv python> $PWD <tmp>
python3 docs/architecture/evidence/a2/merge_overrides.py docs/architecture/evidence/a1/coverage_overrides.json docs/architecture/evidence/a5/coverage_overrides_a5.json
python3 docs/architecture/evidence/a1/gen_coverage.py . docs/architecture/evidence/a1/coverage_overrides.json
```

Isolation of the probe: the runner sets `KRIYA_STATE_DIR` and `KRIYA_LOG_DIR`; the probe itself, before importing
Kriya, sets a scratch `HOME`, `KRIYA_AUTHORITY_HOME` and `KRIYA_MCP_APPROVAL_HOME` and disables user and system Git
configuration for its `git init` calls. The packaged-default memory and skills locations are only **resolved** (P1);
every store the probe writes is in a scratch "shared" directory standing in for them, so the installation tree is
not written by the probe. The line `scratch: <tmp>/kriya-a5-probe-...` differs per run; nothing else does.

A correction made inside this slice, before the checkpoint: the first run of `check_citations_a5.py` failed one
negative control. The draft said the write gate is constructed at four sites; the search found two more
(`policy/authority.py::AuthorityPort.file_writer`, a facade factory with no caller, and the cockpit's
`policy/security_demonstration.py`, which only calls `authorize` over a disposable folder). Neither writes, so the
statement about writing sites stands; the rows and the control were restated to name all six. The first failing
output is not preserved; this paragraph is its record.

Limits of this evidence, stated once:

- No run (direct, enforce or desktop) was driven. P2 calls the readers a run calls, with the arguments a run passes;
  the order of steps inside a run is `TRACED`. The effect of a foreign index on plan quality or on a Developer
  request was not measured.
- P5 measures the write gate's verdict only. That a candidate file named `kriya.yaml` then passes approval and the
  commit into the owner workspace is `INFERRED` from the generic apply path.
- The MCP route (`GAP-A5-03`) is `TRACED` in `mcp/mcp.py` and `mcp/containment_adapter.py`; no server was started
  and the real-OCI tests were not run.
- P6 calls `resolve_config_state`, so it measures classification and the production seal, not `load_config`'s
  approval step.
- The registry's own measurement of the desktop (ten runs with no `retrieval.*` event; an empty database in the
  installation tree) was not repeated; which reader created that file is `INFERRED`.
- Not read: `tools/containment_oci.py`, `repository/git.py`, `repository/snapshot.py`, the scoped-snapshot builder's
  body, `platform/private_store.py`, `control/control_store.py`, `production_doctor.py`, `mcp/capability.py`,
  `mcp/lifecycle.py`, `kriya/kup` beyond its outline, the UI session code.
- The three contained-environment rows (venv bootstrap, a Docker refusal read as a False verdict) and the Docker
  Desktop cleanup family need a real loaded daemon and were not characterized; two of them stay
  `NEEDS_CHARACTERIZATION` as declared Phase-D items.
- No committed test was re-run in this slice (the tests A5 cites are named with "not re-run" where they are cited).
