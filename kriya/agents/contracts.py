"""Structured-output contracts for agent stages whose result is consumed
programmatically downstream, not just read as prose by a human/the Reviewer.
Deliberately narrow: only fields something else actually parses belong here -
free-text rationale/design reasoning stays plain text in the agent's own
response and is never forced through a schema.

First contract, shared by two call sites: a validated list of file paths.
ArchitectAgent.run_with_file_list() uses it for the design's authoritative
file list - the one part of a design IncompleteGenerationError's completeness
check and the Developer's upfront "required files" prompt block both depend
on being accurate, previously recovered by extract_expected_files() (a
blanket regex over the ENTIRE design's prose matching any "word.ext"-shaped
token, with no way to distinguish a real requirement from an incidental
mention like "similar to Foo.java elsewhere in the codebase"). Generalized
2026-08-07 to DeveloperAgent.run_generation()'s own Step 1 (the "which files
do I need to write" query, structurally the identical problem - previously
_extract_json_value()+_normalize_file_entries()'s hand-rolled parsing, no
schema at all) - see kriya/workflow/workflow.py and kriya/agents/agent.py for
both call sites' own older, heuristic fallbacks, kept as this module's
fallback of last resort in each, not removed, since local models don't get
schema-constrained decoding from LLMClient today (kriya/core/llm.py's
json_mode only guarantees SOME valid JSON, not any particular shape) and a
malformed response must degrade, never crash the run.
"""
import json
import logging
import os
import re
from enum import Enum
from typing import List, Optional, Tuple

from pydantic import BaseModel, Field, ValidationError, field_validator

from kriya.workflow.triage import ChangeKind

logger = logging.getLogger(__name__)


class FileList(BaseModel):
    """A validated list of workspace-relative file paths - the authoritative
    set of files a design calls for (ArchitectAgent), or the set a Developer
    completion says it needs to create/modify (DeveloperAgent's Step 1)."""

    files: List[str]

    @field_validator("files")
    @classmethod
    def _non_empty(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("files list must not be empty - a generation run always touches at least one file")
        return v

    @field_validator("files")
    @classmethod
    def _sane_workspace_relative_paths(cls, v: List[str]) -> List[str]:
        cleaned = []
        for raw in v:
            path = (raw or "").strip()
            if not path:
                raise ValueError("file path must not be blank")
            if path.startswith("/") or path.startswith("\\") or re.match(r"^[A-Za-z]:[\\/]", path):
                raise ValueError(f"file path must be workspace-relative, not absolute: {path!r}")
            if ".." in path.replace("\\", "/").split("/"):
                raise ValueError(f"file path must not contain '..' path-traversal segments: {path!r}")
            cleaned.append(path)
        return cleaned


# Takes the LAST fenced ```json ... ``` (or bare ```...```) block in the text
# specifically - a design occasionally includes a smaller illustrative JSON
# snippet earlier (e.g. a sample config payload) before the real file-list
# block, which is always meant to be the final thing in the response per
# ArchitectAgent's own system prompt.
_FENCED_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
# Last-resort match for a response that emits the JSON object without ever
# wrapping it in a fence at all (DeveloperAgent's Step 1 is asked to return
# exactly this shape, no fence) - anchored on the "files" key specifically so
# it doesn't accidentally grab an unrelated brace-delimited snippet.
_BARE_FILES_OBJECT = re.compile(r'\{[^{}]*"files"\s*:\s*\[[^\]]*\][^{}]*\}', re.DOTALL)


# Deliberately scoped to .java ONLY, not every compiled/interpreted source
# extension - Maven/Gradle's src/main/java/ is a rigid, essentially
# universal single-source-root convention that real Java projects don't
# deviate from for main-scope source, which is the whole premise
# _normalize_file_list_paths below relies on. Python/Ruby do NOT have an
# equivalent rigid convention - a root-level entrypoint script alongside a
# lib/package subdirectory (e.g. "cli.py" + "tasks/store.py") is completely
# idiomatic, not a bug, so inferring a shared directory for those languages
# would be a real, harmful false positive. Confirmed as a real, caught-
# before-shipping false positive while testing this fix: an early ".py"-
# inclusive version wrongly rewrote exactly that shape in an EXISTING test
# fixture (test_ignores_trailing_chatty_prose_after_the_json_block) from
# "cli.py" to "tasks/cli.py" - a legitimate root-level script, not a
# mistake. A second false positive, also caught before shipping: grouping by
# raw extension alone (not source-vs-config) "corrected" a real incident's
# own "pom.xml" (correctly bare - pom.xml is always project-root) into
# "src/main/resources/pom.xml", purely because it shares the ".xml"
# extension with an unrelated "ignite-config.xml" resource file in the same
# list - config/resource extensions are excluded for the same reason.
_SOURCE_EXTENSIONS = {".java"}


def _normalize_file_list_paths(files: List[str]) -> List[str]:
    """Corrects a single bare-basename SOURCE-code entry (no directory
    component) sitting among otherwise-consistently-pathed source siblings
    of the same extension in the same file list - found live, 2026-08-16
    (ignite_qpid_person, run b-8): the Architect's own JSON file list was
    '{"files": ["pom.xml", "src/main/java/Person.java", ...,
    "PersonApp.java"]}' - every OTHER file correctly pathed, but the
    entrypoint (the one file that actually matters most, since it's what
    pom.xml's exec plugin targets) came back bare. Nothing downstream ever
    re-checks a file list's own internal path consistency - FileList's
    validators only check per-path syntactic sanity (not blank, not
    absolute, no '..'), so this passed validation cleanly and was written
    literally as given: straight to the WORKSPACE ROOT, not
    src/main/java/PersonApp.java. Maven only ever compiles src/main/java/**,
    so the file was silently outside the build from attempt 1 onward -
    "Could not find or load main class PersonApp" recurred identically
    across 7 attempts and 2 models, including two DIAGNOSIS MISMATCH catches,
    because every attempt was retrying CONTENT at a path Maven structurally
    never looks at; no content-level fix could ever have resolved it.

    Deliberately narrow and conservative - only corrects when the inference
    is unambiguous: scoped to _SOURCE_EXTENSIONS only (see that constant's
    own comment for why - config/resource extensions are excluded entirely,
    not just handled cautiously). For each source extension appearing more
    than once in the list, if exactly one file of that extension has an
    empty directory component AND every OTHER file of that same extension
    shares the IDENTICAL non-empty directory, the bare file is rewritten
    into that shared directory. Any other shape (multiple bare files,
    siblings disagreeing on directory, only one source file total of that
    extension) is left untouched - there's no safe, ungrounded prefix to
    invent, and guessing wrong would be worse than leaving the original bare
    path for the existing IncompleteGenerationError/missing-file-recovery
    machinery to eventually surface as a distinct, correctly-attributed
    problem."""
    by_ext: dict = {}
    for f in files:
        ext = os.path.splitext(f)[1]
        if ext in _SOURCE_EXTENSIONS:
            by_ext.setdefault(ext, []).append(f)

    corrections = {}
    for ext, group in by_ext.items():
        if len(group) < 2:
            continue
        bare = [f for f in group if not os.path.dirname(f)]
        if len(bare) != 1:
            continue
        dirs = {os.path.dirname(f) for f in group if f != bare[0]}
        if len(dirs) != 1:
            continue
        shared_dir = next(iter(dirs))
        if not shared_dir:
            continue
        corrections[bare[0]] = f"{shared_dir}/{bare[0]}"

    if not corrections:
        return files

    for original, corrected in corrections.items():
        logger.info(
            f"Architect/Developer file list: '{original}' had no directory component while its "
            f"'{os.path.splitext(original)[1]}' source siblings all agree on one - normalizing to "
            f"'{corrected}' (an unambiguous, evidence-backed correction, not a guess: see "
            "_normalize_file_list_paths's own docstring for the live incident this closes)."
        )
    return [corrections.get(f, f) for f in files]


class Milestone(BaseModel):
    """One vertical-slice step of a larger goal, produced by
    MilestonePlannerAgent (kriya/agents/agent.py) - see kriya/workflow/milestones.py
    for the orchestrator that consumes this. `goal` must describe a small,
    independently EXECUTABLE and VERIFIABLE slice of real behavior (never
    "write these classes"), and `success_criterion` is the plain-language
    checkable outcome that gets folded into that milestone's own goal text so
    the EXISTING, already-unconditional verification-contract mechanism
    (kriya/workflow/retry_prompts.py's VERIFICATION_CONTRACT_HEADER) fires for
    it with zero further code changes."""

    goal: str
    success_criterion: str
    depends_on_previous: bool = True

    @field_validator("goal", "success_criterion")
    @classmethod
    def _non_blank(cls, v: str) -> str:
        if not (v or "").strip():
            raise ValueError("must not be blank")
        return v


class MilestoneList(BaseModel):
    """A validated, ordered decomposition of one goal into small milestones."""

    milestones: List[Milestone]

    @field_validator("milestones")
    @classmethod
    def _non_empty_and_bounded(cls, v: List[Milestone]) -> List[Milestone]:
        if not v:
            raise ValueError("milestones list must not be empty")
        # Conservative sanity cap, not evidence-based yet - a goal genuinely
        # needing more than 8 vertical slices is rare enough that hitting
        # this should surface as something for a human to look at, not
        # silently proceed. Revisit once a live run produces real data.
        if len(v) > 8:
            raise ValueError(
                f"{len(v)} milestones suggests over-slicing (by code structure, not "
                "behavior) rather than genuine complexity - re-slice into fewer, "
                "larger vertical-slice milestones"
            )
        return v


class MilestoneMode(str, Enum):
    """How a milestone relates to what earlier milestones already built - see
    kriya/workflow/milestone_validation.py (MA3.3+) for the semantics this
    drives: EXTENSION means "evolves the same application" (the existing,
    only mode Kriya has ever supported - `extends` names the milestone whose
    entrypoint/build file this one grows), COMPOSITION means "consumes a
    capability an earlier, not-necessarily-immediate-predecessor milestone
    provides" (`consumes` names capabilities, `depends_on` names the
    milestones that must run first). Neither mode implies a new physical
    build artifact by itself - see Milestone.provides/consumes below and the
    MA3 physical-topology-preservation rule this domain model exists to
    support, not yet enforced by this module."""

    EXTENSION = "extension"
    COMPOSITION = "composition"


class AcceptanceCriterion(BaseModel):
    """One structured, checkable outcome for a milestone - the MA3 successor
    to Milestone.success_criterion's single free-text string (kept below,
    unchanged, since MilestoneV2 is additive in MA3.1: nothing yet consumes
    this type). Deliberately minimal per the MA3 spec - no verification
    command/test file/tool/status/evidence fields; those belong to a future
    control-plane acceptance implementation (MA5+), not this domain model."""

    id: str
    description: str

    @field_validator("id", "description")
    @classmethod
    def _non_blank(cls, v: str) -> str:
        if not (v or "").strip():
            raise ValueError("must not be blank")
        return v


class ProvidedCapability(BaseModel):
    """A named thing a milestone makes available to LATER milestones -
    `ProvidedCapability` rather than `ProvidedContract` because the formal
    ContractRegistry/contract lifecycle doesn't exist until MA5; this is
    planning/validation metadata a deterministic validator (MA3.3+) can check
    reachability for, not an enforced runtime contract."""

    name: str
    description: Optional[str] = None

    @field_validator("name")
    @classmethod
    def _non_blank(cls, v: str) -> str:
        if not (v or "").strip():
            raise ValueError("must not be blank")
        return v


class MilestoneV2(BaseModel):
    """MA3 milestone schema: an explicit dependency DAG (`depends_on`) and
    extension/composition semantics (`mode`/`extends`/`provides`/`consumes`)
    in place of the v1 `Milestone` class's single `depends_on_previous: bool`
    linear-chain assumption above. Deliberately introduced ADDITIVELY in
    MA3.1: nothing constructs, parses, or consumes this type yet (that's
    MA3.2's legacy-normalization loader and MA3.6's planner-output wiring) -
    v1 `Milestone` remains the type every existing code path
    (MilestonePlannerAgent, milestones.py's orchestrator, `kriya
    plan-milestones`/`generate --from-milestones`) actually uses until then,
    unchanged, so this class alone carries zero runtime risk.

    `kind` defaults to ChangeKind.MILESTONE and is deliberately NOT wired to
    drive process depth here or anywhere else - see kriya/workflow/triage.py
    and kriya/workflow/process_profile.py's own guardrail (ProcessProfile is
    resolved from ExecutionWeight only, never from `kind`); an individual
    milestone's own work can still be an enhancement/refactor/task in shape
    without that changing how deep MA3 or MA2 must process it.

    `id` is planner-suggested but NOT trusted as authoritative identity by
    itself - MA3.2's normalization/canonicalization step is what guarantees
    uniqueness for anything downstream that indexes by id (list position is
    never identity, per the MA3 invariant)."""

    id: str
    name: Optional[str] = None
    kind: ChangeKind = ChangeKind.MILESTONE

    goal: str
    depends_on: List[str] = Field(default_factory=list)

    mode: Optional[MilestoneMode] = None
    provides: List[ProvidedCapability] = Field(default_factory=list)
    consumes: List[str] = Field(default_factory=list)

    extends: Optional[str] = None
    entrypoint: Optional[str] = None
    adds_dependencies: List[str] = Field(default_factory=list)

    acceptance: List[AcceptanceCriterion] = Field(default_factory=list)

    @field_validator("id", "goal")
    @classmethod
    def _non_blank(cls, v: str) -> str:
        if not (v or "").strip():
            raise ValueError("must not be blank")
        return v


_FENCED_MILESTONES_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\"milestones\".*?\})\s*```", re.DOTALL)
# Deliberately greedy (`.*`, not `.*?`) unlike parse_file_list()'s analogous
# bare-object fallback (_BARE_FILES_OBJECT, which safely uses [^\]]* since a
# file PATH can never itself contain ']') - a milestone's goal/success_criterion
# is free text that legitimately can (a bracketed marker like the goal's own
# "[VERIFICATION] PASS" convention, an array-index reference, etc.). A
# non-greedy match stops at the FIRST ']'/'}' it finds anywhere in that free
# text, well before the real end of the JSON object - confirmed live via
# direct repro (a milestone goal mentioning "items[0]" truncated the match
# and broke json.loads). Greedy captures from the first '{' to the LAST '}'
# in the text instead, which is safe here because this pattern is only ever
# tried as a fallback when NO fenced block exists at all, so there is no
# other JSON-shaped content later in the response competing for the match.
_BARE_MILESTONES_OBJECT = re.compile(r'(\{.*"milestones"\s*:\s*\[.*\]\s*\})', re.DOTALL)


def parse_milestone_list(text: str) -> Tuple[Optional[List[Milestone]], Optional[str]]:
    """Extracts and validates a {"milestones": [...]} JSON block from a
    MilestonePlannerAgent completion - same last-fenced-JSON-block convention,
    same never-raises/(value, None)-or-(None, error) contract as
    parse_file_list() above, deliberately not shared code since the two
    regexes key off different JSON shapes ("files" vs "milestones") and a
    generic version would risk one accidentally matching the other's block in
    a response that (unexpectedly) contains both."""
    if not text or not text.strip():
        return None, "text is empty"

    candidates = _FENCED_MILESTONES_BLOCK.findall(text) or _BARE_MILESTONES_OBJECT.findall(text)
    if not candidates:
        return None, "no JSON milestones block found in the text"

    try:
        parsed = json.loads(candidates[-1])
    except json.JSONDecodeError as e:
        return None, f"milestones JSON block did not parse: {e}"

    try:
        milestones = MilestoneList.model_validate(parsed).milestones
    except ValidationError as e:
        return None, f"milestones JSON block failed schema validation: {e}"
    return milestones, None


class MilestoneListV2(BaseModel):
    """MA3.7's Schema v2 counterpart to MilestoneList above - same bounds,
    validated against MilestoneV2 instead of v1 Milestone. Kept as a SEPARATE
    model (not a Union/discriminated field on MilestoneList) so
    parse_milestone_list's own v1 contract - still needed to load OLD saved
    plan files (kriya/workflow/milestones.py's MilestoneRunState.from_dict) -
    stays byte-for-byte unchanged."""

    milestones: List[MilestoneV2]

    @field_validator("milestones")
    @classmethod
    def _non_empty_and_bounded(cls, v: List[MilestoneV2]) -> List[MilestoneV2]:
        if not v:
            raise ValueError("milestones list must not be empty")
        if len(v) > 8:
            raise ValueError(
                f"{len(v)} milestones suggests over-slicing (by code structure, not "
                "behavior) rather than genuine complexity - re-slice into fewer, "
                "larger vertical-slice milestones"
            )
        return v


def parse_milestone_list_v2(text: str) -> Tuple[Optional[List[MilestoneV2]], Optional[str]]:
    """v2 counterpart to parse_milestone_list() above - this is what
    MilestonePlannerAgent.run_with_milestone_list() actually calls now
    (MA3.7): the planner's live output is asked for and parsed as Schema v2
    directly, not v1-then-normalized. Reuses the SAME extraction regexes
    (_FENCED_MILESTONES_BLOCK/_BARE_MILESTONES_OBJECT) - they only key off
    the outer {"milestones": [...]} shape, identical in both schema
    versions - and only the inner pydantic validation model differs."""
    if not text or not text.strip():
        return None, "text is empty"

    candidates = _FENCED_MILESTONES_BLOCK.findall(text) or _BARE_MILESTONES_OBJECT.findall(text)
    if not candidates:
        return None, "no JSON milestones block found in the text"

    try:
        parsed = json.loads(candidates[-1])
    except json.JSONDecodeError as e:
        return None, f"milestones JSON block did not parse: {e}"

    try:
        milestones = MilestoneListV2.model_validate(parsed).milestones
    except ValidationError as e:
        return None, f"milestones JSON block failed schema validation: {e}"
    return milestones, None


def parse_file_list(text: str) -> Tuple[Optional[List[str]], Optional[str]]:
    """Extracts and validates a {"files": [...]} JSON block from a completion
    - a full Architect design (the file list is a trailing block inside a
    much larger prose response) or a Developer Step-1 response (asked to be
    exactly this shape, nothing else).

    Returns (files, None) on success or (None, error_message) on any failure
    - never raises, so callers can decide how to degrade (retry, fall back to
    a heuristic) without wrapping every call in a try/except for a
    json.JSONDecodeError or a pydantic ValidationError."""
    if not text or not text.strip():
        return None, "text is empty"

    candidates = _FENCED_JSON_BLOCK.findall(text) or _BARE_FILES_OBJECT.findall(text)
    if not candidates:
        return None, "no JSON file-list block found in the text"

    # Last block wins - see the illustrative-snippet-before-the-real-list case above.
    try:
        parsed = json.loads(candidates[-1])
    except json.JSONDecodeError as e:
        return None, f"file-list JSON block did not parse: {e}"

    try:
        files = FileList.model_validate(parsed).files
    except ValidationError as e:
        return None, f"file-list JSON block failed schema validation: {e}"
    return _normalize_file_list_paths(files), None
