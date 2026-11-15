# 07 - Required contracts (Phase C, TO-BE)

Canonical runtime baseline: commit `3eded93c0cfb50f8855d04510e3f9aa2cd5208f7`. **Everything in this document is
`PROPOSED`.** Nothing is implemented, nothing is authorized, the implementation freeze holds. AS-IS lives in the six
maps and in `06-GAP-AUTHORITY-AUDIT.md`; this document never restates it, it cites it.

Reading order per root issue: `AS-IS -> GAP -> REQUIRED CONTRACT -> IMPLEMENTATION DELTA -> ACCEPTANCE PROOF`. The
first two are the audit's; this document supplies the last three.

Design rules applied to every contract:

- Start from the truth's owner and work backward to its inputs. No new abstraction without a measured or traced need.
- Prefer the smallest correction at the first wrong owner over a new subsystem. Where an owner exists, extend it.
- Fail closed: every UNKNOWN the contract introduces has a typed outcome and a reason code in a closed table.
- Owner rulings in force are constraints, not options: no blocker registry or typed blocker key as authority (WS4,
  2026-11-14); the old `Finding / FindingState` design is not resurrected; the native adapter is the production
  runtime; SEC-009, TOOL-002 and TOOL-003 are not changed.
- Module and symbol names of things that exist are cited from the maps. Names of things that do not exist yet are
  proposals and are written in *italics*.

## 1. Contracts per root issue

### C-01 Semantic contract authority (R-01)

| Field | Required contract |
|---|---|
| Truth | What the user's request requires of the product's behaviour, per requirement, before any model writes code or a test |
| Required single owner | `workflow/contract_compilation.py`: the sealed verification contract gains one field per requirement, the *semantic state* |
| Authoritative inputs | The user's goal words; operator artifacts (requirements contract, acceptance file, approved examples); examples stated in the goal itself (`goal_example_forms.py`). Nothing a model produced |
| Persisted identity and state | In the sealed contract (schema version raised), per requirement: *SPECIFIED* (at least one deterministic clause from the inputs above, with clause ids), *GENERAL_UNSPECIFIED* (a behaviour statement with no clause), *AMBIGUOUS* (two readings the compiler can name: today only mutation scope; extended to the closed set of patterns the compiler can prove, not to model judgement). The contract digest is already a resume identity and in `ControlState` |
| Allowed mutation | None after sealing. New clauses arrive only through an authority request answered by a person or an operator artifact, which produces a new sealed contract (the contract-evolution rule of `CORR016_AUTHORIZED_CONTRACT_EVOLUTION_DESIGN.md`) |
| Forbidden bypass | A generated verifier, a Planner criterion or a structural closer (`test_addition`) closing a *GENERAL_UNSPECIFIED* requirement. A pattern miss deciding that the request does not require execution (`GAP-A4-05`): absence of a match is *unknown*, and unknown keeps the Planner's runtime verification |
| UNKNOWN behaviour | *GENERAL_UNSPECIFIED*: the requirement can be closed only by a person's verifier semantic decision bound to that requirement id (the WS3 review, which exists) or stays `UNVERIFIED`. *AMBIGUOUS*: attended run asks (one typed question per ambiguity); unattended run under the production profile refuses before planning; elsewhere recorded and carried to the result. An executed test identity that maps to no file is *unmapped*: the unit is held for review, never read as "not run-authored" (`GAP-A2-02`) |
| Why minimal and generic | Three states on an owner that already seals content before the first model call. No specification language, no model-extracted specification |
| Alternatives rejected | A model-written specification approved by a person (model text becomes the contract's first draft: the person reviews prose, the invariant "a model is never the first creator of semantics" is lost). A formal behaviour language (no measured need; one scenario family so far) |
| Planner integration decisions (`GAP-A4-08`) | Unchanged by owner ruling; the validator additionally refuses a decision that contradicts a *SPECIFIED* clause |
| Acceptance proof | A2 probes P8 and P9 as tests: a general sentence compiles to *GENERAL_UNSPECIFIED* and `test_addition` cannot close it; a goal example compiles to a clause that the example oracle closes; a `src/`-layout run-authored test is held; an ambiguity fixture produces the typed state and, unattended under production, a refusal before the Planner |
| Negative controls | Removing the semantic state makes the closure test fail; restoring "unmapped = not run-authored" makes the held-unit test fail; a model-supplied clause is refused by the sealing function |
| Stop conditions | If more than the closed pattern set is needed to classify ambiguity (that is, a model would have to judge), stop: ambiguity stays representable only for the provable cases and the rest is `GENERAL_UNSPECIFIED` |

### C-02 Result truth and executor convergence (R-02)

| Field | Required contract |
|---|---|
| Truth | The one meaning of plan validity, requirement truth, approval, commit, final-review authority, requirement closure and terminal result, whichever executor ran |
| Required single owner | `control/run_coordinator.py` remains the one result-truth owner (it already reconciles a result with its RunRecord). It consumes one *result contract* evaluated from the RunRecord and the requirement truth |
| Authoritative inputs | The RunRecord's own proofs (commit settled or none, every work unit VERIFIED) **plus** the run's final requirement truth (`requirements.py::requirement_truth`, closed vocabulary), recorded on the RunRecord as an annotation before the terminal transition |
| Terminal vocabulary | *SUCCESS* only when the record's proofs hold and requirement truth is `ALL_VERIFIED` or `RESOLVED_WITH_OPERATOR_ACCEPTANCE`. *APPLIED_REQUIREMENTS_OPEN* (new): the change is committed and at least one requirement is open or contradicted; reported as needs review, exit code non-zero, in every profile. *FAILED*, *NEEDS_REVIEW* (uncertain commit) as today. The requirement policies (`record` / `block`) decide only whether a run stops early; they never change the word |
| The seven truths, one definition each | (1) Plan validity: one validation function called by both executors; an executor may add operational checks, never a second verdict. (2) Requirement truth: one closer set, available on both executors (the two closers that cannot fire under enforce are made callable from the terminal gate). (3) Approval: C-03. (4) Commit truth: the RunRecord's commit cycle, already shared. (5) Final review: advisory on every executor; a refused or failed review is recorded and shown, and never flips a unit that is committed and verified (the Reviewer has no authority, `AUTH-A3-12`). (6) Requirement closure: evaluated once, at the terminal of the run, over the committed tree. (7) Terminal result: the result contract above |
| Persisted identity and state | RunRecord annotation *requirement_truth* (status and counts, written by the coordinator from the event the workflow already emits); the history row is written, or rewritten, **after** reconciliation with the reconciled status |
| Allowed mutation | The coordinator's terminal transition only |
| Forbidden bypass | A status computed by an executor and shown without reconciliation; a surface that states an outcome from anything but the reconciled result or the RunRecord |
| UNKNOWN behaviour | Missing or malformed requirement truth is not `ALL_VERIFIED`: the result is *APPLIED_REQUIREMENTS_OPEN* (committed) or *FAILED* (not committed) |
| Repair after a terminal gap (`GAP-A3-02`) | A terminal requirement gap names its owning unit through requirement ownership (`AUTH-A4-15`) and opens one bounded recovery episode for that unit under C-07's ledger. Second step of the slice; if ownership cannot be established the run ends *APPLIED_REQUIREMENTS_OPEN* or *FAILED* as above |
| Why minimal and generic | The executors are not merged. The truths converge by calling the same functions and by one terminal owner that already exists |
| Alternatives rejected | Merging the two executors (large rewrite, no evidence it is needed for truth). Keeping `record` as "success with open requirements" (the owner's rule: applied must not imply verified) |
| Acceptance proof | A3 probe P8 inverted: under every profile a run with an open requirement is not SUCCESS. A scripted plan run through both executors yields the same seven verdicts (one table-driven test). A refused final review on a committed, verified unit leaves the unit accepted on all three paths. The history row's status equals the reconciled status in a scripted contradiction |
| Negative controls | Removing the requirement-truth proof makes the P8 test fail; writing the history row before reconciliation makes the row test fail; a second plan validator makes the convergence test fail |
| Stop conditions | If making the two enforce-only-missing closers callable needs a change to how the plan sandbox is built, stop and record the closer set per executor as the contract instead |
| Owner policy inside it | Final review advisory everywhere (recommended) versus deciding everywhere; the name and exit code of *APPLIED_REQUIREMENTS_OPEN* |

### C-03 Approval subject authority (R-03)

| Field | Required contract |
|---|---|
| Truth | What a person, or a threshold, approved |
| Two subjects, kept apart | **Execution approval**: may this candidate's code be executed on my machine? Subject: one unit's candidate (today's held execution; unchanged in scope). **Apply approval**: may these bytes enter my workspace? Subject: the **commit transaction**: the exact batch (path, mode, content digest) one commit will apply. Direct and milestone: the unit's batch. Enforce: the plan candidate at the terminal commit, that is the cumulative plan delta |
| Required single owner | `policy/approval.py` for the subject and its identity; `workflow/stage_apply.py::approval_gate` and the enforce terminal commit ask through it; the commit service checks it |
| Authoritative inputs | The bound batch of `AUTH-A3-01` / `AUTH-A3-02` (never a re-read of the sandbox) |
| Persisted identity and state | The approval record stores the subject kind and the batch digest; the commit record stores the approval's id. (Split B of `GAP-A3-04`: audit binding) |
| Runtime binding | The commit refuses unless the approved digest equals the digest of the batch it is about to apply. (Split A: runtime authorization binding.) The desktop launch record's per-review digest becomes the same value |
| Size and risk measure (`GAP-A3-05`) | Evaluated on the apply subject, so on the cumulative plan delta under enforce. Decomposition by the Planner cannot lower it. Per-unit measures remain as early warnings only |
| Path class (from C-04) | A commit transaction that contains a Kriya project-authority path (configuration file names, the workspace skills root) requires an explicit apply approval that names those paths, in every autonomy mode, including guardrails below the size threshold. That approval is recorded as an authority-state change, distinct from a source approval |
| Forbidden bypass | An apply without an approval record where the mode or the path class requires one; an approval reused for another digest; a diff that omits a mode or binary change the batch carries |
| UNKNOWN behaviour | A batch whose digest cannot be computed, or an approval whose record cannot be written: nothing is applied |
| Why minimal and generic | One identity that already exists (the batch digest) is stored and compared; no new review kind except the path-class naming |
| Alternatives rejected | Summing unit line counts (the subject would still be the unit). A plan-level approval before execution (the bytes do not exist yet) |
| Acceptance proof | A3 probes as tests: the approval record carries the digest; a batch changed after approval is refused at commit; an enforce plan of N small units whose cumulative delta exceeds the threshold asks once at the terminal commit in guardrails mode; a transaction carrying `kriya.yaml` asks in every mode and the record names the file |
| Negative controls | Removing the digest comparison makes the changed-batch test fail; measuring per unit makes the split test fail |
| Stop conditions | If the enforce terminal commit cannot present a reviewable cumulative diff in the desktop review channel without a new review kind, stop and return for an owner decision on that kind |
| Owner policy inside it | Whether per-unit approval is intentionally sufficient (the owner's stated exception); whether CI workflow and Git attribute files join the path class |

### C-04 Mutation transaction authority (R-04)

| Field | Required contract |
|---|---|
| Truth | Which path may change the owner workspace or a repository |
| Required single owner | The candidate transaction (`candidate_transaction.py`, the write gate `policy/filesystem.py::AuthorizedFileWriter`, the commit service) is the only route of a model-originated change. Everything else is an operator action or a Kriya-state write |
| MCP (exception 1) | A planned MCP call never receives a writable workspace mount: the planned-governance resolution (`tools/tool.py::resolve_planned_tool_governance`) classes an MCP tool whose bound capability profile holds a workspace or additional write grant as operator-only for planned calls. The fact is read from the bound profile, never from the tool's description. Operator execution (`kriya tools execute`) is unchanged. The server's workspace binding uses the run's authority root, not the process directory |
| Skill learning (exception 2) | One skill-write entry on the Skills owner; every writer uses it. Run-time learning (scaffolds, extracted rules, staged facts) is written as unverified project state under C-05's project location, never into a repository and never committed to Git during a run. Promotion into a repository's skills directory is either a candidate change (through the write gate and C-03's path class) or an explicit operator command |
| Kriya project inputs (exception 3) | A closed table of project-authority relative paths beside the trusted control names. The write gate denies a model-directed write to them with a new reason code unless the user's goal itself names the file, in which case C-03's path-class approval applies |
| Persisted identity and state | None new for MCP. Skill writes carry provenance (exists). The path-class table is code |
| Forbidden bypass | Any `open()` for writing under a skills root outside the one entry; `git_commit_if_tracked` during a run; a planned call to a write-granted MCP tool |
| UNKNOWN behaviour | A capability profile that cannot be read: the tool is not plannable (as today for governance). A skills location that cannot be classified project or global: no write |
| Also | The fail-closed but untyped refusal at the guarded commit (`KERNEL-STAGING-WORKSPACE-BASE-ESCAPES-001`) gets a typed reason code |
| Why minimal and generic | Alternative A of `GAP-A5-03` is a classification, not a new transaction. One writer for skills replaces eleven |
| Alternatives rejected | Mounting the candidate into the MCP server (needs a per-run server lifetime and a candidate-aware mount; a real feature, deferred: GENUINE_FUTURE_FEATURE). Keeping mid-run Git commits behind a flag (a second repository writer stays) |
| Acceptance proof | With the stand-in MCP fixture: a planned call to a tool of a write-granted server is refused pre-effect and the workspace is byte-identical; the same tool runs by operator command. A scripted direct-shape run that fails leaves the skills directories and the repository HEAD unchanged. A5 probe P5 inverted for the two configuration names and a workspace skill path |
| Negative controls | Restoring plannable governance for the write-granted tool makes the first test fail; a structural test fails on any new skill writer outside the entry; removing a table entry makes the P5 test fail |
| Stop conditions | If an existing supported workflow depends on a planned write-capable MCP call, stop: that is evidence for the candidate-mounted alternative and an owner decision |

### C-05 Project-state namespace authority (R-05)

| Field | Required contract |
|---|---|
| Truth | Which project a piece of derived state belongs to |
| Principle | Project-derived mutable or semantic state carries authoritative project identity unless explicitly promoted into a governed global namespace |
| Required single owner | `core/state_paths.py`: one function beside `resolve_state_directory` returns the project state root for a workspace identity |
| Authoritative inputs | `workspace_identity` of the authority root (`AUTH-A5-01`) |
| Per class | **Code index and vector index (A):** live under the project state root; the manifest records the identity it was built for; a reader refuses a store of another identity with a typed "no index for this project" and never reads it. **Skills (C):** two roots: the shipped library, read-only to a run; and the project's run-written overlay under the project state root. A project's own repository skills directory stays possible and is bound the same way. **Run history (F):** each row records the workspace identity (and, with C-11, the RunRecord revision). **Learned knowledge (B) and release cache (D):** declared global stores under a named global location, read-only to a run except their one writer each (`kriya learn`; the registry lookup). **Control state, approvals, checkpoints (E):** unchanged |
| Fallback behaviour | An explicit `paths.memory` or `paths.skills` remains possible and is identity-bound. The packaged default is no longer a directory shared between projects. A fallback from a project-keyed lookup to an unkeyed store does not exist: absence is "no index", never another project's |
| Migration | An existing shared index has no attribution to migrate by: it is ignored and reported once. Skills already in the installation directory that the shipped manifest does not list are reported, not loaded as trusted |
| Forbidden bypass | A reader or writer that opens a project-class store by a path not derived from the identity (structural test over the store modules) |
| UNKNOWN behaviour | A store whose recorded identity is missing or differs: refused |
| Why minimal and generic | One path function and one identity check per store; the stores themselves do not change shape |
| Alternatives rejected | A project column in each shared database (every reader must filter correctly forever; one missed reader is a leak). Leaving the default and documenting it (the desktop product runs on the default) |
| Acceptance proof | A5 probes P1, P2 and P8 inverted: two unconfigured projects resolve different locations; after indexing A every reader called for B answers "no index"; indexing B leaves A's index intact; A's scaffold does not change B's knowledge gate. Kernel goldens no longer read the checkout's memory |
| Negative controls | Altering a store's recorded identity yields the refusal; removing the identity check makes the P2 test fail |
| Stop conditions | If a supported workflow relies on one index serving several workspaces (a monorepo opened at two roots), stop: the identity is the authority root, which already covers subfolders of one repository; anything else is an owner decision |

### C-06 Knowledge authority (R-06)

| Field | Required contract |
|---|---|
| Truth | Whether required knowledge is satisfied; which skills apply to the run |
| Required single owner | The Skills owner (`skills/skill.py::SkillEngine`). `KnowledgeGuard` asks it; it no longer lists a directory |
| Authoritative inputs | The skills loaded for this project (C-05), their tags, and their verified facts (`AUTH-A4-07`) |
| Coverage rule | A library is covered when a loaded skill's tags name it **and** the skill holds at least one trusted rule, or a person accepted the gap for this run. A folder, a scaffold or an unverified rule is "not covered" |
| Relevance rule | Skill applicability is decided once per run from the user's goal and the validated plan's declared libraries, recorded, and carried to every unit. A unit's composed text never selects skills |
| Persisted identity and state | The run's selected skill set with each skill's content digest, in the run events (exists as the active-skill manifest) and in the plan's control state |
| Allowed mutation | None by the gate. Scaffolding after a gap writes to the project overlay (C-04, C-05) and never closes the gate |
| Forbidden bypass | Closing the knowledge gate by any write the run itself made; a second selection |
| UNKNOWN behaviour | A skill that cannot be loaded or whose verification state cannot be read is "not covered". On the desktop the person can confirm a gap through a typed decision (today the desktop policy is strict with no confirmation: the run can only stop) |
| Why minimal and generic | The guard receives the engine's answer instead of a path; the selector is already sound |
| Alternatives rejected | Embedding-based relevance (invented capability; owner: do not invent embedding retrieval). Trusting unverified rules for coverage (the rule that created the false closure) |
| Acceptance proof | A4 probes P5 and P11 as tests: an empty folder and Kriya's own scaffold leave the gap; a skill with a trusted rule and a matching tag clears it. The Planner and every unit hold the same selected set in a scripted plan whose unit text names another library |
| Negative controls | Reverting to the directory listing makes P5 fail; selecting per unit makes the same-set test fail |
| Stop conditions | Scenario 04 relies on the current behaviour with offline mode (owner-accepted for that scenario): stop and re-decide that scenario's knowledge before changing the gate for it |

### C-07 Retry, blocker and durability authority (R-07)

| Field | Required contract |
|---|---|
| Truth | Whether the open finding is still the same one, how much allowance it has, and what survives an interruption |
| Constraints | No blocker registry and no typed blocker key as authority (owner ruling). Continuity is therefore defined by what already exists |
| Required single owner | `workflow/retry_policy.py` for the allowance (already the one grant path inside a unit); the unit checkpoint and `control/state.py::ControlState` for durability |
| Continuity rule | The failure-family signature is **declared** the allowance authority for one unit, with two corrections: (1) the predicate that lets a failure open a family is its own, fail-closed predicate (a closed list of candidate-failure types; an unlisted type inherits the previous family), separate from the predicate that keeps a finding in memory (`GAP-A1-07`); (2) the allowance ledger is carried across invocations of the same unit, so a new `GenerationState` continues the families and charges already recorded for that unit (`AUTH-A1-13`) |
| Persisted identity and state | Per unit, written at every failed attempt: the retry budgets, the family history, the open finding, the fallback state, the attempt count. Per plan, in `ControlState`: the enforce recovery state of `AUTH-A1-14` (cycle counters, reopened owners, the verifier semantic ledger, replay handles, human recovery decisions) |
| Allowance record (`GAP-A1-03`) | Every grant and charge is a typed run event and evidence record with the counters before and after |
| Where unit state lives under enforce | Under the owner workspace's control state, keyed by plan and unit, not under the plan sandbox: it must not depend on a repository's ignore rules (audit 3.4) |
| Accepted unit work (`GAP-A1-09`) | Owner policy. Proposed: accepted unit work stays in the candidate (atomic commit kept) and becomes **resumable**: the completion scope `candidate` is reusable by a resume that proves the sandbox bytes equal the recorded batch digests, instead of being discarded |
| Enforce resume (`GAP-A4-06`, audit 3.3) | A resume carries the earlier run's id as its parent and reuses the approved plan document; it does not ask the Planner for a byte-identical plan |
| Forbidden bypass | A new invocation of a unit that starts from zero allowance without recording why (a declared reset: a new plan revision, a human decision) |
| UNKNOWN behaviour | Unreadable persisted retry state: the unit runs with **no** scoped allowance left for families it cannot account for (it may make full-set attempts under the global ceiling) and says so; never a silent full grant |
| Small corrections | The configured no-progress limit is validated to its effective range (`GAP-A1-05`); the per-requirement cap that cannot fire is removed or made effective with its test (`GAP-A1-08`) |
| Why minimal and generic | The one grant path, the one checkpoint and the one control state are extended; no new authority object |
| Alternatives rejected | A blocker registry or typed key (rejected by the owner). Exact-output identity as the allowance authority (a one-line shift would grant a new allowance) |
| Acceptance proof | The run-10 reproducer as a deterministic scripted loop: the same assertion across attempts and across a re-invocation shares one allowance; an unlisted orchestrator failure does not replenish it; a process killed in attempt N resumes with N-1 charges recorded; a person is not asked twice for one recovery decision |
| Negative controls | The existing nine committed tests; reverting the predicate split makes the unlisted-failure test fail; dropping the ledger from the checkpoint makes the resume test fail |
| Stop conditions | The original symptom (run 10) must be reproduced pre-fix by the scripted loop before any change; if it cannot, stop |

### C-08 Run resource budget authority (R-08)

| Field | Required contract |
|---|---|
| Truth | The time and the amount of work one run may consume |
| AS-IS that stays | One generation clock per top-level run on the run lease, read by every unit invocation, retry, role, embedding and validator deadline; human waits excluded; the production seal of at most 3,600 seconds |
| Required single owner | `config/authority.py` for who may set the value; `control/run_coordinator.py` (the run lease) for a work ledger beside the clock |
| The levels | (1) Model call: unchanged. (2) Run generation time: the value becomes value-bounded like the containment bounds: a repository may lower it below the trusted value, never raise or remove it; where no trusted layer sets one, the run is declared unbounded in the preflight and in the result, never silently. (3) Attempts per invocation and (4) re-invocations: C-07's ledger. (5) **Work of the run** (new): a ceiling on unit invocations and on Developer attempts for the whole run, held on the run lease, set by the trusted layers, sealed by the production profile. (6) Profile maximum: unchanged for time; added for work |
| Resume | A resumed run records its parent run and the time and work the parent consumed; whether it continues that ledger or starts a new budget is an explicit operator choice at resume, recorded on the RunRecord |
| Authoritative inputs | The trusted configuration layers; the monotonic clock; the counts on the lease |
| Persisted identity and state | The run ledger (time consumed, unit invocations, attempts) as a RunRecord annotation at each unit boundary |
| Forbidden bypass | A repository value above the trusted one; a unit invocation not counted on the lease |
| UNKNOWN behaviour | No trusted time bound: under production a configuration error (as today); elsewhere "unbounded" stated in the preflight and the run events |
| Why minimal and generic | The clock is already on the lease; the value-bounded field class already exists |
| Alternatives rejected | A token budget as the primary bound (provider-reported, unknown before the call). A second clock per unit (the run clock already bounds every unit) |
| Acceptance proof | A5 probe P6 inverted for the value (a repository raise and a repository removal are violations; a lower value is accepted). B-P2 kept as the pinning test of the clock's scope. A scripted enforce plan whose re-invocations would exceed the work ceiling stops typed at the ceiling |
| Negative controls | Removing the value bound makes the P6 test fail; not counting re-invocations makes the ceiling test fail |
| Stop conditions | None beyond the dependency on C-07's ledger |
| Owner policy inside it | Whether the work ceiling and the resume ledger are needed before production, or only the value's authority class |

### C-09 Runtime identity and qualification authority (R-09)

| Field | Required contract |
|---|---|
| Truth | Requested model identity -> resolved binding -> qualification identity -> dispatched model, exact-bound |
| Required single owner | The caller that chooses the model (the role choke point `agents/agent.py::call_with_escalation`; the Developer choke point) resolves **one binding object** and passes it to `LLMClient`. `LLMClient` no longer looks a binding up by model name |
| Authoritative inputs | The effective configuration (and the routing plan). The binding is the configuration object itself: the role's own, a chain entry, or the primary |
| Unknown or ambiguous identity | A model name with no binding is refused, typed, before any provider contact. Two bindings that share a model name with different identity-relevant settings are a configuration error at load (the rule that exists for routing candidates, `routing_alias_conflicts`, applied to every binding) unless the configuration gives them distinct aliases |
| Production admission (alternative A) | Under `runtime_profile: production`, before the engine is built, every role's active binding (primary and chain) is assessed with the function the doctor uses; anything but QUALIFIED for the role's required capabilities refuses the run with a typed reason naming the role, the model and the record's state. The decision and the digests it used are recorded on the run. Outside production: recorded, never refused (unchanged) |
| Persisted identity and state | A `model.admission` run event and RunRecord annotation: per role the runtime digest, settings digest, policy digest and status |
| Forbidden bypass | A call whose binding was not resolved by the caller; `KRIYA_MODEL_RUNTIME_PROBE=0` under production (it makes every runtime non-exact: refused by the admission) |
| UNKNOWN behaviour | A non-exact runtime, a missing or stale record: not admitted under production |
| Why minimal and generic | The role-first resolver exists (`role_binding_for_model`); the assessment exists; both are moved to where the decision is |
| Alternatives rejected | Doctor as a mandatory external precondition (alternative B): needs a durable doctor result bound to configuration and identities, a freshness rule, and a run-start check of that binding, which is this check plus a second store. Keeping the name lookup with more matching rules (the lookup is the defect) |
| Acceptance proof | B-P1 and A6 P7 inverted: the same-name configuration is refused at load, or with distinct aliases the Reviewer's call uses its own adapter and window; an unknown name is refused. A production configuration with an unqualified primary is refused before any model call; the same configuration outside production starts; the doctor and the admission agree on one fixture |
| Negative controls | Restoring the name lookup makes the B-P1 test fail; skipping the primary in the admission makes the unqualified-primary test fail |
| Stop conditions | `tests/test_inf001_runtime_parity.py` pins the wire and the runtime digest: if passing a binding object changes a digest, stop (a digest change invalidates every qualification record) |

### C-10 Prompt trust provenance authority (R-10)

| Field | Required contract |
|---|---|
| Truth | The origin of each piece of text in a model request, and what it may influence |
| Required single owner | `workflow/untrusted_context.py` for rendering (the fence), and the retry state for typing: fetched text is held in its own field and never concatenated into the error context |
| Authoritative inputs | The origin of the value at creation: validator output, Kriya text, the user's words, repository bytes, model output, skill text, learned reference, **fetched during this run** |
| Rule | A value of an untrusted class reaches a request only through `fence_untrusted_reference`, in the user message, never in a system message. Its only consumer is the prompt builder of the request it was fetched for. It is not read by the targeted-test selection, the scaffold builders, the Reviewer's rejected-candidate prompt or the lesson extraction |
| Persisted identity and state | None persisted beyond the run; the evidence record of the request gains the list of untrusted segments with their source URL and digest, and each outbound query is a typed run event |
| Skill-rule route | Rules extracted from a fetched page stay unverified and fenced until promoted (C-04, C-06) |
| Forbidden bypass | Any consumer of `state.error_context` receiving fetched text (the field no longer contains it); any new bytes source not listed in the bytes-source inventory test |
| UNKNOWN behaviour | Text whose origin is not known to the builder is rendered as untrusted |
| Why minimal and generic | A separate field already exists on the retry package (`advisory_context`); the fence exists; the change is to stop the concatenation and route one field |
| Alternatives rejected | Structured multi-message trust roles at the provider (no provider contract for it; headings would remain the mechanism). Removing live lookup (a product feature the owner has not withdrawn) |
| Acceptance proof | A6 probe P1 inverted, items (a) to (e): the page text is inside the fence with its markers neutralized; it is absent from the Reviewer's system prompt, from the scaffold input, from the targeted-test selection input and from the lesson-extraction prompt |
| Negative controls | Re-concatenating makes each item fail; the inventory test fails until the live lookup is listed |
| Stop conditions | None; independent of every other contract |

### C-11 Evidence and diagnostic truth (R-11)

| Field | Required contract |
|---|---|
| Truth | What each record says, and what a reader may conclude from it |
| Required single owner | Each record keeps its writer; one written evidence contract (this section, then `docs/design.md`) states the class of each |
| Integrity classes | RunRecord: **authoritative** (revisioned, ownership-bound; the outcome of record). History row: **bound copy**: carries the workspace identity and the RunRecord revision it was written from; written after reconciliation; a reader compares and reports `consistent`, `differs` or `record unavailable`. Attempt evidence: **integrity against damage**: the chain and seal detect corruption and partial edits; the manifest binds it to the workspace and the base revision; `run.closed` binds it to the RunRecord revision; it is not an authenticity proof and the desktop says "intact", not "verified", or defines the word. Run log: **diagnostic**, with a retention owner and private file mode |
| Authenticity | Owner policy. Recommended: Kriya's evidence is diagnostic history of a local, single-user tool whose state directory is private; an authenticated store (a key outside the state directory) is a GENUINE_FUTURE_FEATURE. If the owner requires tamper-evident evidence for production, it becomes a slice of its own and a blocker |
| Diagnostic truth | A label or count is written by the owner of the decision it describes, at the decision: attribution kind (`unknown` is its own value), `attempts`, retries triggered, gate purpose (the vocabulary covers every orchestrating entry), the role of a model call (knowledge channels scoped), held-gate evidence written after the gates run. One run identity chain: a resumed run records its parent run id |
| Retention | Every record class has a retention owner (launch records and review sidecars, run logs) |
| Forbidden bypass | A surface stating an outcome from the history row without the comparison; a record class without a stated integrity class |
| UNKNOWN behaviour | A row with no binding (written before this contract) is shown as `record unavailable`, never as consistent |
| Why minimal and generic | Two columns, one comparison, wording, and existing fields moved to their owners |
| Acceptance proof | A6 probe P5 as a test: a row whose status differs from its RunRecord is reported as differing; a row of another workspace is not attributed to this one. P4 pinned as the documented limit. Each label fix carries its own regression (the registry rows under R-11) |
| Negative controls | Removing the comparison makes the P5 test fail |
| Stop conditions | The kernel goldens pin every event and record byte: every change here regenerates goldens once, in one slice, with the state-machine tier and the architecture tier run |

### C-12 Certification harness determinism (R-12) and C-13 Verification environment verdict (R-13)

| Field | C-12 | C-13 |
|---|---|---|
| Truth | A red certification run is a statement about the product | A failed gate is a statement about the candidate only when the gate ran |
| Required owner | The test suite (`tests/conftest.py` contract) | `tools/validate.py` and each launch site |
| Contract | No fixed machine-global resource (ports, paths); wall-clock margins replaced by injected clocks; goldens independent of checkout path and memory; one xdist group per shared external resource; tracked bytecode removed from the index | Every container or environment refusal returns a typed environment verdict with a reason code; no launch site converts it to a gate verdict of False or True; the retry machine charges nothing to the candidate for it |
| Acceptance proof | The fifteen rows' own reproducers green under load; one full suite on the final tree | A scripted daemon refusal at every launch site (candidate gates, terminal gate, baseline, requirement closers) yields the typed verdict; this test is the missing evidence of the Phase-D blocker |
| Negative controls | Never weaken an assertion, widen a baseline or skip a test | Converting one refusal to False makes the test fail |
| Stop conditions | A row that turns out to be a product defect leaves this slice | If a launch site is found that does convert a refusal, that is a confirmed product defect: fix now, with the reproducer |

## 2. Corrections in the form the documentation addendum requires

| Correction | Root issue | First wrong owner (AS-IS) | Current owner | Required owner | Dependencies | Slice |
|---|---|---|---|---|---|---|
| C-01 | No semantic contract before generation | None exists | `contract_compilation.py` (statements only) | `contract_compilation.py` (statements and semantic state) | - | S5 |
| C-02 | Two executors, seven truths; status without requirement truth | The status rule (`AUTH-A3-10`, `AUTH-A3-11`) | Each executor | `control/run_coordinator.py` | C-01 | S6 |
| C-03 | No approval subject with identity | `stage_apply.py::approval_gate` | The gate, per unit | `policy/approval.py` + commit check | C-02, C-04 | S7 |
| C-04 | Three writers outside the candidate transaction | `MCPManager.start_all`; the skill writers; the write gate's missing class | Several | The candidate transaction; one skill entry | C-05 | S4 |
| C-05 | Derived state has no project owner | `config/resolution.py` default anchor | None | `core/state_paths.py` | - | S3 |
| C-06 | A folder closes the knowledge gate; relevance decided twice | `KnowledgeGuard.check_goal`; `stage_analysis.py` | The guard; two selectors | `SkillEngine` | C-05, C-04 | S4 |
| C-07 | Allowance on a text difference; nothing durable | `retry_policy.py::observe_failure_family`; no durability owner | The policy; memory | The policy (declared) + checkpoint + `ControlState` | C-01 (weak), C-13 | S8 |
| C-08 | The run clock's value has no trusted owner; no bound on work or across resumes | The configuration class of the budget | The run lease (clock); any configuration layer (value) | `config/authority.py` (value) + the run lease (work ledger) | C-07 | S8 |
| C-09 | Binding by name; no production admission | `LLMClient._binding`; `_enter_developer_model` (fallback only) | `LLMClient` | The role and Developer choke points; one admission | - | S1 |
| C-10 | Fetched text untyped | `_augment_error_with_live_lookup` | `state.error_context` | A typed field + the fence | - | S2 |
| C-11 | No evidence contract; labels apart from decisions | None | Four writers | The same writers under one contract | C-02 | S6 |
| C-12 | Harness races | The tests | - | The suite contract | - | S0 |
| C-13 | Environment refusal versus gate verdict not proven at every site | Unknown until characterized | `tools/validate.py` | The same, proven | - | S0 |

Why each is minimal, the alternatives rejected, the tests, the negative controls and the stop conditions are in the
contract tables of section 1.

## 3. Implementation slices (after Phase D and only on the owner's authorization)

A small number of large, coherent, safe slices. Each slice is one repair batch in the sense of the engineering rules:
reproduce the original symptom, fix at the root owner, regression, mutation, adjacent tiers, lint; one full suite at
the batch boundary where the rules reserve it.

| Slice | Contracts | Content | Why one slice | Size and risk | First post-fix gate (the original symptom) |
|---|---|---|---|---|---|
| S0 Certification base | C-12, C-13 | Test-only hygiene for the fifteen harness rows; scripted daemon-refusal tests at every launch site (the Phase-D blocker's evidence); fix any site that fails them | Everything later is certified by a suite that must be deterministic; the blocker must be resolved before verification truth is relied on | Small; tests only unless C-13 finds a defect | The rows' own reproducers; the scripted refusal |
| S1 Runtime identity and admission | C-09 | Binding passed by the caller; same-alias rule for every binding; unknown name refused; production admission | One truth chain, one module pair (`llm.py`, the choke points), no dependency | Small to medium; touches every model call's entry: parity test is the guard | B-P1, A6 P7 |
| S2 Prompt trust | C-10 | Fetched text typed, fenced, routed to one consumer; query events; inventory entry | Independent; closes a P1 security path on an opt-in feature | Small | A6 P1 (a) to (e) |
| S3 Project state | C-05 | Project state root; index, vector store, skill overlay and history rows bound to identity; global stores named | The base for knowledge and mutation; changes locations once | Medium; migration is "ignore and report" | A5 P1, P2, P8 |
| S4 Knowledge and mutation | C-06, C-04 | Coverage and relevance from the engine; one skill writer; no mid-run Git commit; planned write-granted MCP tools operator-only; project-authority path class | Both rewrite the same writers and the same gate; the path class is needed by S7 | Medium | A4 P5, P11; A5 P5; the MCP stand-in fixture |
| S5 Semantic contract | C-01 | Semantic state in the sealed contract; closure rules; unmapped verifier held; ambiguity outcomes | The root of requirement truth; nothing else may define it | Medium to large (schema version, goldens) | A2 P8, P9; the `src/` layout probe |
| S6 Result contract and evidence | C-02, C-11 | Terminal vocabulary with requirement truth; seven truths through shared functions; history row after reconciliation and bound; evidence contract wording; label and purpose fixes; retention owners | Result words and the records that carry them change together: one golden regeneration | Large (kernel goldens, desktop wording) | A3 P8, P1, P3; A6 P5 |
| S7 Approval subject | C-03 | Digest in the approval record, checked at commit; apply approval on the commit transaction; cumulative measure; path-class approval | Needs S6's commit truth and S4's path class | Medium | The A3 approval probes; the split-plan scenario |
| S8 Retry, durability and budget | C-07, C-08 | Predicate split; allowance ledger and its record; unit and plan state persisted outside the sandbox; resumable candidate completions; value-bounded run time budget, work ceiling and resume ledger | One accounting model for attempts and time; the run-10 symptom is its gate | Large (state machine, checkpoints) | The run-10 scripted reproducer; A5 P6 |

## 4. Dependency order (derived, not assumed)

From the dependency graph of audit section 9 and the content of the slices:

```mermaid
flowchart LR
    S0[S0 certification base<br/>C-12, C-13] --> S5
    S0 --> S8
    S1[S1 runtime identity<br/>C-09]
    S2[S2 prompt trust<br/>C-10]
    S3[S3 project state<br/>C-05] --> S4[S4 knowledge and mutation<br/>C-06, C-04]
    S5[S5 semantic contract<br/>C-01] --> S6[S6 result contract and evidence<br/>C-02, C-11]
    S4 --> S7[S7 approval subject<br/>C-03]
    S6 --> S7
    S5 --> S8[S8 retry, durability, budget<br/>C-07, C-08]
    S6 --> S8
```

| Step | Slice | Why here |
|---|---|---|
| 1 | S0 | Makes certification deterministic and resolves the one Phase-D blocker; no product risk |
| 2 | S1, S2 (either order, or together) | Independent of everything; both P1; both small; both testable with existing probes |
| 3 | S3 | The base of S4; independent of the semantic chain |
| 4 | S4 | Needs S3's locations |
| 5 | S5 | The base of S6 and S8; independent of S3 / S4, so it can run in parallel with steps 3 and 4 if two people work |
| 6 | S6 | Needs S5 (requirement truth) |
| 7 | S7 | Needs S6 (commit truth) and S4 (path class) |
| 8 | S8 | Needs S6's terminal vocabulary for the repair-after-terminal step and S0's environment verdict so an environment refusal is never charged; last because it changes the state machine everything else is verified with |

The order differs from the expected list in one place: runtime identity (expected seventh) has no upstream
dependency and is the cheapest P1 to close, so it moves to step 2; and state isolation precedes mutation and
knowledge because both write into its locations. S6 before S8: the retry machine's repair-after-terminal route needs
the terminal vocabulary; the reverse dependency does not exist.

Certification: one full suite after S0 (the base), then focused gates per slice (state-machine tier, architecture
tier, the slice's probes turned tests, mutation checks), and one full suite on the final tree, per the standing
"full suite is certification" rule.

## 5. What stays unchanged

SEC-009 configuration authority and its approval; TOOL-002 invocation approval and TOOL-003 containment; the write
and read gates' existing refusals; the guarded commit and `kriya recover`; the candidate identity and verification
binding; the provider contract, the runtime port and both adapters; the qualification policy and records; the
verifier semantic review; the fence; the redaction owner; the attempt-evidence store's format (two manifest facts are
already there). No executor merge, no blocker registry, no new specification language, no MCP candidate mount, no
authenticated evidence store.

## 6. Clauses added for the integration findings of the audit (section 12 of `06-GAP-AUTHORITY-AUDIT.md`)

Each finding is closed by a clause in an existing contract; none needs a contract of its own.

| Finding | Contract | Clause (`PROPOSED`) | Acceptance proof |
|---|---|---|---|
| INT-01 stop reason as a string protocol | C-11 | A stop carries a typed reason code from one closed table and a separate message; `state.environment_failure` keeps only environment stops; `terminal_failure_category` reads the code, never a prefix. An unknown code is its own category, never "environment" | A table-driven test over every producer: each stop yields its category by code; a stop with an unregistered code fails a tripwire test; `tests/test_outcome.py` goldens regenerated once |
| INT-02 plan identity | C-07, C-03 | The executed plan has one identity, the content hash of the validated plan, recomputed at each deterministic revision and recorded with its predecessor; a resume reads the approved plan document by that identity; a plan-level approval subject (C-03) names it | A scripted revision leaves a chain of two identities on the plan document; a resume of the revised plan reuses its completions without a new Planner call |
| INT-03 two bindings of one model name | C-09 | In the contract already | B-P1 inverted |
| INT-04 desktop requirement truth and pre-reconciliation status | C-02, C-11 | The RunRecord annotation *requirement_truth* is the value every surface reads; the history row is written after reconciliation | A scripted run whose last attempt differs from the applied candidate shows the applied candidate's truth |
| INT-05 `kriya fix` has no requirement set | C-02 | A fix run's contract is explicit: its one requirement is "the reported failure no longer reproduces", closed by the gate that reproduced it (the failing command or test, re-run on the candidate). A fix run with no reproducible failure is refused or runs as an ordinary goal | A scripted fix whose failure persists ends not successful; one whose failure is gone and whose suite passes ends *SUCCESS* with that requirement verified |
| INT-06 direct resume opens the knowledge gate | C-06 | The gate's decision (covered by which skill digests, or accepted by whom) is persisted with the unit checkpoint and re-evaluated on resume against the current skill set; a checkpoint's existence proves nothing | A resume after the covering skill was removed stops at the gate |
| INT-07 skill facts fail open on read errors | C-06 | A skill folder that cannot be loaded is reported in the run result and counts as "not covered"; an unreadable provenance record makes every rule of that skill unverified | A4 probe P8 extended: a corrupt provenance file renders the rules under "Unverified Rules" |
| INT-08 unresolved criterion id | C-01 | Rendering a unit's semantic context refuses a criterion id that does not resolve (typed), instead of omitting it | A plan fixture with a dangling id is refused at validation and, if forced past it, at rendering |
| INT-09 invalid `sensitive_paths` pattern | C-03 | The pattern list is validated when the configuration loads; an invalid pattern is a configuration error, never a skipped trigger | A configuration with a malformed pattern does not load |
| INT-10 a resumed run is a new run to every record | C-11, C-08 | A resumed run records its parent run id on the RunRecord, in the evidence manifest and in the history row | The three records of a resumed scripted run name the parent |
| INT-11 enforce unit checkpoints depend on an ignore rule | C-07 | In the contract already (unit state lives under the owner workspace's control state) | The resume test passes in a repository that does not ignore `.kriya/` |
| INT-12 an internal Kriya error is charged to the candidate | C-07, C-11 | An internal error charges nothing and is labelled as internal | The existing internal-error fixture leaves the budgets unchanged |
| INT-13 lesson extraction reads the mixed error context, unattributed | C-10, C-11 | In C-10 (the field no longer contains fetched text); the call runs under a role scope | A6 probe P1 and the role-metrics test |
