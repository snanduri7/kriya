"""CAGC composition, caps, rendering and request refit (§8).

compose_guidance applies the role and per-capability caps once, producing the
block's immutable ``base_rule_entries``; refit_guidance only ever removes
further whole rules from that baseline, recomputed from it on every call (no
cumulative fitting, no reparsing of text, no re-selection). A rule is never
truncated.
"""
from __future__ import annotations

import hashlib
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from kriya.capabilities.guidance.facts import SelectionFacts
from kriya.capabilities.guidance.model import (
    DOMAIN_ORDER,
    CapabilityGuidance,
    GuidanceBlock,
    RenderedRule,
    Role,
)
from kriya.capabilities.guidance.registry import REGISTRY
from kriya.capabilities.guidance.selection import select
from kriya.workflow.context_budget import estimate_tokens

HEADER = ("=== Kriya capability guidance (general semantics; the repository conventions above take "
          "precedence) ===")
FOOTER = "=== end capability guidance ==="

# (total, per capability) in estimate_tokens units.
ROLE_CAPS: Dict[Role, Tuple[int, int]] = {
    Role.PLANNER: (250, 100),
    Role.ARCHITECT: (250, 100),
    Role.DEVELOPER: (200, 90),
    Role.REVIEWER: (200, 90),
}


def _order_key(rule: RenderedRule) -> Tuple[int, str, int, str]:
    return (DOMAIN_ORDER.index(rule.domain), rule.capability_id, rule.priority, rule.rule_id)


def _keep_order(rule: RenderedRule) -> Tuple[int, str]:
    """The order rules are kept in under a cap or a fit (dropped last first)."""
    return (rule.priority, rule.rule_id)


def _render(entries: Iterable[RenderedRule]) -> str:
    """The byte-stable block: "" without a rule; otherwise a header, one
    ``[capability]`` section per capability in render order, one line per
    rule, and a footer. Prefixed by a blank line so it can be placed after
    any prompt section."""
    ordered = sorted(entries, key=_order_key)
    if not ordered:
        return ""
    lines = ["", "", HEADER]
    current = None
    for rule in ordered:
        if rule.capability_id != current:
            current = rule.capability_id
            lines.append(f"[{current}]")
        lines.append(f"- {rule.text}")
    lines.append(FOOTER)
    return "\n".join(lines) + "\n"


def _capability_cost(entries: Sequence[RenderedRule]) -> int:
    return estimate_tokens(f"[{entries[0].capability_id}]\n" + "".join(f"- {r.text}\n" for r in entries))


def _block(selected_ids: Tuple[str, ...], base: Tuple[RenderedRule, ...], visible: Sequence[RenderedRule],
           cap_dropped: Tuple[str, ...]) -> GuidanceBlock:
    visible_ids = {rule.rule_id for rule in visible}
    visible_entries = tuple(rule for rule in base if rule.rule_id in visible_ids)
    text = _render(visible_entries)
    rendered = tuple(dict.fromkeys(f"{r.capability_id}@{r.capability_version}" for r in visible_entries))
    return GuidanceBlock(
        text=text, selected_capability_ids=selected_ids, rendered_capability_ids=rendered,
        base_rule_entries=base, visible_rule_entries=visible_entries, dropped_by_cap_rule_ids=cap_dropped,
        dropped_by_fit_rule_ids=tuple(rule.rule_id for rule in base if rule.rule_id not in visible_ids),
        estimated_tokens=estimate_tokens(text), digest=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def compose_guidance(facts: SelectionFacts, cap_tokens: Optional[int] = None,
                     registry: Sequence[CapabilityGuidance] = REGISTRY) -> GuidanceBlock:
    """The one selection/composition entry point (and the only ablation seam,
    via ``registry``): select, filter, then apply the role's total cap
    (``cap_tokens``, default ROLE_CAPS) and per-capability cap, keeping
    whole rules by (priority, rule_id). Rules the caps remove are
    ``dropped_by_cap_rule_ids``; the rest form ``base_rule_entries``."""
    total_cap, per_capability_cap = ROLE_CAPS[facts.role]
    if cap_tokens is not None:
        total_cap = cap_tokens
    selected = select(facts, registry)
    selected_ids = tuple(f"{c.capability_id}@{c.version}" for c, _rules in
                         sorted(selected, key=lambda item: (DOMAIN_ORDER.index(item[0].domain),
                                                            item[0].capability_id)))
    candidates = sorted((RenderedRule(c.capability_id, c.version, c.domain, r.rule_id, r.text, r.priority)
                         for c, rules in selected for r in rules), key=_keep_order)
    kept: List[RenderedRule] = []
    cap_dropped: List[str] = []
    for rule in candidates:
        same = [r for r in kept if r.capability_id == rule.capability_id] + [rule]
        if _capability_cost(same) <= per_capability_cap and estimate_tokens(_render(kept + [rule])) <= total_cap:
            kept.append(rule)
        else:
            cap_dropped.append(rule.rule_id)
    base = tuple(sorted(kept, key=_order_key))
    return _block(selected_ids, base, base, tuple(sorted(cap_dropped)))


def refit_guidance(base_block: GuidanceBlock, budget: int) -> GuidanceBlock:
    """``base_block`` refitted to ``budget`` estimate_tokens units, always
    from its immutable ``base_rule_entries``: whole rules are kept in
    (priority, rule_id) order until the next one would not fit; the rest are
    ``dropped_by_fit_rule_ids``. Cap-dropped rules never come back."""
    kept: List[RenderedRule] = []
    for rule in sorted(base_block.base_rule_entries, key=_keep_order):
        if estimate_tokens(_render(kept + [rule])) > budget:
            break  # priority order: a later rule never displaces an earlier one
        kept.append(rule)
    return _block(base_block.selected_capability_ids, base_block.base_rule_entries, kept,
                  base_block.dropped_by_cap_rule_ids)


def guidance_event_details(facts: SelectionFacts, block: GuidanceBlock) -> Dict[str, object]:
    """The ``capability.guidance`` event of one request, describing the exact
    block sent (call it after the request's final fit)."""
    return {
        "role": facts.role.value, "operation": facts.operation.value, "context": facts.context.value,
        "selected_capability_ids": list(block.selected_capability_ids),
        "rendered_capability_ids": list(block.rendered_capability_ids),
        "rule_ids": list(block.rule_ids),
        "dropped_by_cap_rule_ids": list(block.dropped_by_cap_rule_ids),
        "dropped_by_fit_rule_ids": list(block.dropped_by_fit_rule_ids),
        "estimated_tokens": block.estimated_tokens,
        "digest": block.digest,
    }
