# Orchestrator read-only findings (2026-10-19), snapshot 8e0c90a (scratch, not yet in the repo)

## F1 - OI-74 vs N1-CY (scc_sim.py, MEASURED)
Baseline largest SCC: 67 today; 39 after the N1-CY edges (A developer->context_budget, B/C verifier->context_budget/verifier_evidence, D qualification->agents.agent).
Candidate OI-74 moves, largest SCC after the move: today 68 (every one grows) -> after N1-CY edges removed 39 (flat) for _grounded_output_expectations, _lower_output_protocol_retry,
_diagnosis_mismatch_bypass_reason, _patch_required_files, _as_developer, _raise_unexecuted_test_delta; 40 (+1) for _code_intelligence_for (graph_retrieval). EstablishedProvenance: class-shape change (not simulated; not Type A).
=> the owner's hypothesis (E14 resumes after N1+N1-CY) holds for 6 of 7 symbols; _code_intelligence_for and EstablishedProvenance need their own prerequisite.

## F2 - debt markers in kriya/ and plugins/ (MEASURED, git grep -w at 8e0c90a)
TODO 2 (both the text 'todo list' in CLI examples), HACK 2 (comments saying 'not a hack': validate.py 656/1107), workaround 1 (candidate_transaction.py:180, 'is not a workaround'), deprecated 4 (docs/comments), FIXME 0, XXX 0, 'remove later' 0.
'temporary' 40: 31 are the temp-file+os.replace pattern or the word in prose; ELEVEN files hand-roll `temporary = f"{path}.tmp"; open; os.replace` without fsync/cleanup (model_routing, example_oracle, example_oracle_java, requirement_disposition, requirement_contract, acceptance_oracle, acceptance_approval, authority_request, authority_bundle, contract_compilation, context_certification): this is R1's scope ('no caller-specific durability code (grep proves it)'), so R1's closure must grep `os.replace` outside kriya/repository/atomic_write.py (26 files contain os.replace today; some are legitimate non-write uses: classify each). kriya/client/contract.py:14 'TEMPORARY local-only parameters' = real modularization debt, closes with the client contract (O-slices): list it in the closure report.
=> no hidden unfinished-work comment found; two real items: R1 acceptance grep, client/contract.py TEMPORARY parameters.

## F3 - import allowlist (TRACED + MEASURED)
363 import-direction rows; removal slices: P2 225, O2 77, N1 38, P1 8, O1 5, H2 3, M2 2, K1 2, I3/L1/J1 1 each.
221 of the 225 P2 rows carry the reason 'not enumerated in DEPENDENCY-RULES section 3: public-surface or declared-owner edge, no owning slice yet' = the owner's forbidden 'remove later' justification, parked on P2. By domain pair: KERNEL->PROVIDER 31, KERNEL->RETRIEVAL 30, KERNEL->AUTHORITY 29, KERNEL->EXECUTION 24, KERNEL->STATE 17, AUTHORITY->KERNEL 11, TOOLBOX->AUTHORITY 8, KERNEL->TOOLBOX 7, KERNEL->KNOWLEDGE 6, others <=5.
Direct-access allowlist: 11 rows. FACADES 4 entries (plus PARTIAL_FACADES).
=> a P2 preflight must classify 221 rows into (a) edge is legitimate: declare it in DEPENDENCY-RULES and mark PERMANENT_INTENTIONAL with rationale/owner/test, (b) edge is a violation: owning slice (K1/I*/L*/M* etc.) or a NEW slice. P2 as written is the catch-all the owner does not want.
