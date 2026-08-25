"""ControlState - MA5.1 of the control-plane implementation plan (see
kriya/control/__init__.py for MA5's overall principle).

Answers "what request are we executing, what risk/profile applies, which
milestone are we on, which contracts/artifacts exist, what durable
checkpoint is valid" - durable CONTROL METADATA, never context memory.
Deliberately excludes large source content, prompt text, or generated file
bodies (see kriya/workflow/context_package.py, MA5.6, for that) - this is
small enough to persist and diff on every milestone transition.

Frozen and immutable by construction, the same convention every MA1-4
domain type already established (kriya/workflow/triage.py's
EngineeringRoute, kriya/policy/model.py's ActionRequest/PolicyResult,
kriya/policy/trust.py's TrustedContent, ...): with_updates() returns a NEW
ControlState rather than mutating one in place, so a caller can never lose
track of which state object an earlier decision was actually made against.

Kept explicitly separate from GenerationState (kriya/workflow/state.py) -
see MA5's own design doc section 8: ControlState answers "what request/
risk/milestone/contract/checkpoint," GenerationState answers "what
happened in THIS Developer attempt, which retry are we on." Never merge
them; GenerationState remains scoped to one generation/repair run exactly
as it always has.

engineering_route/process_profile round-trip ASYMMETRICALLY: a freshly
constructed ControlState (during a live run) can hold the real, live
EngineeringRoute/ProcessProfile objects (kriya/workflow/triage.py,
kriya/workflow/process_profile.py). Neither of those classes has ever
needed - or been given - a from_dict() reconstructor (they're one-way,
"content-free operational telemetry" projections by their own design); a
ControlState loaded back FROM DISK therefore has engineering_route/
process_profile set to None, carrying only their to_dict() SUMMARIES
under the persisted JSON's own "engineering_route"/"process_profile" keys
for a human/telemetry consumer to read. A caller that needs the live
objects back after a resume re-derives them the normal way (MA1/MA2's own
classification path) and calls with_updates() to attach them - this
mirrors the exact "load control metadata, then re-run required
acceptance" resume flow MA5.9 specifies, not a gap unique to this field.
"""

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from kriya.workflow.process_profile import ProcessProfile
from kriya.workflow.triage import EngineeringRoute

CURRENT_SCHEMA_VERSION = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ControlState:
    schema_version: int
    run_id: str

    engineering_route: Optional[EngineeringRoute] = None
    process_profile: Optional[ProcessProfile] = None

    milestone_group_id: Optional[str] = None
    current_milestone_id: Optional[str] = None

    # milestone_id -> a plain state string ("pending"/"in_progress"/"done"/
    # "failed") - deliberately not its own enum-typed field per MA5's own
    # "do not over-engineer" spirit; kriya/workflow/milestones.py's own
    # completed_milestone_ids already tracks completion authoritatively,
    # this is a denser, queryable summary alongside it, not a replacement.
    milestone_states: Dict[str, str] = field(default_factory=dict)

    # subtask_id -> "completed"/"failed", the MA6/WorkflowController analog
    # of milestone_states above - added 2026-08-24 to make MA5.9's resume-
    # validation machinery (compute_control_plane_hashes/
    # validate_resume_against_reality, kriya/workflow/checkpoint.py) real:
    # those functions existed since MA5.9 but had zero callers anywhere
    # until wired into WorkflowController._run_structured_enforce's own
    # subtask-level resume. Unlike MA3's milestone flow, MA6's
    # EngineeringPlan is rebuilt FRESH on every enforce() call (no separate
    # plan-persistence sidecar) - current_plan_hash below is what lets a
    # resumed run detect "the freshly-rebuilt plan doesn't match what these
    # subtask_states were recorded against" and refuse to trust them,
    # exactly the drift MA5.9 was designed to catch.
    subtask_states: Dict[str, str] = field(default_factory=dict)

    # subtask_id -> the real, applied file paths that subtask actually wrote
    # to the workspace (not the plan's mere upfront planned_files declaration -
    # this mirrors call_result["files"], the ground truth) - added 2026-08-25
    # so a LATER re-plan that abandons this subtask (a fresh Planner call
    # produces a different plan shape - see WorkflowController._run_structured_
    # enforce's "refusing subtask resume" path) has enough information to
    # identify which real, already-applied files belonged only to the
    # abandoned plan and no longer belong to any subtask in the new one.
    # Found live, 2026-08-25 (protocol_encoder_java): an abandoned plan's own
    # completed subtask had already applied ProtocolMain.java to the real
    # workspace; the next run re-planned with a different shape (Main.java
    # instead) and nothing ever knew ProtocolMain.java was now orphaned -
    # left silently on disk, alongside the new files, with no plan
    # referencing it. Without this field, that recovery is structurally
    # impossible - subtask_states alone only says "id -> completed", not
    # "which files did completing it produce."
    subtask_written_files: Dict[str, List[str]] = field(default_factory=dict)

    current_plan_hash: Optional[str] = None
    current_contract_hash: Optional[str] = None
    current_artifact_registry_hash: Optional[str] = None

    base_commit: Optional[str] = None
    tree_hash: Optional[str] = None
    patch_hash: Optional[str] = None

    last_verified_checkpoint: Optional[str] = None

    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def with_updates(self, **changes: Any) -> "ControlState":
        """Returns a NEW ControlState with the given fields replaced and
        updated_at refreshed to now - never mutates self. created_at,
        run_id, and schema_version are never meant to change after
        construction; passing them here is a caller error, not silently
        accepted (dataclasses.replace itself would happily let a caller
        overwrite them, so this explicitly guards against that)."""

        immutable_fields = {"run_id", "schema_version", "created_at"}
        overridden = immutable_fields & changes.keys()
        if overridden:
            raise ValueError(
                f"ControlState.with_updates cannot change {sorted(overridden)} - these are "
                "fixed at construction. Build a new ControlState directly if a genuinely new "
                "run/schema is intended."
            )
        return replace(self, **changes, updated_at=_now_iso())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "engineering_route": self.engineering_route.to_dict() if self.engineering_route else None,
            "process_profile": self.process_profile.to_dict() if self.process_profile else None,
            "milestone_group_id": self.milestone_group_id,
            "current_milestone_id": self.current_milestone_id,
            "milestone_states": dict(self.milestone_states),
            "subtask_states": dict(self.subtask_states),
            "subtask_written_files": {k: list(v) for k, v in self.subtask_written_files.items()},
            "current_plan_hash": self.current_plan_hash,
            "current_contract_hash": self.current_contract_hash,
            "current_artifact_registry_hash": self.current_artifact_registry_hash,
            "base_commit": self.base_commit,
            "tree_hash": self.tree_hash,
            "patch_hash": self.patch_hash,
            "last_verified_checkpoint": self.last_verified_checkpoint,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ControlState":
        """Reconstructs a ControlState from a previously persisted dict.
        engineering_route/process_profile always come back None - see this
        module's own docstring for why; their to_dict() summaries under
        those same keys in `data` are simply not reconstructible into live
        objects and are dropped here, not silently misrepresented as live
        ones."""

        return cls(
            schema_version=data["schema_version"],
            run_id=data["run_id"],
            engineering_route=None,
            process_profile=None,
            milestone_group_id=data.get("milestone_group_id"),
            current_milestone_id=data.get("current_milestone_id"),
            milestone_states=dict(data.get("milestone_states", {})),
            subtask_states=dict(data.get("subtask_states", {})),
            subtask_written_files={k: list(v) for k, v in data.get("subtask_written_files", {}).items()},
            current_plan_hash=data.get("current_plan_hash"),
            current_contract_hash=data.get("current_contract_hash"),
            current_artifact_registry_hash=data.get("current_artifact_registry_hash"),
            base_commit=data.get("base_commit"),
            tree_hash=data.get("tree_hash"),
            patch_hash=data.get("patch_hash"),
            last_verified_checkpoint=data.get("last_verified_checkpoint"),
            created_at=data.get("created_at", _now_iso()),
            updated_at=data.get("updated_at", _now_iso()),
        )

    def content_hash(self) -> str:
        """Stable sha256 over everything except the two timestamps (pure
        metadata, not content a resume comparison should care about) - used
        by MA5.9's checkpoint/resume validation to detect drift between a
        saved checkpoint's expectation and the control state actually
        loaded back."""

        hashable = self.to_dict()
        hashable.pop("created_at", None)
        hashable.pop("updated_at", None)
        blob = json.dumps(hashable, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    @classmethod
    def new(
        cls,
        run_id: str,
        engineering_route: Optional[EngineeringRoute] = None,
        process_profile: Optional[ProcessProfile] = None,
        milestone_group_id: Optional[str] = None,
    ) -> "ControlState":
        now = _now_iso()
        return cls(
            schema_version=CURRENT_SCHEMA_VERSION,
            run_id=run_id,
            engineering_route=engineering_route,
            process_profile=process_profile,
            milestone_group_id=milestone_group_id,
            created_at=now,
            updated_at=now,
        )
