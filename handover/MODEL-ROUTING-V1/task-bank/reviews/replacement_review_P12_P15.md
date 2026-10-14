# Independent review of replacement tasks MR-P12 .. MR-P15 (2026-10-14)

Reviewer: fresh general-purpose subagent, read-only on the bank, oracle experiments only on scratch copies. Model-quality evidence was not used. Report condensed; every line below is the reviewer's measured finding unless marked UNVERIFIED.

Verdicts: **MR-P12 BLOCK**, MR-P13 ACCEPT LIMITATION, MR-P14 ACCEPT LIMITATION, MR-P15 ACCEPT.

Common (all four): ws/ has exactly one commit, no remote, no unreachable objects; no reference or hidden-test identifier in ws; goals name no reference-only identifier or path; references are real merged upstream PRs (click#3152 2025-11-20, marshmallow#2928 2026-04-01, jsonschema#972 2022-07-10, boltons#445 2026-08-07); no EXACT/MATERIAL overlap (P12 POSSIBLE RELATED, P13 POSSIBLE RELATED, P14 SAME REPOSITORY ONLY, P15 POSSIBLE RELATED; only the six listed datasets were screened, SWE-bench-Live / SWE-PolyBench UNVERIFIED); evaluator cost trivial (accept 0.6-0.9 s, regress 2.0-5.6 s).

## MR-P12 click#3152 - BLOCK (task removed from the bank; kept as evidence under bank/excluded/MR-P12)
- Goal fidelity NO: the goal sentence "options with is_flag=False and no flag_value must continue to require an argument" is false at base and in the reference when no default is set (`--x` with flag_value unset, default unset -> rc 0, value None on both).
- Discrimination NO: replacing the one changed line with `self._flag_needs_value = True` makes `is_flag=False, default="D"` + bare `--x` silently return None; accept.sh PASS 537/537 and regress.sh PASS 1313/1313. A literal-goal variant (`= flag_value is not UNSET`) also passes.
- No near-miss control possible (fewer than two executable hunks) so nothing guards over-fixing.
- Substance: one-token change in a large file, same shape as the refused python-tabulate#268. Possible repair (corrected sentence + bank-authored negative guard) breaks "upstream tests only" and is outside the owner's no-polish instruction; not pursued.

## MR-P13 marshmallow#2928 - ACCEPT LIMITATION
- All 6 F2P are exactly the 6 URLs in the goal; the scheme-less rejection claim maps to `test_url_idn_invalid` (P2P, passes at base); a `\w` class fails the Bengali URL (combining vowel signs) -> REJECT, so discrimination is real.
- near_miss_0 rejects because the revert leaves `unicode_letters` undefined (NameError, 18 failures): a crash artifact, not a semantic control.
- near_miss_1 PASSES (class A, legitimate alternative): it reverts only the `require_tld=False` dotless-host hunk; the goal never mentions require_tld and every example is dotted. Measured: reference accepts `URL(require_tld=False)("http://münchen")`, near_miss_1 rejects it.
- Disclose: (1) near_miss_1 survives (cert.json as_expected=false but not in limits); dotless-IDN under require_tld=False is unenforced; (2) the 2 "superseded" base tests are `test_invalid_datetime_deserialization[...]` whose IDs embed the wall-clock time - a mislabel, irrelevant to the goal.
- Anchor: NO.

## MR-P14 jsonschema#972 - ACCEPT LIMITATION
- One F2P (`TestBestMatch::test_it_prioritizes_matching_types`, fails at base with 'type' != 'minLength'); `test_boolean_schemas` is a real P2P guard (unguarded `schema["type"]` raises on bool schemas).
- Gap: the goal clause "existing relevance rules keep their precedence" is not enforced - moving the type-match key to the FIRST position of the relevance tuple changes best_match on a crafted case (`{"type":"object","minProperties":3,"properties":{"a":{"type":"integer"}}}`, instance `{"a":"x"}`: reference minProperties, variant type) yet accept.sh PASS 33/33 and regress.sh PASS 6343/6343.
- An exceptions.py-only fix (no validators.py change) also passes, so multi-file is not enforced; draft-specific type semantics are untested (the goal does not claim them).
- Near-misses 0..3 all REJECT (confirmed). 630 excluded base tests are JSON-Schema-Suite / known-bug skips, irrelevant.
- Anchor: YES (moderate confidence); structural evidence: two production files, the error carries no type checker so one must be plumbed in or reconstructed, context descent uses max at top level and min inside context.

## MR-P15 boltons#445 - ACCEPT (cert verdict CERTIFIED stands)
- All four goal cases match the tests; `test_wraps_target_kwonly_arg` is P2P (passes at base), consistent with "already works". Stale comment in that upstream test ("raised TypeError before the fix") is an advisory only.
- Removing the try/except around `inspect.signature` -> REJECT via `test_wraps_uninspectable_target` (non-vacuous on 3.12: `inspect.signature(map)` raises ValueError); forwarding all args as keywords -> REJECT (3 tests); near_miss_0..2 REJECT.
- No exclusions (464/464). Anchor: NO (single file, direct localization).

---

# MR-P16 pydantic-settings#826 (added after MR-P12 was blocked) - independent review (2026-10-14)

Reviewer: second fresh subagent, read-only on the bank; behaviour matrix of 192 config rows on ws/ and ref_tree; 9 hand-built wrong solutions run through accept.sh / regress.sh.

Verdict: **ACCEPT LIMITATION** (no blocker).

- Goal fidelity YES with caveats; leakage NO (public API names only; ws/ one commit, no remote, no populate_by_name test or string in ws); reference independence YES (merged upstream PR, hidden test file byte-identical to the upstream test file).
- Goal-claim table: env source already accepts the field name TRUE; base fails `--project-id x` TRUE; field name accepted with validate_by_name / populate_by_name at top level and in CliSubCommand TRUE+TESTED; aliases keep working TRUE+TESTED; kebab handling TRUE (tested only with cli_kebab_case=True); case handling TRUE on reference but UNTESTED BY ORACLE; "without either option the same invocation must raise SettingsError" was PARTLY FALSE as written (default cli_exit_on_error=True exits with SystemExit(2); SettingsError only with cli_exit_on_error=False as in the hidden test).
  -> Applied before freeze: goal.txt amended with the clause "(with `cli_exit_on_error=False`)". Goal text only; oracle, hidden tests and certification evidence unchanged.
- Wrong solutions: correct re-implementation PASS (sanity); flag read from the sub-model's own config REJECT; always-accept REJECT; validate_by_name only REJECT; populate_by_name only REJECT; name inserted first REJECT; disabled when kebab on REJECT. PASS (oracle gaps, disclosed): field name appended after `.lower()` (case-insensitive mixed-case field names untested); AliasChoices-only restriction (within the goal wording).
- Environment exclusions: 31 = 7 failed + 4 collection errors + 20 skipped, none related to the goal. The 7 failures are `TypeError: _field_name_for_signature() missing 'validate_by_alias'` on the CliApp.run path for a non-BaseSettings top-level model (venv pydantic 2.14.1 vs uv.lock 2.12.5); collection errors are missing pytest_examples / pytest_mock; skips are PyYAML (16), aws_secrets_manager and 3 hard skips.
- Near-misses: all 4 REJECT; nm0/nm1 semantic (fail only the 2 F2P), nm2/nm3 crash artifacts (169/171 fail with ValidationError / AttributeError).
- Reference-side oddity outside the goal: AliasPath-only validation_alias plus by-name raises argparse.ArgumentError in some configs (goal does not claim it).
- Overlap NO KNOWN OVERLAP in all six datasets (same repository as MR-P09 but different files: SAME REPOSITORY ONLY). Cost accept 1.3 s, regress 1.8 s.
- Structural difficulty anchor: YES. Traps confirmed by oracle rejects: the flag must come from the root source config not the sub-model's config; the name must be appended not prepended; both config keys are needed and the default must stay off. About 15 production lines in 2 files, a defaulted parameter on a helper with 6 other callers, a new field on a model class and config plumbing; modest, not a one-token change.
- Disclosure: "Oracle does not cover case-insensitive handling of mixed-case field names (a variant that appends the field name after lowercasing passes). It also does not cover cli_kebab_case=False or AliasPath-only fields. 31 base tests fail or are skipped because of pydantic 2.14.1 drift, missing plugins and PyYAML, none related to the goal. Two of the four near-misses (nm2, nm3) are crash rejects, not semantic ones."

# Difficulty anchors (pre-model, structural evidence only) - recomputed 2026-10-14

MR-P02 is no longer in the bank and its anchor label is NOT transferred. Anchors: MR-J06, MR-J08, MR-J10, MR-P01, MR-P07, MR-P10 (unchanged, earlier pre-model classification) plus MR-P14 (reviewer: YES, moderate confidence; two production files, type checker must be plumbed in, max/min context descent) and MR-P16 (reviewer: YES; three confirmed traps). MR-P13 NO, MR-P15 NO.
