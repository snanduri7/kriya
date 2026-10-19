import collections
import json
import re

rows = []
for n in (1, 2, 3):
    for x in json.load(open(f"allow_out_{n}.json")):
        x["_b"] = n
        rows.append(x)
AUTH4 = {
    "kriya.workflow.acceptance_approval",
    "kriya.workflow.authority_bundle",
    "kriya.workflow.authority_escalation",
    "kriya.workflow.authority_request",
}
KERNEL_FROM_AUTH = {
    "kriya.workflow.acceptance_jvm",
    "kriya.workflow.acceptance_oracle",
    "kriya.workflow.context_source",
    "kriya.workflow.contract_compilation",
    "kriya.workflow.language_adapters",
    "kriya.workflow.obligations",
    "kriya.workflow.requirement_scopes",
    "kriya.workflow.requirements",
}
DEAD = {"kriya.tools.tool", "kriya.core.registry"}  # partly dead re-exports


def slice_name(s):
    s = str(s or "")
    for k in ("SURFACE-1", "PUB-1", "PUB1"):
        if s.startswith("NEW: " + k) or s.startswith(k):
            return "PUBSURFACE (one Type D slice)"
    m = re.match(r"NEW: ([A-Z0-9-]+[a-z0-9]*)", s)
    return m.group(1) if m else s.split(" (")[0].split(" - ")[0][:60]


out = []
for x in rows:
    t = x["target"]
    res = x["resolution"]
    n = x.get("importing_rows_count") or len(x.get("importing_rows") or [])
    slice_ = slice_name(x.get("removal_or_acceptance_slice"))
    note = ""
    if res == "PUBLIC_CONTRACT":
        action = "PUBLIC_CONTRACT"
        pub = "yes"
        fdom = x["domain"]
        slice_ = "PUBSURFACE (one Type D slice)"
        if t == "kriya.static_analysis.registry":
            note = "PUBLIC only for legitimate importers; the config.config row is a CONFIG->EXECUTION direction violation (do not widen footnote 9): move the provider settings models into CONFIG (unit CFG-SA, Type D)"
    elif t in AUTH4:
        action = "SPLIT_MODULE"
        pub = "no"
        fdom = "AUTHORITY (pure decisions/types/ports) + KERNEL (orchestration)"
        slice_ = "AUTHSPLIT (Type D; replaces SURFACE-1 re-key and OD-AB)"
        note = "owner 2026-10-19: no re-key of authority-decision modules to KERNEL"
    elif t in KERNEL_FROM_AUTH:
        action = "REPOINT_IMPORT"
        pub = "no"
        fdom = "KERNEL (unchanged)"
        slice_ = "AUTHSPLIT (Type D)"
        note = "the AUTHORITY importers are split; the target stays a KERNEL internal"
    elif t == "kriya.core.model_qualification":
        action = "SPLIT_MODULE"
        pub = "no (the split-off read model becomes public in its new owner)"
        fdom = "read model below L4 (PROVIDER/STATE owner TBD at QR-1) + QUALIFICATION"
        slice_ = "QR-1 (before N1)"
    elif t == "kriya.core.model_runtime":
        action = "SPLIT_MODULE"
        pub = "neutral half yes / provider-naming half no"
        fdom = "PROVIDER"
        slice_ = "I2b (Type D)"
    elif t == "kriya.core.execution_environment":
        action = "REKEY_DOMAIN"
        pub = "no"
        fdom = "QUALIFICATION"
        slice_ = "N1 scope extension (split first if it also runs processes)"
    elif t == "kriya.workflow.context_recall_fixtures":
        action = "REKEY_DOMAIN"
        pub = "no"
        fdom = "QUALIFICATION"
        slice_ = "PUBSURFACE (one Type D slice)"
    elif t == "kriya.tools.containment":
        action = "SPLIT_MODULE"
        pub = "already public"
        fdom = "EXECUTION"
        slice_ = "AUTHSPLIT (the importer authority_bundle is split)"
    elif t == "kriya.core.model_routing":
        action = "REMOVE_DEPENDENCY"
        pub = "no"
        fdom = "APPLICATION-SERVICE/POLICY (owner 2026-10-19)"
        slice_ = "ROUTE1"
    elif res == "REMOVE_DEPENDENCY":
        action = "REMOVE_DEPENDENCY"
        pub = "no"
        fdom = x["domain"]
        slice_ = "J1b (analyzer skill bootstrap out; Type C)"
    elif res == "REPOINT_IMPORTERS" or res == "EXISTING_SLICE_ALREADY_OWNS":
        action = "REPOINT_IMPORT"
        pub = "no"
        fdom = x["domain"]
    else:
        action = "?"
        pub = "?"
        fdom = x["domain"]
    if t in DEAD:
        note = (
            (note + " ; ").lstrip(" ;")
            + "the kriya.tools / kriya.core re-export rows are DEAD in-repo (DELETE_DEAD_CODE after the plugin/public-API search)"
        )
    proof = str(
        x.get("protecting_test")
        or "name-pinning/contract test + planted control; violations decrease, new=0, stale rows removed"
    )[:150]
    out.append(
        dict(
            target=t,
            current_violations=n,
            actual_owner=str(x.get("actual_owner"))[:70],
            action=action,
            final_domain=fdom,
            public=pub,
            closing_slice=slice_,
            required_proof=proof,
            note=note,
            batch=x["_b"],
        )
    )
json.dump(out, open("ALLOWLIST_FINAL_TABLE.json", "w"), indent=1)
c = collections.Counter()
r = collections.Counter()
for o in out:
    c[o["action"]] += 1
    r[o["action"]] += o["current_violations"]
print("targets by action:", dict(c))
print("rows by action:", dict(r), "total", sum(r.values()))
sl = collections.Counter()
for o in out:
    sl[o["closing_slice"]] += o["current_violations"]
for k, v in sl.most_common():
    print(f"  {v:3} rows -> {k}")
L = [
    "# Allowlist final per-target table (76 targets / 225 P2 rows) - from the 3 classifier reports with the owner's 2026-10-19 overrides; PROVISIONAL until the combined plan is reviewed\n",
    "| Target | Viol. | Actual owner | Action | Final domain | Public | Closing slice | Required proof |\n|---|---|---|---|---|---|---|---|",
]
for o in sorted(out, key=lambda o: (o["action"], o["target"])):
    L.append(
        f"| {o['target']} | {o['current_violations']} | {o['actual_owner']} | {o['action']} | {o['final_domain'][:50]} | {o['public'][:20]} | {o['closing_slice'][:60]} | {o['required_proof'][:70]} |"
    )
open("ALLOWLIST_FINAL_TABLE.md", "w").write("\n".join(L) + "\n")
