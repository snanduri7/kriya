import sys, json, dataclasses, collections
SNAP = sys.argv[1]; sys.path.insert(0, SNAP)
from tests.architecture import module_map as mm, test_import_direction as tid
edges = mm.scan(); table = mm.owner_table()
base = tid.violations(edges, table)
assert set(base) == set(tid.ALLOWLIST)
def sim(pubs, rekeys):
    mods = dict(mm.MODULES)
    for dom, tgt in pubs:
        m = mods[dom]
        if tgt not in m.public: mods[dom] = dataclasses.replace(m, public=m.public + (tgt,))
    for path, frm, to in rekeys:
        a = mods[frm]; mods[frm] = dataclasses.replace(a, owner_paths=tuple(p for p in a.owner_paths if p != path))
        b = mods[to]; mods[to] = dataclasses.replace(b, owner_paths=b.owner_paths + (path,))
    t2 = mm.owner_table_for(mm.python_files(), mods)
    return tid.violations(edges, t2, mods)
pubs = []
for n in (1, 2, 3):
    for x in json.load(open(f"/private/tmp/claude-501/-Users-sriramnanduri-WorkingDirectory-AI-ClaudeCode-Kriya-By-ClaudeCode/bc971a42-dc25-4273-ba41-f653fa8c96f5/scratchpad/reg/allow_out_{n}.json")):
        if x["resolution"] == "PUBLIC_CONTRACT": pubs.append((x["domain"], x["target"]))
print("PUBLIC_CONTRACT targets across the 3 batches:", len(pubs), "distinct", len(set(pubs)))
a = sim(sorted(set(pubs)), [])
print("A) public declarations only: violations", len(base), "->", len(a), "| NEW:", sorted(set(a) - set(base))[:5])
# reason-changed rows
chg = [k for k in set(base) & set(a) if base[k] != a[k]]; print("   rows whose reason changed:", len(chg))
rk = [("kriya/workflow/context_recall_fixtures.py", "KERNEL", "QUALIFICATION"), ("kriya/core/execution_environment.py", "PROVIDER", "QUALIFICATION")]
try:
    b = sim(sorted(set(pubs)), rk); print("B) A + re-keys of context_recall_fixtures and execution_environment to QUALIFICATION: ->", len(b), "| NEW:", sorted(set(b) - set(base))[:5])
except Exception as e: print("B failed:", e)
rk4 = rk + [(f"kriya/workflow/{f}.py", "AUTHORITY", "KERNEL") for f in ("acceptance_approval", "authority_bundle", "authority_request", "authority_escalation")]
try:
    c = sim(sorted(set(pubs)), rk4); print("C) B + the four authority modules re-keyed to KERNEL (NOT recommended): ->", len(c), "| NEW:", sorted(set(c) - set(base))[:5])
except Exception as e: print("C failed:", e)
rem = collections.Counter(base[k][0] for k in a); print("remaining after A by removal slice:", dict(rem))
print("cleared by A, by old slice:", dict(collections.Counter(base[k][0] for k in set(base) - set(a))))
cleared = set(base) - set(a)
print("CLEARED by A, by ALLOWLIST removal slice:", dict(collections.Counter(tid.ALLOWLIST[k][0] for k in cleared)))
print("REMAINING after A, by ALLOWLIST removal slice:", dict(collections.Counter(tid.ALLOWLIST[k][0] for k in a)))
print("REMAINING P2 rows after A:", sum(1 for k in a if tid.ALLOWLIST[k][0] == "P2"), "of the 225 originally P2")
