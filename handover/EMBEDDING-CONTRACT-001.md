KRIYA — NEXT CONSOLIDATED EXECUTION BATCH
Execute this entire batch sequentially without waiting for owner approval between stages.
General operating rule
Continue automatically whenever the previous stage satisfies its exit gate.
Stop only if one of these occurs:
1. a generic Kriya safety/correctness defect that could corrupt code, violate authority/containment, leak data, or create false success;
2. a destructive/unresolvable Git merge problem;
3. an architectural choice materially outside the design below;
4. a quality gate cannot be made green without weakening an existing invariant.
Do not stop for:
- model variance;
- imperfect generated prose;
- a non-critical demo observation;
- optimization opportunities;
- timing improvements;
- known/deferred backlog items;
- evidence formatting;
- minor refactoring choices.
Record those and continue.
Run the full suite once per major implementation batch, not after every small fix.
PHASE 1 — Publish the current validated work
KNOW is closed and frozen.
Current validated runtime:
demo-runtime-7
product commit: ad5598d
wheel:
fc24bf9a6fe3cc32b375b2d5f56ea0269004504003daec962ff8aa12ec507c7d

Push the current development branch and its existing commits to the remote.
Do not merge yet.
Preserve all KNOW evidence.
PHASE 2 — LEARN validation
Use demo-runtime-7 unchanged.
Do not create another runtime merely for LEARN.
LEARN-A
Validate controlled public knowledge acquisition using:
Apache ActiveMQ Artemis 2.54.0

Use the already-approved acquisition configuration.
Public lookup is explicitly authorized for this acquisition only.
Verify:
public information only
no proprietary content leaves the host
acquired knowledge is persisted locally
resulting artifact is genuinely Artemis 2.54 knowledge

Do not manually improve the learned artifact.
LEARN-B
Switch back to the normal production posture.
Require:
web/outward lookup disabled
same runtime-7
same provider/model identities
learned artifact retained locally

Run the agreed small Artemis task once.
Verify only:
learned artifact loaded
no outward lookup
generated result materially uses learned knowledge
compile/test/runtime result interpretable

If LEARN works, mark it FROZEN and continue immediately.
If the model produces a poor answer but Kriya behaved correctly, record it and continue.
Do not turn LEARN into another certification project.
PHASE 3 — EMBEDDING-CONTRACT-001
Implement the embedding contract on the existing development branch.
Re-read the current implementation first.
Do not blindly implement the old review document.
Some findings may already have been closed by later work.
Classify each item:
ALREADY CLOSED
STILL OPEN
PARTIALLY CLOSED
SUPERSEDED

Implement only the remaining real gaps.
Required final embedding invariants
E1 — no fake vectors
Never replace an embedding failure with a zero vector.
Validate every embedding response:
expected response count
dimension
finite values
non-zero norm

Fail with typed embedding errors.
For indexing:
A changed file must never become the current vector state unless all vectors for that file were produced successfully.

Use transactional per-file publication.
A previous vector generation may remain physically stored, but it must be marked stale/non-current and never masquerade as the current revision.
E2 — no silent truncation
Use native Ollama embedding support with:
/api/embed
truncate: false

Provider refusal is authoritative.
Estimation may be used only as an optimization.
Overlong content must be segmented deterministically with bounded progress.
Preserve:
parent chunk identity
segment index
source span
source revision

Record an embedding_admission_miss when local estimation accepted something the provider refuses.
E3 — trustworthy embedding identity
Index identity must include enough information to prove semantic compatibility, including:
embedding artifact/model digest
provider/adapter identity
dimension
served embedding context
document/query prefix policy
preprocessing version
segmentation version

Bind indexed vectors to:
workspace
path
raw source digest
source span
index generation
embedding fingerprint

A fingerprint mismatch must never query stale vectors as current.
E4 — provider/security/deadline behavior
Embedding transport belongs under Kriya security authority.
Require:
trust_env = false
one explicit endpoint
no hidden /v1 → legacy fallback
bounded timeout
at most one transient retry

No retry for:
input too long
malformed response
identity mismatch
security refusal
root deadline exhaustion

Query-time embeddings during generation consume the existing root run deadline.
Offline standalone analyze may use its own operation deadline.
Do not mislabel root-deadline exhaustion as embedding failure.
Query degradation
If semantic query embedding is unavailable:
exact/path/lexical/graph retrieval may continue

but explicitly record:
semantic_unavailable

Missing semantic evidence must not artificially increase retrieval confidence.
Doctor
Add/complete one concise embedding-contract doctor check covering:
provider reachable
model identity
dimension
context known
truncate:false behavior
index fingerprint compatibility
small valid embedding probe

Do not grow another large doctor subsystem.
PHASE 4 — Embedding verification and branch closure
Run:
focused embedding tests
required mutation tests
affected retrieval/indexing tests
full pytest once
ruff
pylint
doctor --production

Expected:
0 failures
0 unexplained skips
no new ResourceWarning category
production_ready=true

Perform only the minimum live embedding checks needed to prove:
truncate:false refusal works
real vector dimensions are observed
query failure never becomes zero-vector retrieval
fingerprint mismatch is refused/degraded correctly

Do not build a large embedding benchmark here.
Commit the implementation as a coherent embedding-contract batch.
Push the development branch.
PHASE 5 — Merge to main/head
If all Phase 4 gates are green:
merge the development branch into the project's main/default branch.
Resolve only mechanical/documentation conflicts automatically.
Stop on a semantic conflict.
After merge run:
focused merge smoke
full pytest once if merge changed executable content
ruff
pylint
doctor --production

If green, push main/default.
This is the post-embedding integration milestone requested by the owner.
PHASE 6 — Start the new Code Intelligence branch
Create from the freshly merged main:
feature/code-intelligence-r1

This work is not shadow mode and not an isolated proof-of-concept.
It must integrate incrementally into the real production path.
Before coding, rebaseline the old E01–E17 findings against current HEAD.
Do not assume findings from the September snapshot still exist.
Produce a simple table:
finding
current status
evidence
action

Then implement the first integrated Code Intelligence vertical slice described below.
PHASE 7 — CODE INTELLIGENCE VERTICAL SLICE 1
Goal:
Give Kriya a trustworthy answer to “where is the code?” without recreating another huge subsystem.

Create/complete:
kriya/code_intel/

behind one small service interface.
Prefer something conceptually like:
CodeIntelligenceService
  refresh(...)
  find_symbol(...)
  find_references(...)
  locate(...)
  get_member(...)
  build_context(...)

Adapt names to the repository.
Do not expose storage/parser details to workflow code.
7.1 Java + Python structural model
Use tree-sitter for structural truth.
Support initially:
Java
Python

Extract:
qualified symbol id
kind
file
declaration span
signature span
body span
parent
modifiers/annotations where relevant
imports

Java must correctly handle at least:
constructors
package-private methods
final classes
records
enums
generics
multiline declarations
overloaded members

Python must include:
async def
nested/class methods
module-qualified identity

Do not implement Ruby in this slice.
7.2 BaselineIndex + CandidateOverlay
Do not destructively rebuild a single global truth while a candidate is being edited.
Implement:
immutable/current baseline index
+
candidate overlay for changed/deleted/added files

Every lookup must know which revision it represents.
Bind structure to raw source digests.
This is important for mutation authority later.
7.3 Deterministic localization first
Integrate these signals into the real generation path:
explicit path
exact qualified symbol
simple symbol
compiler file:line
stack-trace frame
exact string/error message
lexical identifier match

Embeddings are an additional candidate channel, not the source of truth.
Do not add an LLM localization call when deterministic evidence already produces a clear target.
Use the LLM only for ambiguity resolution when necessary.
7.4 Member-level context
For an identified target, provide:
exact target member body
enclosing type/header
relevant fields/annotations
directly relevant collaborator signatures
directly relevant tests/config when deterministically linked

The target member must not be dropped merely because its source file is huge.
Preserve the existing rule:
Skeleton/localization tells Kriya where; exact authoritative current source tells the model what bytes it may edit.

The actual mutation input must be captured from the exact current candidate source with revision/digest binding.
7.5 Production integration
Wire this slice into at least:
initial brownfield localization
Developer context for known targets
compiler-error retry localization
stack-trace retry localization
investigation tools

Do not run it only in shadow.
Keep the old retrieval path as fallback until the new slice is proven.
7.6 Measurement
Use a small benchmark, not a 150-task program.
Build about 15–25 high-value localization cases using:
commons-lang
Spring Petclinic
one Spring XML project
Kriya itself
one Python project

Measure:
target member recall@5
gold target body in Developer context
context relevance
retrieval latency
incremental refresh latency

Initial targets:
≥90% target recall@5
≥90% target body present
no whole-file loss of target member
retrieval <2s on large repository after warm index

Do not block implementation on building a large golden corpus.
PHASE 8 — Simplification while touching the path
Do not perform a separate “big cleanup project.”
While replacing capability, remove obsolete code only where the new implementation has proven replacement coverage.
Priorities:
duplicate regex symbol parsers
duplicate skeleton/member extractors
obsolete graph lookup branches
shadow-only Code Intelligence paths
dead recovery branches superseded by deterministic localization

Keep:
provider contract
file integrity
transactional commit
containment
pure retry policy
run lock
qualification

Freeze rather than expand:
certification/streak machinery
metrics/adjudication
static-analysis governance
Windows abstractions
MCP governance
routing experiments

Do not add new config flags unless absolutely necessary.
Prefer deletion over another compatibility mode.
PHASE 9 — Architectural seams for GUI/plugins
Do not build the GUI yet.
But ensure this Code Intelligence work moves Kriya toward it.
Where practical, expose core operations behind application/service interfaces rather than CLI-only functions.
Preserve/prepare these future extension points:
ProviderAdapter
BuildAdapter
LanguageAdapter
ToolPlugin
KnowledgeSource
CodeIntelligenceService
RunService

Do not build:
plugin marketplace
plugin signing PKI
hot reload
dependency resolver
desktop shell
GUI workflow logic

The future GUI must consume Kriya services, not duplicate workflow code.
PHASE 10 — Code Intelligence slice exit gate
Run:
focused Code Intelligence tests
benchmark
relevant integration tests
full pytest once
ruff
pylint
doctor --production

Require no regression to existing KNOW/LEARN functionality.
If green:
commit and push:
feature/code-intelligence-r1

Do not merge this branch yet.
Stop here and give the owner one consolidated report.
FINAL REPORT — ONE REPORT ONLY
Report:
1. current branch push completed
2. LEARN A/B result
3. embedding findings: closed/already-closed/superseded
4. embedding implementation summary
5. embedding tests/live checks
6. full-suite/lint/doctor before merge
7. merge commit and pushed main identity
8. new Code Intelligence branch
9. current E01–E17 rebaseline
10. Code Intelligence architecture actually implemented
11. production integration points
12. localization benchmark before/after
13. code removed/simplified
14. full-suite/lint/doctor after CI slice
15. remaining blockers
16. branch/commit state

Keep it concise.
Do not produce separate closure dossiers for each subtask.
Do not generate evidence-only commits unless needed for durable test fixtures or release evidence.
