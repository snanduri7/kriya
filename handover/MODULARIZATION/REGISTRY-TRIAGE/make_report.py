import json, collections
f=json.load(open("FINAL_CLASSIFICATION.json"))
orig=[x for x in f if not x["id"].endswith("-NEW")]
L=["# Registry classification - SIZING REPORT v2 (2026-10-19; PROVISIONAL per owner; snapshot 8e0c90a; scratch, nothing written to the repo)\n",
   "Status: AFTER the 4 classifier batches, my review, the 3 blind adversarial batches and the owner decisions of 2026-10-19. BEFORE: the E14 final report, the ONE combined allowlist simulation, the final owner-cohesive routing and the plan/ledger mutation.\n"]
c=collections.Counter(x["final_class"] for x in orig); c2=collections.Counter(x["final_class"] for x in f)
L.append("| Class | 76 registry rows | + derived/new rows (5 A, 1 B) |\n|---|---|---|")
for k,n in (("A","A - defect, fix in this workstream"),("B","B - future feature (DEFERRED_BY_OWNER/FUTURE_FEATURE)"),("C","C - obsolete/fixed"),("D","D - disproved"),("E","E - intentional contract (4 CONDITIONAL on pins)"),("NEEDS_OWNER_DECISION","needs owner decision")):
    L.append(f"| {n} | {c.get(k,0)} | {c2.get(k,0)} |")
v=collections.Counter(x["verified"] for x in orig)
L.append(f"\nVerification depth on the 76 rows: {dict(v)} (PROBE = reproduced by me; CODE_READ = I read code/contract; EVIDENCE_READ = I read classifier evidence + the blind adversarial verdict, no own code check). Every B/C/E row also had one BLIND adversarial review (row + definition only).\n")
L.append("## Class A (%d incl. %d derived) by closing unit\n\n| ID | Verified | Closing unit | Size | Prerequisite | Auth/Sec |\n|---|---|---|---|---|---|" % (c2["A"], c2["A"]-c["A"]))
for x in sorted([x for x in f if x["final_class"]=="A"], key=lambda x:(x["closing_slice"],x["id"])):
    L.append(f'| {x["id"]} | {x["verified"].replace("VERIFIED_BY_","")} | {x["closing_slice"][:70]} | {x["size"]} | {x["prerequisite"][:50]} | {"yes" if x["authority_security"] else ""} |')
by=collections.OrderedDict()
for x in f:
    if x["final_class"]=="A": by.setdefault(x["closing_slice"].split(" (")[0].split(" ")[0],[]).append(x["id"])
L.append("\n## Closing units (Class A rows per unit)\n"+"\n".join(f"- {k}: {len(v2)}" for k,v2 in sorted(by.items())))
L.append("\n## Conditional E (closure work first)\n"+"\n".join(f'- {x["id"]}: {x["note"]}' for x in f if x["final_class"]=="E" and "CONDITIONAL" in x["note"]))
L.append("\n## B (%d)\n"%c2["B"]+", ".join(sorted(x["id"] for x in f if x["final_class"]=="B")))
L.append("\n## C (%d)\n"%c2["C"]+", ".join(sorted(x["id"] for x in f if x["final_class"]=="C")))
open("SIZING_REPORT.md","w").write("\n".join(L)+"\n"); print(len(L))
