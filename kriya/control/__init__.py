"""The control plane (MA5 of the control-plane implementation plan; see
kriya/policy/__init__.py for MA4's own principle, kriya/workflow/triage.py
for MA1, kriya/workflow/process_profile.py for MA2, kriya/workflow/
milestone_validation.py for MA3).

MA5's own principle: durable control state, contracts, artifacts, and
decisions become first-class, persisted objects instead of being
reconstructed each time from prompt text, generated files, or a milestone
sidecar's ad-hoc fields. This package sits ABOVE existing Kriya execution
(Planner/Architect/Developer/failure-grounding/attribution/retry/
verification) as an additive control layer, never a replacement of it -
every MA5 hard constraint (GenerationState stays run-scoped, existing
checkpointing keeps working, established_file_context/established_
dependencies remain available as compatibility projections, existing
retry/failure/verification machinery is untouched) exists to keep that
true. Control-plane stores are derived from deterministic evidence
(real build/workspace metadata, MA1-4's own deterministic classifiers)
wherever possible - an LLM's own output is never the source of truth for
a physical artifact fact, and a contract change is always explicit and
auditable, never a silent in-place edit.
"""
