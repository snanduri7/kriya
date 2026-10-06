# A5 live post-P3-D: SUCCESS, applied, GENUINE

Run `20261006T175548-2b834bc0` (Kriya `bcb0c1d`, goal by `-f` absolute path), M1 `VERIFIED` (198 records, sealed),
wall 316.8 s, generate exit 0. Acceptance d6bdc942 (3 cases) + B3 approval 5f2318b6 bound at start, unchanged after.

MEASURED (traces run 5695a587 = s1, bfd5820a = s2, .enforce = terminal):
- Plan: s1 PetTypeFormatter.java, s2 verification; 0 Planner repair rounds.
- Edit authority, every attempt: full_file=false, operations [anchored_edit], ONE member_exact span 55-63
  (PetTypeFormatter.parse), insertion null (P3-D not offered). `authority.expansion` (attempt 2): member authority
  GRANTED for PetTypeFormatter.parse only (pristine, in write scope).
- a1 (qwen3-coder): anchored edit refused INDENTATION_STYLE_MISMATCH (file uses 4-space indent; the SEARCH matched only
  ignoring indentation). a2, a3: ANCHOR_CONTEXT_NOT_ESCALATED before any request (same capability digest 7dc1f207 - the
  member was already shown exactly, so nothing to escalate); REPEATED_ACTION x2 -> strategy transition. a4 fallback
  qwen3.6: anchored edit accepted (`context.anchor_authorized_by_sent_request` = P3-A path), gates passed.
- Applied diff: 2 insertions, 1 deletion inside parse(); trims the text once, compares each PetType name with
  equalsIgnoreCase, ParseException after the loop unchanged; no import change; no other file.
- Terminal: acceptance 3/3 passed -> REQ-1 `human_accepted` (ACCEPTANCE_HUMAN_ACCEPTED, GENERAL); C0
  PetTypeFormatterTests ORACLE_PASSED, regression preserved; full-regression delta passed; REQ-2 closed_by_evidence
  (actual == authorized). Model verdict REQ-1 satisfied (MODEL_CLAIMED, non-authoritative). SUCCESS / COMMITTED (written
  to the workspace, no git commit).
- Model calls 10: planner 1 (qwen3.6), localization 2, developer 2 (qwen3-coder 1, qwen3.6 1), run_verifier 1,
  spec_compliance 2, reviewer 1, unattributed 1 (qwen3.6 knowledge-fact extraction after the fallback fix).
- Independent check (`independent_check_IndepCheckTest.java`, own JUnit test in a scratch copy, mvn offline with the
  run's dependency cache; 4 pet types): "  bird ", "\tBIRD\n" -> Bird; " dOg " -> Dog; " hAmStEr  " -> Hamster;
  "Fish", "  fish ", "Bir d" -> ParseException: PASS.
Classification: GENUINE_SUCCESS. No stop condition hit.

Observations (not fixed; no fixes during live runs):
- O-1 attempts 2 and 3 were both pre-inference refusals of the same unchanged context before the fallback switch;
  same shape as the P3-B record (two refused attempts before a model switch) but a different trigger (indentation, not a
  stitched anchor). 0 model calls lost; one attempt slot spent twice.
- O-2 the knowledge-fact extraction call is reported under role `unattributed` in role metrics.
- O-3 `text.trim()` would throw NullPointerException for a null text where the base threw ParseException; the class is
  `@NullMarked` (text is non-null by contract), so INFERRED irrelevant in practice.
