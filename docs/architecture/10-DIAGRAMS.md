# 10 - Diagrams

Canonical runtime baseline: commit `3eded93c0cfb50f8855d04510e3f9aa2cd5208f7`. AS-IS. Every node carries the row
that holds its fact; a diagram defines nothing and must agree with the tables it cites. Dotted edges lead to the
gap or root issue at that point. Further diagrams of single slices are in `00-END-TO-END-LIFECYCLE.md`.

## D-1 End-to-end lifecycle

```mermaid
flowchart TD
    G[Goal intake<br/>AUTH-A4-01, AUTH-A4-02] --> SC[Verification contract compiled and sealed<br/>AUTH-A2-02]
    SC -. no product semantics, no ambiguity state .-> R01[R-01]
    SC --> W[Workspace: identity, membership, sandbox, configuration<br/>AUTH-A5-01..03, AUTH-A5-10]
    W --> K{Knowledge gate<br/>AUTH-A4-05, SM-A4-03}
    K -- gap, not confirmed --> XK[knowledge_gap<br/>FR-A4-02]
    K -- a folder name covers --> R06[R-06]
    K -- proceed --> SH{Execution shape<br/>AUTH-A4-04}
    SH -- direct / milestone --> PD[Planner answer classified<br/>AUTH-A4-11, SM-A4-02]
    SH -- enforce --> PE[Structured plan validated<br/>AUTH-A4-10, SM-A4-01]
    PD --> U[Unit invocation]
    PE --> U
    SH -. seven truths differ .-> R02[R-02]
    U --> CX[Context and request<br/>AUTH-A4-09, AUTH-A6-05, AUTH-A6-09]
    CX --> M[Model call<br/>AUTH-A6-01..04, SM-A6-01]
    M --> C[Candidate staged through the write gate<br/>AUTH-A3-01, AUTH-A5-04]
    C --> A{Approval needed?<br/>AUTH-A3-03}
    A -- yes --> H[Person decides; execution held in human-in-the-loop<br/>AUTH-A3-04..06]
    A -- no --> GT
    H --> GT[Gates<br/>AUTH-A3-07, AUTH-A3-08]
    GT -- fail --> RT[Retry policy<br/>AUTH-A1-01..07, SM-A1-01]
    RT -- another attempt --> CX
    RT -- stop --> T
    GT -- pass --> RQ[Requirement closure<br/>AUTH-A3-09]
    RQ --> CM[Commit<br/>AUTH-A3-02, AUTH-A3-14, SM-A3-03]
    CM --> T[Terminal result reconciled with the RunRecord<br/>AUTH-A3-10, AUTH-A3-11, audit 3.5]
    RQ -. truth not consumed by the status .-> R02
    T --> EV[Records: RunRecord, history row, evidence, log<br/>AUTH-A6-11]
```

## D-2 Authoritative state machine of one run (RunRecord)

```mermaid
stateDiagram-v2
    [*] --> NEW: begin_mutating_run (lock taken, prior commit state assessed)
    NEW --> RUNNING: first coordinated mutation (goal hash, config fingerprint)
    RUNNING --> PLANNING: stage marker
    RUNNING --> CANDIDATE: stage marker
    PLANNING --> CANDIDATE: stage marker
    CANDIDATE --> VERIFYING: stage marker
    VERIFYING --> CANDIDATE: another attempt
    CANDIDATE --> COMMIT_ELIGIBLE: begin_commit (candidate hash)
    VERIFYING --> COMMIT_ELIGIBLE: begin_commit
    COMMIT_ELIGIBLE --> COMMITTED: settle_commit
    COMMITTED --> PLANNING: a later milestone or subtask
    COMMITTED --> CANDIDATE: a later milestone or subtask
    COMMITTED --> SUCCESS: payload claims success AND every work unit VERIFIED
    RUNNING --> SUCCESS: no commit cycle at all, payload claims success
    CANDIDATE --> FAILURE: payload failed, exception, or run exit
    VERIFYING --> FAILURE: payload failed, exception, or run exit
    COMMIT_ELIGIBLE --> UNCERTAIN: commit state unknown
    UNCERTAIN --> RECOVERED: kriya runs recover (terminal status FAILURE or NEEDS_REVIEW, never SUCCESS)
    SUCCESS --> [*]
    FAILURE --> [*]
    RECOVERED --> [*]
    note right of SUCCESS
      control/run_record.py: _STAGE_TRANSITIONS, _COMMIT_SOURCES, begin_commit, settle_commit;
      run_coordinator.py: _complete_successful_run, _fail_active_run (audit 3.5).
      FAILURE and UNCERTAIN are reachable from every non-terminal state (not all drawn).
      Requirement truth is not among the proofs (GAP-A3-01, R-02).
    end note
```

## D-3 Failure and recovery

```mermaid
flowchart TD
    F[Attempt failed] --> CL{Class of the failure<br/>FR-A1-01..16}
    CL -- validator finding about the candidate --> OF[Replaces the open finding<br/>AUTH-A1-01]
    CL -- delivery, advisory, resource refusal, unclassified --> IN[Inherits the previous family<br/>FR-A1-02..05]
    CL -- typed stop / environment --> ST[STOP_ENVIRONMENT, nothing charged<br/>FR-A1-07]
    CL -- unlisted orchestrator refusal --> G7[May replenish the allowance<br/>GAP-A1-07, R-07]
    CL -- scope conflict --> SC2[Unit stops; enforce recovery<br/>FR-A1-09, SM-A1-04]
    OF --> FAM{New family?<br/>AUTH-A1-03}
    FAM -- yes --> RS[Scoped budgets reset<br/>MUT-A1-02]
    FAM -- no --> CH[Charge<br/>AUTH-A1-06]
    IN --> CH
    RS --> NX[Next mode<br/>AUTH-A1-05]
    CH --> NX
    NX -- targeted / fallback / full set --> AT[Next attempt]
    NX -- exhausted or no progress --> END[Unit fails]
    SC2 --> OR{Owner resolvable?<br/>FR-A1-17, FR-A1-18}
    OR -- yes --> RE[Owner reopened, fresh unit budgets<br/>BYP-A1-01, AUTH-A1-11]
    OR -- no --> END
    RE --> AT
    ST -. reason travels as a prefixed string .-> I1[INT-01, R-11]
    ST -. is every environment refusal typed? .-> R13[R-13 Phase-D blocker]
```

## D-4 Approval and mutation

```mermaid
flowchart TD
    C[Candidate batch: path, mode, digest<br/>AUTH-A3-01] --> TR{Trigger?<br/>AUTH-A3-03}
    TR -- human-in-the-loop, sensitive path, size, profile --> D[Diff rebuilt from the sandbox]
    D --> P[Person: approved / declined / unavailable<br/>AUTH-A3-04]
    P -- approved --> REC[Record: launch record with a digest of the diffs, or an event<br/>AUTH-A3-13]
    P -- declined / unavailable --> X[Nothing applied<br/>FR-A3-06]
    TR -- none --> V
    REC --> V[Verified binding of the batch<br/>AUTH-A3-02]
    V --> CM[Commit refuses unless batch equals the verified binding<br/>AUTH-A3-14]
    REC -. approved digest never compared with the committed batch .-> R03[R-03, GAP-A3-04]
    TR -. per unit under enforce: no plan-level subject .-> R03b[R-03, GAP-A3-05]
    MCP[Planned MCP call, write grant<br/>AUTH-A5-07] -. owner workspace, no candidate .-> R04[R-04, GAP-A5-03]
    SK[Skill learning, eleven writers<br/>MUT-A5-08] -. outside the write gate, Git commits .-> R04
    C -. may carry kriya.yaml and skills .-> R04b[R-04, GAP-A5-02]
```

## D-5 Retry and blocker lifecycle

```mermaid
stateDiagram-v2
    [*] --> NoFinding
    NoFinding --> Open: candidate failure (AUTH-A1-01)
    Open --> Open: same family, charged (AUTH-A1-06)
    Open --> Replaced: another candidate failure (finding_from_failure)
    Replaced --> Open
    Open --> NewFamily: signature differs and was never seen (AUTH-A1-03)
    NewFamily --> Open: scoped budgets reset (MUT-A1-02)
    Open --> Gone: unit invocation ends, Best-of-N reset, or process ends
    Gone --> [*]
    note right of Open
      No identity, no "resolved" state (AUTH-A1-02, SM-A1-02).
      Four definitions of "the same failure" (AUTH-A1-12).
      Nothing persisted (AUTH-A1-09). R-07.
    end note
```

## D-6 Semantic contract and verifier

```mermaid
flowchart TD
    G[Goal sentences<br/>AUTH-A2-01] --> CT[Sealed verification contract: statements, kinds, closers<br/>AUTH-A2-02]
    CT -. no behaviour clauses .-> R01[R-01, GAP-A2-01]
    CT --> DEV[Developer writes code and tests<br/>AUTH-A6-09]
    DEV --> RUN[Test gate runs, complete report]
    RUN --> ID{Executed identity maps to a run-written file?<br/>AUTH-A2-06}
    ID -- no mapping --> FO[Read as not run-authored<br/>GAP-A2-02]
    ID -- yes --> SUB[Verifier subject, fingerprint<br/>AUTH-A2-07]
    SUB --> HU{Person, typed channel}
    HU -- valid --> WD[Bytes write-denied; unit proceeds<br/>AUTH-A2-08]
    HU -- invalid --> RG[One regeneration of that path]
    HU -- cannot decide / unattended --> NR[Needs review]
    WD --> FAILS{Accepted verifier fails?}
    FAILS -- yes --> OWN[Production owner by unique locator and plan<br/>AUTH-A2-09]
    OWN --> ATT[Attribution replaced by the ruling<br/>MUT-A2-06]
```

## D-7 Workspace mutation and containment

```mermaid
flowchart LR
    subgraph Owner workspace
      OW[Tracked files]
      KY[.kriya control state<br/>AUTH-A5-08]
    end
    subgraph Candidate sandbox
      SB[Linked work tree or scoped snapshot<br/>AUTH-A5-03]
    end
    subgraph Kriya state
      ST[Approvals, launches, evidence, traces]
      MEM[paths.memory, paths.skills: installation default<br/>AUTH-A5-09]
    end
    DEVW[Developer batch] -- write gate<br/>AUTH-A5-04 --> SB
    SB -- guarded commit<br/>AUTH-A3-14 --> OW
    GATE[Gates run model-written code] -- containment --> SB
    BOOT[Greenfield bootstrap<br/>MUT-A5-01] --> OW
    MCPW[Planned MCP call, write grant] -. in place .-> OW
    SKW[Skill writers] -. plain writes, Git commits .-> MEM
    IDX[kriya analyze, auto-index<br/>MUT-A5-09] --> MEM
    MEM -. one namespace for every project .-> R05[R-05]
    MCPW -.-> R04[R-04]
    SKW -.-> R04
```

## D-8 Checkpoint and resume

```mermaid
flowchart TD
    U[Unit reaches a stage: plan, design, candidate gates passed, developer success] --> CK[Stage checkpoint with fingerprints<br/>AUTH-A1-09, MUT-A1-12]
    E[Enforce subtask transition] --> CS[ControlState: states, plan hash, digests, completion scope<br/>AUTH-A1-10, MUT-A1-13]
    R[Operator asks to resume] --> NEWRUN[New top-level run: new id, new clock, new evidence store<br/>audit 3.3, B-P2]
    NEWRUN --> VAL{Fingerprints and digests match reality?}
    VAL -- no --> FRESH[Fresh run or resume_refused]
    VAL -- yes --> REUSE[Reuse plan, design, verified candidate]
    REUSE --> LOST[Not restored: retry budgets, open finding, fallback state, recovery state, semantic decisions<br/>AUTH-A1-14, SM-A1-03]
    CS --> SCOPE{Completion scope is workspace?}
    SCOPE -- candidate --> DISC[Completions discarded; plan runs from subtask 1<br/>GAP-A1-09]
    SCOPE -- workspace --> SKIP[Completed subtasks skipped]
    CK -. under the plan sandbox for an enforce unit: survives only if .kriya is ignored .-> I11[INT-11, R-07]
    LOST -.-> R07[R-07]
```

## D-9 Runtime and provider dispatch

```mermaid
flowchart TD
    CALLER[Role or Developer choke point<br/>AUTH-A6-01, AUTH-A6-07] --> BIND[LLMClient._binding by model name]
    BIND -. first match, primary first; unknown name = primary .-> R09[R-09, B-P1]
    BIND --> ADP{Adapter registered?<br/>AUTH-A6-02}
    ADP -- no --> X1[UnknownRuntimeAdapterError]
    ADP -- ollama / ollama_native --> FP[Runtime fingerprint<br/>AUTH-A6-03]
    FP --> PLAN[Provider request plan: settings proven or refused]
    PLAN --> WIN{Window state<br/>AUTH-A6-04}
    WIN -- served below requested; mismatch under production --> X2[Refused]
    WIN -- ok --> BUD{Dispatch fits?<br/>AUTH-A6-05}
    BUD -- no --> X3[CONTEXT_BUDGET_UNSATISFIABLE]
    BUD -- yes --> DL{Run clock has time?<br/>AUTH-A6-10}
    DL -- no --> X4[INFERENCE_DEADLINE_EXHAUSTED]
    DL -- yes --> SEND[Adapter sends]
    SEND --> AFTER{Truncation or unobserved window?}
    AFTER -- yes --> X5[PROVIDER_CONTRACT_VIOLATION]
    AFTER -- no --> RES[CompletionResult: digests, role metrics<br/>AUTH-A6-08]
    QUAL[Qualification record<br/>AUTH-A6-06, SM-A6-02] -- fallback and retry identity only, under production --> CALLER
    QUAL -. primary and other roles never checked in a run .-> R09
```
