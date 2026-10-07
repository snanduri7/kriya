# Graphify forensic comparison: Claude reference fix vs canonical Kriya candidate

Read-only. No model call, no Kriya change, no Graphify rerun. Accepted by the owner as **PRIMARY = BOTH, confidence MEDIUM**:
the initial wrong candidate is primarily a context gap; the failure to repair after deterministic error evidence is a
model/recovery capability weakness.

## Artifacts in this directory

| File | What it is |
|---|---|
| `ast_probe.py`, `ast_probe_output.txt` | AST shapes of `Get<int>("port")`, `this.Get<int>("port")`, `Make<int>()`, `GetRaw("host")` from the installed tree-sitter-c-sharp 0.23.5 |
| `run_extract.py`, `extract_traces.txt` | The issue's three files through base, reference fix A and candidate `8a0f23e9`, with `GRAPHIFY_DEBUG=1` tracebacks |
| `attempt2_developer_request_wire_body.json` | Exact sealed attempt-2 Developer wire body (M1 blob sha256 `c734c21c…`) |
| `attempt2_developer_request_messages.json` | Its messages (M1 blob `07919722…`) |
| `attempt2_developer_response.txt` | Exact attempt-2 response (M1 blob `7311d105…`) |
| `reference_fix_A.diff` | Claude's GR-R0 diagnostic reference fix (scratch-only, never given to Kriya) |
| `candidate_8a0f23e9.diff` | The canonical run's staged candidate (attempts 2 and 8) |

## Findings (MEASURED unless marked)

- `generic_name` has **no fields**: children `identifier` and `type_argument_list`, both unfielded.
  `child_by_field_name("name")` and `("identifier")` return `None`.
- Reference fix A: iterates `generic_name.children` for `identifier` on both paths. External 5/5, evaluator regression
  76/76, `test_csharp_call_site_generic_args.py` 6/6 (2 failures in a wider C# selection fail at base too).
- Candidate `8a0f23e9`: `_read_text(mname.child_by_field_name("name"), source)` at engine.py:5368 dereferences `None`
  (`AttributeError ... start_byte`); `graphify/extract.py::_safe_extract` skips the whole file, so `Reader.cs` (A-D)
  is dropped. Its two other hunks never run (TRACED: an `identifier` function's parent is the invocation; the
  invocation has only `function`/`arguments` fields), so `Make<int>` stays unfixed. External 0/5.
- Attempt-2 request: the broken branch was shown (windows 5356-5370, 5385-5413). Not shown: the children-iterating
  call-site code (~5427-5440; window ends at 5413, not selected), the `_csharp_collect_type_refs` `generic_name` block
  (lines 215-220) and `_read_csharp_type_name`'s body (engine.py at signatures tier: 330,491 chars requested, 13,666
  kept), and `graph_context` (15,425 chars dropped; not recorded). No grammar field information. Developer
  investigation disabled (default). The same absence holds in every Developer request of the run.
- Evidence received but not acted on: the model's own plan ("identifier part (before the `<`)") needed no field; the
  attempts 3 and 9 requests carried `skipped ... AttributeError: 'NoneType' object has no attribute 'start_byte'`
  beside the candidate's own `child_by_field_name("name")` line; 6 of 7 repair attempts failed on protocol basics
  (2 fabricated anchors, 2 no-ops, 2 malformed), 1 on a diagnosis mismatch.
- Claude's access for fix A (this session's transcript): whole repository and tools; read engine.py 5355-5420 before
  editing; no AST probe before editing; acceptance 5/5 afterwards; the quarantined maintainer fix was never opened.
  Prior tree-sitter knowledge: UNKNOWN.
