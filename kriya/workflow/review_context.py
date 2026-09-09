"""Budget-aware review-prompt batching, shared between the standalone `kriya review`
CLI command and the generation workflow's own Reviewer stage(s) (kriya/workflow/workflow.py).

Extracted from kriya/cli.py's `review` command (2026-08-15 SME review, stage 6, Finding 2)
so both callers get the same protection: with no size control at all, a file (or file
set) exceeding the model's context window gets silently truncated from the FRONT by the
backend, cutting off every "=== File: ... ===" framing marker along with it - the model
receives an unlabeled fragment of raw code with no indication it's even being asked to
review anything, produces a confused non-review response, and the caller has no signal
anything went wrong. Confirmed live as a real, severe bug for the CLI path; the
workflow.py Reviewer stage(s) had no equivalent protection at all until this fix.
"""
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from kriya.analyzer.analyzer import chunk_file_syntactically
from kriya.analyzer.graph import DependencyGraph
from kriya.analyzer.java_members import JavaMember, extract_java_members


def build_reviewer_verified_evidence(gate_outcomes: List[Dict[str, Any]]) -> str:
    """Real, already-proven evidence Quality Gates established for this
    attempt, formatted for injection into ReviewerAgent's own prompt -
    closes a real, confirmed live incident (2026-08-25, ignite_qpid_protocol,
    a real Ignite+Qpid Java app): with ZERO visibility into what already
    passed, ReviewerAgent's own prompt is just goal text + raw file content
    (see workflow.py's Reviewer call sites) - nothing tells it the code
    ALREADY compiled and ACTUALLY RAN successfully moments earlier in the
    same run. The model confidently fabricated several SPECIFIC, plausible-
    sounding runtime exceptions (an IgniteException "instance already
    started", a NoClassDefFoundError for a management plugin class) for code
    that had, in the very same run, already been proven not to hit them -
    directly contradicting real evidence Kriya already had on hand
    (state.gate_outcomes) but never showed the Reviewer. ReviewerAgent's own
    system_prompt guideline #2 already warns against exactly this class of
    hallucination ("do not claim parameters/dependencies are missing unless
    absolutely certain") - the model simply doesn't reliably follow a prose
    instruction with no grounding evidence behind it, the same "instruction
    alone isn't enough, give it deterministic grounding" lesson this
    codebase has applied to several other agents already (see
    ground_java_entrypoint_in_no_build_file_projects, _self_heal_structured_
    plan_dict).

    Only run_verification and goal_spec_compliance are surfaced (not
    compile/test) - those two are exactly the gates whose real, SPECIFIC
    captured evidence (actual stdout, a deterministic PASS marker, or the
    spec-compliance agent's own real reasoning) can directly refute a
    plausible-sounding but wrong runtime-failure guess; a bare compile/test
    pass is already implied by "Files generated" reaching Review at all and
    adds no comparably falsifiable evidence. Returns "" when neither gate
    ran/passed (e.g. a goal with no runtime-observable behavior) - nothing
    to inject, never a fabricated claim of its own."""
    lines: List[str] = []
    for outcome in gate_outcomes:
        if outcome.get("type") == "run_verification" and outcome.get("success"):
            lines.append(
                "- Runtime verification ACTUALLY RAN the generated application and it PASSED. "
                f"Real captured output:\n{outcome.get('output', '')}"
            )
        elif outcome.get("type") == "goal_spec_compliance" and outcome.get("success"):
            lines.append(f"- Goal spec compliance check PASSED: {outcome.get('output', '')}")
    if not lines:
        return ""
    return (
        "\n=== Already Verified (real evidence, not a claim to take on faith) ===\n"
        "The following already happened for real, moments ago, before you were asked to "
        "review this code - do not contradict it with a hypothetical or speculative runtime "
        "failure (a specific exception you believe would be thrown, a class you believe is "
        "missing, etc.) unless you can point to something in the actual files above that this "
        "verification evidence does not cover.\n" + "\n".join(lines) + "\n\n"
    )


def build_review_batches(files: List[Tuple[str, str]], budget: int) -> Tuple[List[str], List[str]]:
    """Chunks and greedily batches (relpath, content) pairs into review-prompt blobs that
    each fit within `budget` tokens (same context_window * 0.75 convention used throughout
    workflow.py, via the caller's own estimate_tokens() heuristic - not duplicated here to
    avoid an import cycle with kriya.workflow.workflow).

    A single oversized file is truncated in place (kept chunks + an explicit "TRUNCATED"
    marker) rather than ever spanning batches - a file's own review always sees a
    contiguous prefix of itself, never a scattered/reordered view. The common case (a
    handful of small/medium files) produces exactly ONE batch: one combined call, full
    cross-file architectural context for the reviewer. Only degrades to multiple separate,
    independently-reviewed batches (no shared context between them) when the combined
    content genuinely wouldn't fit.

    Returns (batches, truncated_relpaths) - the caller decides how to surface a truncation
    warning (CLI: click.secho; workflow: logger.warning), kept UI-agnostic here.
    """
    from kriya.workflow.workflow import estimate_tokens

    file_blobs: List[Tuple[str, str, int]] = []  # (rel, blob_text, token_estimate)
    truncated_relpaths: List[str] = []
    for rel, content in files:
        chunks = chunk_file_syntactically(content, max_lines=150, overlap=15)
        blob = ""
        for c_idx, chunk_data in enumerate(chunks, 1):
            suffix = f" (Part {c_idx})" if len(chunks) > 1 else ""
            blob += f"\n=== File: {rel}{suffix} ===\n{chunk_data['text']}\n"

        if estimate_tokens(blob) > budget:
            truncated_relpaths.append(rel)
            kept = ""
            for c_idx, chunk_data in enumerate(chunks, 1):
                suffix = f" (Part {c_idx})" if len(chunks) > 1 else ""
                candidate = kept + f"\n=== File: {rel}{suffix} ===\n{chunk_data['text']}\n"
                if estimate_tokens(candidate) > budget:
                    break
                kept = candidate
            blob = kept + f"\n=== File: {rel} - TRUNCATED: remainder omitted, file exceeds the review token budget ===\n"

        file_blobs.append((rel, blob, estimate_tokens(blob)))

    batches: List[str] = []
    current_batch = ""
    current_tokens = 0
    for _rel, blob, tokens in file_blobs:
        if current_batch and current_tokens + tokens > budget:
            batches.append(current_batch)
            current_batch = ""
            current_tokens = 0
        current_batch += blob
        current_tokens += tokens
    if current_batch:
        batches.append(current_batch)

    return batches, truncated_relpaths


# --- A1-P1 (Java Repository-Aware Code Review, 2026-09-09): bounded
# repository context for a single-Java-file `kriya review` target. ---
#
# Deliberately reuses, rather than re-implements, DependencyGraph
# (kriya/analyzer/graph.py) - the SAME per-file parser (_parse_java()) and
# the SAME weighted BFS ranking (get_neighborhood(), the existing
# relation-type/hop-distance scoring already used by the generation
# pipeline's own Graph RAG retrieval) that this codebase already has, run
# against a throwaway ":memory:" graph populated with only a small,
# deterministically-selected candidate set - never the whole repository,
# and never persisted to the real on-disk index databases (a `kriya
# review` invocation must not have side effects on paths.memory either).

_IGNORE_DIRS = {".git", ".kriya", "target", "build", "dist", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache"}


@dataclass(frozen=True)
class RelatedFile:
    relpath: str
    relation: str  # "implements" | "collaborator" | "test" | "neighbor"
    detail: str


@dataclass(frozen=True)
class ReviewRepositoryContext:
    target_type: str
    class_annotations: Tuple[str, ...] = ()
    implements: Tuple[str, ...] = ()
    extends: Optional[str] = None
    files_scanned: int = 0
    related_files: Tuple[RelatedFile, ...] = field(default_factory=tuple)


def _class_level_annotations(content: str, type_name: str) -> Tuple[str, ...]:
    """Deterministic, bounded: walks backward from the FIRST
    class/interface/enum/record declaration line naming `type_name`,
    collecting consecutive own-line `@Annotation` lines immediately above
    it - the same convention `java_members.py`'s own `_annotations_for()`
    already applies to members, extended here to the enclosing TYPE
    itself (deliberately out of that function's own scope - it is a
    member inventory, not a type-level one)."""
    if not type_name:
        return ()
    lines = content.splitlines()
    type_re = re.compile(r"\b(?:class|interface|enum|record)\s+" + re.escape(type_name) + r"\b")
    decl_idx = next((i for i, line in enumerate(lines) if type_re.search(line)), None)
    if decl_idx is None:
        return ()
    ann_re = re.compile(r"^\s*@([A-Za-z_$][\w$.]*)")
    found: List[str] = []
    idx = decl_idx - 1
    while idx >= 0:
        m = ann_re.match(lines[idx].strip())
        if not m:
            break
        found.insert(0, "@" + m.group(1))
        idx -= 1
    return tuple(found)


def build_review_repository_context(
    workspace_path: str,
    target_relpath: str,
    target_content: str,
    members: Optional[List[JavaMember]] = None,
    max_related_files: int = 8,
    max_scan_files: int = 500,
    max_file_bytes: int = 20480,
) -> ReviewRepositoryContext:
    """Bounded, deterministic repository context for one Java review
    target - NOT the entire repository. Priority order (matches the
    review's own required ordering): (1) the interface `target` declares
    `implements`, if its defining file is found among the bounded
    candidate set; (2) constructor-injected collaborator types (from
    `members`' own constructor parameter types); (3) a test file matching
    the `{Type}Test.java`/`Test{Type}.java` naming convention (the
    DependencyGraph has no "tested_by" relation type - this is the
    smallest deterministic linkage consistent with this codebase's own
    existing naming-convention checks elsewhere, e.g. `is_runnable_test_
    file`, not a fabricated graph edge); (4) remaining `get_neighborhood()`
    hits, in that function's own existing weighted-score order, until
    `max_related_files` is reached.

    Candidate selection itself (which files are even considered) is a
    cheap, bounded, whole-word TEXT pre-filter over `.java` files under
    `workspace_path` (skipping common build/VCS/venv directories, capped
    at `max_scan_files` files and `max_file_bytes` per file) - a file is a
    candidate only if it names the target type, its implemented
    interface, or one of its collaborator types, or matches the test
    naming convention. This keeps the graph actually built here small
    (never full-repository indexing) while still letting
    `get_neighborhood()`'s own real ranking operate on genuinely relevant
    files, not an arbitrary/unranked dump."""
    if members is None:
        members = extract_java_members(target_content)

    graph = DependencyGraph(":memory:")
    try:
        target_symbols, target_relations = graph._parse_java(target_relpath, target_content)
        type_name = next(
            (s["name"] for s in target_symbols if s.get("type") in ("class", "interface")), "",
        )
        implements = tuple(sorted({
            r["target"] for r in target_relations
            if r["type"] == "implements" and r["source"] == type_name
        }))
        extends_hits = [
            r["target"] for r in target_relations
            if r["type"] == "extends" and r["source"] == type_name
        ]
        extends = extends_hits[0] if extends_hits else None
        # `type_name` is the FQN the graph itself is keyed by (needed for
        # implements/extends relation lookups and get_neighborhood seeds
        # below); source-text matching (annotations above the declaration,
        # {Type}Test.java naming) needs the bare simple name instead, since
        # that's what actually appears in Java source/filenames.
        simple_type_name = type_name.rsplit(".", 1)[-1] if type_name else ""
        class_annotations = _class_level_annotations(target_content, simple_type_name)

        collaborator_types = sorted({
            pt for mm in members if mm.kind == "constructor" for pt in mm.parameter_types
            if pt and pt[:1].isupper()  # a plausible class/interface type, not a primitive
        })
        search_terms = {
            t for t in ([type_name, simple_type_name] + list(implements) + collaborator_types) if t
        }
        test_names = (
            {f"{simple_type_name}Test.java", f"Test{simple_type_name}.java"}
            if simple_type_name else set()
        )

        candidates: List[Tuple[str, str]] = []
        scanned = 0
        term_res = {t: re.compile(rf"(?<!\w){re.escape(t)}(?!\w)") for t in search_terms}
        for root, dirs, filenames in os.walk(workspace_path):
            dirs[:] = [d for d in dirs if d not in _IGNORE_DIRS and not d.startswith(".")]
            for fname in filenames:
                if not fname.endswith(".java") or scanned >= max_scan_files:
                    continue
                full = os.path.join(root, fname)
                rel = os.path.relpath(full, workspace_path)
                if rel == target_relpath:
                    continue
                scanned += 1
                try:
                    if os.path.getsize(full) > max_file_bytes:
                        continue
                    with open(full, "r", encoding="utf-8", errors="replace") as fh:
                        content = fh.read()
                except OSError:
                    continue
                is_test_name = fname in test_names
                if is_test_name or any(p.search(content) for p in term_res.values()):
                    candidates.append((rel, content))

        graph.index_file(target_relpath, target_content, mtime=0.0)
        for rel, content in candidates:
            graph.index_file(rel, content, mtime=0.0)

        seed_symbols = [s for s in ([type_name] + list(implements) + collaborator_types) if s]
        neighborhood = graph.get_neighborhood(seed_symbols, max_hops=2, max_results=max_related_files * 3)

        related: List[RelatedFile] = []
        seen_files = {target_relpath}

        for iface in implements:
            for rel, content in candidates:
                if rel in seen_files:
                    continue
                if re.search(rf"\binterface\s+{re.escape(iface)}\b", content):
                    related.append(RelatedFile(rel, "implements", f"declares interface {iface} that {type_name} implements"))
                    seen_files.add(rel)
                    break

        for ctype in collaborator_types:
            for rel, content in candidates:
                if rel in seen_files:
                    continue
                if re.search(rf"\b(?:class|interface)\s+{re.escape(ctype)}\b", content):
                    related.append(RelatedFile(rel, "collaborator", f"constructor-injected dependency type {ctype}"))
                    seen_files.add(rel)
                    break

        for rel, content in candidates:
            if rel in seen_files:
                continue
            if os.path.basename(rel) in test_names:
                related.append(RelatedFile(rel, "test", f"test file (naming convention: {os.path.basename(rel)})"))
                seen_files.add(rel)

        for hit in neighborhood:
            if len(related) >= max_related_files:
                break
            rel = hit.get("filepath")
            if not rel or rel in seen_files:
                continue
            related.append(RelatedFile(
                rel, "neighbor",
                f"{hit['relation_type']} relation to {hit['name']} (score={hit['score']:.2f})",
            ))
            seen_files.add(rel)

        return ReviewRepositoryContext(
            target_type=type_name,
            class_annotations=class_annotations,
            implements=implements,
            extends=extends,
            files_scanned=scanned,
            related_files=tuple(related[:max_related_files]),
        )
    finally:
        graph.close()


def format_review_repository_context(ctx: ReviewRepositoryContext) -> str:
    """Turns a ReviewRepositoryContext into the labeled prompt block. []/""
    fields are simply omitted, never fabricated as "none" noise the model
    has to read past."""
    if not ctx.target_type:
        return ""
    lines = ["\n=== Repository Evidence (deterministic, source/graph-derived - not model interpretation) ==="]
    lines.append(f"Target type: {ctx.target_type}")
    if ctx.class_annotations:
        lines.append(f"Class-level annotations: {', '.join(ctx.class_annotations)}")
    if ctx.implements:
        lines.append(f"Implements: {', '.join(ctx.implements)}")
    if ctx.extends:
        lines.append(f"Extends: {ctx.extends}")
    if ctx.related_files:
        lines.append("Related files (deterministic, bounded - not the entire repository):")
        for rf in ctx.related_files:
            lines.append(f"  - [{rf.relation}] {rf.relpath}: {rf.detail}")
    else:
        lines.append("Related files: none found by the bounded deterministic scan.")
    lines.append(
        f"(Scanned {ctx.files_scanned} candidate repository .java file(s); "
        f"{len(ctx.related_files)} included above, ranked deterministically.)\n"
    )
    return "\n".join(lines)


def format_java_member_inventory(members: List[JavaMember]) -> str:
    """Turns a deterministic member list into the labeled prompt block the
    Reviewer must treat as authoritative - it must not be expected to
    rediscover this itself, and must account for every member listed here
    (not invent additional ones, not silently skip one)."""
    if not members:
        return ""
    lines = ["\n=== Deterministic Symbol Inventory (machine-extracted - authoritative; do not invent members not listed here, and address every one) ==="]
    for m in members:
        ann = f" {' '.join(m.annotations)}" if m.annotations else ""
        throws = f" throws {', '.join(m.throws)}" if m.throws else ""
        lines.append(f"  -{ann} {m.signature}{throws}  [lines {m.start_line}-{m.end_line}]")
    lines.append("")
    return "\n".join(lines)


def check_member_coverage(review_text: str, members: List[JavaMember]) -> Dict[str, List[str]]:
    """Best-effort, deterministic, NON-gating coverage check: does the
    Reviewer's own (free-form) output text mention each deterministically-
    extracted member by name at least once? This is deliberately narrow -
    a bare name-presence text scan, not a semantic "was this member
    actually reviewed" judgment, since ReviewerAgent's own output format
    is free-form prose (see this module's own module docstring / the
    A1-P1 handoff for why a real structured-coverage guarantee would need
    a Reviewer output-format change this task does not make). Returns
    {"covered": [...], "not_mentioned": [...]} - a caller (the CLI) can
    print a footer note; nothing here gates success or raises."""
    covered: List[str] = []
    not_mentioned: List[str] = []
    for m in members:
        pattern = re.compile(rf"(?<!\w){re.escape(m.name)}(?!\w)")
        (covered if pattern.search(review_text) else not_mentioned).append(m.name)
    return {"covered": covered, "not_mentioned": not_mentioned}


_JAVA_REPO_ROOT_MARKERS = (".git", "pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle")


def find_java_repo_root(java_file_path: str, max_levels: int = 12) -> str:
    """Walks upward from a single Java file's own directory looking for a
    repository/build-root marker (.git, pom.xml, a Gradle build/settings
    file), bounded at `max_levels` - this is the workspace root
    `build_review_repository_context()`'s bounded scan uses, not an
    invitation to index arbitrary ancestor directories. Falls back to the
    file's own containing directory (still a valid, if narrower, workspace)
    if no marker is found within the bound - never raises, never walks
    past the filesystem root."""
    current = os.path.dirname(os.path.abspath(java_file_path))
    fallback = current
    for _ in range(max_levels):
        if any(os.path.exists(os.path.join(current, marker)) for marker in _JAVA_REPO_ROOT_MARKERS):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return fallback


# =====================================================================
# A1-E2: Deterministic Review Evidence Adjudication
#
# ReviewerAgent may REQUEST a confidence level (PROVEN_ISSUE /
# STRONG_STATIC_INDICATION / REQUIRES_PROFILING_OR_RUNTIME_EVIDENCE) for a
# finding, but is never authoritative for the FINAL confidence - Kriya
# computes that deterministically from whether the model's own cited
# evidence references actually resolve to evidence Kriya supplied for this
# exact call. This directly targets the gap two live A1 runs demonstrated:
# a model can correctly spot a real static pattern (condition) while
# asserting a stronger practical consequence than any supplied evidence
# establishes - self-invocation in run 1 ("risking uncommitted state"),
# delete()'s implicit persistence in run 2 ("will not be persisted") - and
# both times self-assigned PROVEN ISSUE for it. This mechanism is
# deliberately pattern-agnostic: it never inspects what a finding is
# ABOUT, only whether the model backed its own requested confidence with
# evidence IDs Kriya actually gave it. No Spring/JPA/framework-specific
# rule exists anywhere below.
#
# IDs are Kriya-generated, never derived from model output (see
# `kriya/workflow/attempt.py::_spec_requirements_contradicting_authority`'s
# own docstring for why fuzzy-matching a model's own free-text claims back
# onto ground truth is the wrong pattern in this codebase) - the model can
# only ever CITE an ID Kriya already assigned and displayed to it; an ID it
# invents cannot, by construction, appear in the registry, so "invalid
# reference" and "fabricated reference" are the same code path, not two.
# =====================================================================

CONFIDENCE_PROVEN_ISSUE = "PROVEN_ISSUE"
CONFIDENCE_STRONG_STATIC_INDICATION = "STRONG_STATIC_INDICATION"
CONFIDENCE_REQUIRES_RUNTIME = "REQUIRES_PROFILING_OR_RUNTIME_EVIDENCE"
CONFIDENCE_EVIDENCE_INSUFFICIENT = "EVIDENCE_INSUFFICIENT"  # internal-only; never a valid model request

_VALID_REQUESTED_CONFIDENCES = frozenset({
    CONFIDENCE_PROVEN_ISSUE, CONFIDENCE_STRONG_STATIC_INDICATION, CONFIDENCE_REQUIRES_RUNTIME,
})

# User-facing labels (spaces, matching the pre-existing A1-P1/A1-R1 vocabulary
# reviewers/users already saw) for the three real report-facing tiers - the
# internal EVIDENCE_INSUFFICIENT state never reaches the user under that name,
# see format_adjudicated_review()'s own rendering.
_CONFIDENCE_DISPLAY_LABELS = {
    CONFIDENCE_PROVEN_ISSUE: "PROVEN ISSUE",
    CONFIDENCE_STRONG_STATIC_INDICATION: "STRONG STATIC INDICATION",
    CONFIDENCE_REQUIRES_RUNTIME: "REQUIRES PROFILING OR RUNTIME EVIDENCE",
}


def build_member_evidence_ids(members: Sequence[JavaMember]) -> Dict[str, JavaMember]:
    """Kriya-generated, deterministic M1..Mn ids in the members' own
    existing order (already sorted by start_line by extract_java_members).
    Same ordered input always produces the same ids - no randomness, no
    dependency on anything the model says."""
    return {f"M{i}": m for i, m in enumerate(members, 1)}


def build_relation_evidence_ids(related_files: Sequence[RelatedFile]) -> Dict[str, RelatedFile]:
    """Kriya-generated, deterministic R1..Rn ids in the related files' own
    existing priority order (already ranked by build_review_repository_context)."""
    return {f"R{i}": rf for i, rf in enumerate(related_files, 1)}


def format_member_evidence_registry(member_ids: Dict[str, JavaMember]) -> str:
    """The ID-annotated Deterministic Symbol Inventory shown to the model in
    structured-review mode - the human-readable signature stays present,
    the leading id is the only thing a finding may cite as a CONDITION/
    CONSEQUENCE evidence reference."""
    lines = [
        "\n=== Deterministic Symbol Inventory (machine-extracted - authoritative; "
        "cite members ONLY by the id shown, e.g. M1 - do not invent members or ids "
        "not listed here, and address every one) ==="
    ]
    for mid, m in member_ids.items():
        ann = f" {' '.join(m.annotations)}" if m.annotations else ""
        throws = f" throws {', '.join(m.throws)}" if m.throws else ""
        lines.append(f"  - {mid} |{ann} {m.signature}{throws}  [lines {m.start_line}-{m.end_line}]")
    lines.append("")
    return "\n".join(lines)


def format_relation_evidence_registry(ctx: "ReviewRepositoryContext", relation_ids: Dict[str, RelatedFile]) -> str:
    """The ID-annotated Repository Evidence shown to the model in
    structured-review mode. An id proves ONLY the relation/detail text
    printed next to it - never that related file's unseen contents (the
    A1-R1 evidence-boundary rule, preserved here at the data-supply layer
    too, not just in prompt wording)."""
    lines = ["\n=== Repository Evidence (deterministic, source/graph-derived - not model interpretation) ==="]
    if ctx.target_type:
        lines.append(f"Target type: {ctx.target_type}")
    if ctx.class_annotations:
        lines.append(f"Class-level annotations: {' '.join(ctx.class_annotations)}")
    if ctx.implements:
        lines.append(f"Implements: {', '.join(ctx.implements)}")
    if ctx.extends:
        lines.append(f"Extends: {ctx.extends}")
    lines.append(
        "Related artifacts (cite ONLY by the id shown, e.g. R1 - an id proves only its own "
        "relation/detail text below, never that file's unseen contents):"
    )
    if not relation_ids:
        lines.append("  (none found by the bounded deterministic scan)")
    for rid, rf in relation_ids.items():
        lines.append(f"  - {rid} | [{rf.relation}] {rf.relpath}: {rf.detail}")
    lines.append(f"(Scanned {ctx.files_scanned} candidate repository .java file(s); {len(relation_ids)} included above, ranked deterministically.)")
    lines.append("")
    return "\n".join(lines)


@dataclass(frozen=True)
class StructuredFinding:
    """One finding as the model reported it - REQUESTED, not final,
    confidence. Parsed defensively from the model's raw JSON; every field
    has a safe default so a partially-malformed single finding degrades to
    EVIDENCE_INSUFFICIENT rather than crashing the whole response."""
    finding_id: str
    title: str
    member_id: Optional[str]
    requested_confidence: str
    condition_evidence_ids: Tuple[str, ...] = ()
    consequence_evidence_ids: Tuple[str, ...] = ()
    runtime_dependency_declared: bool = False
    explanation: str = ""
    recommendation: Optional[str] = None


@dataclass(frozen=True)
class AdjudicatedFinding:
    """Kriya's own final verdict for one finding - the only thing the
    formatter is allowed to render as the finding's confidence."""
    finding: StructuredFinding
    final_confidence: str
    downgrade_reason: Optional[str]
    invalid_condition_ids: Tuple[str, ...]
    invalid_consequence_ids: Tuple[str, ...]


def parse_structured_findings(raw: Any) -> List[StructuredFinding]:
    """Defensive parse of the model's own `findings` JSON array into
    StructuredFinding records - never raises; a malformed individual entry
    is simply skipped (matches this codebase's own established `.get()`/
    isinstance-guarded parsing convention for json_mode agent responses,
    e.g. SpecComplianceAgent.check()), a malformed top-level shape yields
    an empty list rather than crashing the whole review."""
    if not isinstance(raw, list):
        return []
    out: List[StructuredFinding] = []
    for i, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            continue
        finding_id = str(item.get("finding_id") or f"F{i}")
        title = str(item.get("title") or "")
        member_id = item.get("member_id")
        member_id = str(member_id) if member_id else None
        requested = str(item.get("requested_confidence") or "").strip().upper().replace(" ", "_").replace("-", "_")
        cond_ids = tuple(str(x) for x in (item.get("condition_evidence_ids") or []) if x)
        cons_ids = tuple(str(x) for x in (item.get("consequence_evidence_ids") or []) if x)
        runtime_dep = bool(item.get("runtime_dependency_declared", False))
        explanation = str(item.get("explanation") or "")
        recommendation = item.get("recommendation")
        recommendation = str(recommendation) if recommendation else None
        out.append(StructuredFinding(
            finding_id=finding_id, title=title, member_id=member_id,
            requested_confidence=requested, condition_evidence_ids=cond_ids,
            consequence_evidence_ids=cons_ids, runtime_dependency_declared=runtime_dep,
            explanation=explanation, recommendation=recommendation,
        ))
    return out


def adjudicate_findings(
    findings: Sequence[StructuredFinding],
    member_ids: Dict[str, JavaMember],
    relation_ids: Dict[str, RelatedFile],
) -> List[AdjudicatedFinding]:
    """The evidence-authority gate itself. Deliberately NOT asserting "the
    consequence evidence logically proves the consequence" - that would be
    a technical-correctness engine, explicitly out of scope. It enforces
    only: a finding cannot claim PROVEN_ISSUE without citing, for BOTH
    condition and consequence, at least one id that actually resolves to
    evidence Kriya supplied for this exact call. An unknown/invented id is
    never treated as satisfying that requirement - it simply isn't in
    `known_ids`, so it can't count, by construction (no special-cased
    "fabrication detected" branch is needed).

    Precedence when a finding requests PROVEN_ISSUE: a declared runtime
    dependency is checked BEFORE evidence completeness - the model's own
    honest admission that the consequence depends on runtime information
    overrides everything else, since no amount of supplied static evidence
    could make that consequence PROVEN anyway."""
    known_ids = set(member_ids) | set(relation_ids)
    results: List[AdjudicatedFinding] = []
    for f in findings:
        invalid_condition_ids = tuple(i for i in f.condition_evidence_ids if i not in known_ids)
        invalid_consequence_ids = tuple(i for i in f.consequence_evidence_ids if i not in known_ids)
        valid_condition = any(i in known_ids for i in f.condition_evidence_ids)
        valid_consequence = any(i in known_ids for i in f.consequence_evidence_ids)

        final_confidence: str
        reason: Optional[str] = None

        if f.requested_confidence == CONFIDENCE_PROVEN_ISSUE:
            if f.runtime_dependency_declared:
                final_confidence = CONFIDENCE_REQUIRES_RUNTIME
                reason = "runtime dependency declared - a static review cannot establish a runtime-dependent consequence as proven"
            elif valid_condition and valid_consequence:
                final_confidence = CONFIDENCE_PROVEN_ISSUE
            elif valid_condition:
                final_confidence = CONFIDENCE_STRONG_STATIC_INDICATION
                reason = "no consequence evidence reference resolved to evidence supplied for this review - the condition alone does not establish the claimed consequence"
            else:
                final_confidence = CONFIDENCE_EVIDENCE_INSUFFICIENT
                reason = "no condition evidence reference resolved to evidence supplied for this review"
        elif f.requested_confidence == CONFIDENCE_STRONG_STATIC_INDICATION:
            if valid_condition:
                final_confidence = CONFIDENCE_STRONG_STATIC_INDICATION
            else:
                final_confidence = CONFIDENCE_EVIDENCE_INSUFFICIENT
                reason = "no condition evidence reference resolved to evidence supplied for this review"
        elif f.requested_confidence == CONFIDENCE_REQUIRES_RUNTIME:
            final_confidence = CONFIDENCE_REQUIRES_RUNTIME
        else:
            final_confidence = CONFIDENCE_EVIDENCE_INSUFFICIENT
            reason = f"unrecognized requested confidence {f.requested_confidence!r}"

        results.append(AdjudicatedFinding(
            finding=f, final_confidence=final_confidence, downgrade_reason=reason,
            invalid_condition_ids=invalid_condition_ids, invalid_consequence_ids=invalid_consequence_ids,
        ))
    return results


def compute_member_coverage(member_ids: Dict[str, JavaMember], returned_member_review_ids: Sequence[str]) -> Dict[str, List[str]]:
    """Deterministic id-set comparison (exact, not name-presence text
    scanning like the older, never-wired check_member_coverage() above) -
    prefer this for the structured A1 path per explicit instruction.
    Returns {"missing": [...], "invented": [...]} - "missing" ids are in
    the supplied inventory but never accounted for in the model's own
    member_reviews; "invented" ids were returned but never actually
    supplied (a fabricated member id, structurally impossible to be a real
    member since ids only ever come from Kriya's own registry)."""
    expected = set(member_ids)
    returned = {str(i) for i in returned_member_review_ids if i}
    missing = sorted(expected - returned, key=lambda mid: int(mid[1:]))
    invented = sorted(returned - expected)
    return {"missing": missing, "invented": invented}


def _validate_run_guidance_statement_ids(evidence_ids: Sequence[str], known_ids: set) -> Tuple[str, ...]:
    return tuple(i for i in evidence_ids if i in known_ids)


def parse_and_validate_run_guidance(raw: Any, member_ids: Dict[str, JavaMember], relation_ids: Dict[str, RelatedFile]) -> Tuple[List[Tuple[str, Tuple[str, ...]]], List[str]]:
    """Parses the model's own `run_guidance` object into (statements,
    gaps). Each statement is (text, valid_evidence_ids) - an evidence id
    that doesn't resolve is silently dropped from that statement (same
    "invalid reference never counts as authority" rule the finding
    adjudicator applies), never used to lend the statement unearned
    authority. A statement with NO evidence ids at all is still rendered -
    it is understood to be grounded directly in the always-fully-visible
    TARGET SOURCE (which needs no id, unlike a related artifact's unseen
    contents), not a free pass to assert unrelated facts; this function
    does not and cannot judge that, it only enforces the id-resolution
    boundary, exactly like adjudicate_findings()."""
    known_ids = set(member_ids) | set(relation_ids)
    if not isinstance(raw, dict):
        return [], []
    statements: List[Tuple[str, Tuple[str, ...]]] = []
    raw_statements = raw.get("statements")
    if isinstance(raw_statements, list):
        for item in raw_statements:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            ids = tuple(str(x) for x in (item.get("evidence_ids") or []) if x)
            valid_ids = _validate_run_guidance_statement_ids(ids, known_ids)
            statements.append((text, valid_ids))
    gaps_raw = raw.get("not_determinable")
    gaps = [str(g) for g in gaps_raw if g] if isinstance(gaps_raw, list) else []
    return statements, gaps


def format_adjudicated_review(
    summary: str,
    member_ids: Dict[str, JavaMember],
    member_review_ids: Sequence[str],
    coverage: Dict[str, List[str]],
    adjudicated: Sequence[AdjudicatedFinding],
    recommendations: Sequence[str],
    run_guidance_statements: Sequence[Tuple[str, Tuple[str, ...]]],
    run_guidance_gaps: Sequence[str],
) -> str:
    """Renders Kriya's own adjudicated structured result into the same
    readable Markdown report shape `kriya review` has always produced -
    the user never sees raw JSON, and every rendered fact traces back to
    the validated structured result, never straight from the model's own
    unvalidated prose. Transparent about downgrades on purpose: when Kriya
    changes a requested confidence, the report says so explicitly rather
    than silently substituting its own verdict."""
    lines = ["## Review Status: Reviewed (Kriya-adjudicated evidence confidence)", ""]
    if summary:
        lines += ["### Overview", summary.strip(), ""]

    lines += ["### Member Coverage", ""]
    accounted = len(set(member_review_ids) & set(member_ids))
    lines.append(f"{accounted}/{len(member_ids)} deterministic member(s) accounted for.")
    if coverage["missing"]:
        lines.append(f"**Missing (never addressed by the review):** {', '.join(coverage['missing'])}")
    if coverage["invented"]:
        lines.append(f"**Invented (not part of the supplied inventory - ignored):** {', '.join(coverage['invented'])}")
    lines.append("")

    lines += ["### Findings", ""]
    if not adjudicated:
        lines.append("No findings reported.")
    for af in adjudicated:
        f = af.finding
        member_note = f" ({f.member_id})" if f.member_id and f.member_id in member_ids else ""
        display_confidence = _CONFIDENCE_DISPLAY_LABELS.get(
            af.final_confidence, "EVIDENCE INSUFFICIENT - not enough supplied evidence to support any confidence tier",
        )
        lines.append(f"#### {f.title}{member_note}")
        lines.append(f"**{display_confidence}**")
        requested_label = _CONFIDENCE_DISPLAY_LABELS.get(f.requested_confidence, f.requested_confidence)
        if af.final_confidence != f.requested_confidence:
            lines.append(f"*(Reviewer requested: {requested_label}; Kriya downgraded: {af.downgrade_reason})*")
        if f.explanation:
            lines.append(f.explanation.strip())
        if af.invalid_condition_ids or af.invalid_consequence_ids:
            invalid_all = sorted(set(af.invalid_condition_ids) | set(af.invalid_consequence_ids))
            lines.append(f"*(Unresolvable evidence reference(s) ignored: {', '.join(invalid_all)})*")
        if f.recommendation:
            lines.append(f"Recommendation: {f.recommendation.strip()}")
        lines.append("")

    if recommendations:
        lines += ["### Recommendations", ""]
        for r in recommendations:
            lines.append(f"- {r}")
        lines.append("")

    lines += ["## How to Run the Application", ""]
    for text, ids in run_guidance_statements:
        suffix = f" (evidence: {', '.join(ids)})" if ids else ""
        lines.append(f"- {text}{suffix}")
    if run_guidance_gaps:
        lines.append("")
        lines.append("Not determinable from the supplied repository evidence: " + ", ".join(run_guidance_gaps) + ".")
    if not run_guidance_statements and not run_guidance_gaps:
        lines.append("No run guidance was returned.")

    return "\n".join(lines)


def build_structured_review_report(
    raw_response: Dict[str, Any],
    member_ids: Dict[str, JavaMember],
    relation_ids: Dict[str, RelatedFile],
) -> str:
    """Orchestrates the whole structured-response -> adjudicated Markdown
    pipeline for the A1 single-Java-file review path. `raw_response` is
    already-parsed JSON (a dict) - a total call/parse failure is the
    caller's (ReviewerAgent.run_structured_review()) responsibility to
    surface as a clear error BEFORE reaching here; this function only
    handles a successfully-parsed-but-possibly-incomplete response,
    degrading missing/malformed pieces to empty rather than raising."""
    summary = str(raw_response.get("summary") or "")
    findings = parse_structured_findings(raw_response.get("findings"))
    adjudicated = adjudicate_findings(findings, member_ids, relation_ids)

    member_reviews = raw_response.get("member_reviews")
    member_review_ids = [
        str(item.get("member_id")) for item in member_reviews
        if isinstance(item, dict) and item.get("member_id")
    ] if isinstance(member_reviews, list) else []
    coverage = compute_member_coverage(member_ids, member_review_ids)

    recommendations_raw = raw_response.get("recommendations")
    recommendations = [str(r) for r in recommendations_raw if r] if isinstance(recommendations_raw, list) else []

    run_guidance_statements, run_guidance_gaps = parse_and_validate_run_guidance(
        raw_response.get("run_guidance"), member_ids, relation_ids,
    )

    return format_adjudicated_review(
        summary=summary, member_ids=member_ids, member_review_ids=member_review_ids,
        coverage=coverage, adjudicated=adjudicated, recommendations=recommendations,
        run_guidance_statements=run_guidance_statements, run_guidance_gaps=run_guidance_gaps,
    )
