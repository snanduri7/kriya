import json, gzip, re, sys, hashlib
sys.path.insert(0, "/Users/sriramnanduri/WorkingDirectory/AI/ClaudeCode/Kriya-main-demo")
base = "/Users/sriramnanduri/WorkingDirectory/AI/ClaudeCode/Kriya-main-demo/handover/DEMO-V2/evidence/live-greenfield-failure/attempt-evidence"
recs = {}
for l in open(base + "/records.jsonl"):
    r = json.loads(l); recs[r["seq"]] = r
def blob(ref):
    h = ref.split(":", 1)[1]
    return gzip.decompress(open(f"{base}/blobs/{h[:2]}/{h}.gz", "rb").read()).decode()
resp = blob(recs[181]["blobs"]["content"])
m = re.search(r'<<<KRIYA:FILE path="pom.xml">>>\n(.*?)<<<KRIYA:END_FILE>>>', resp, re.S)
new_pom = m.group(1)
old_pom = blob(recs[204]["blobs"]["proposed"])
print("new==old", new_pom == old_pom, "sha new", hashlib.sha256(new_pom.encode()).hexdigest()[:12], "sha old", hashlib.sha256(old_pom.encode()).hexdigest()[:12], "proposed_digest", recs[204]["payload"]["proposed_digest"][7:19])
analysis = resp.split("<<<KRIYA:")[0].strip()
from kriya.workflow.attribution import find_edits_ignoring_own_diagnosis
print("RESULT(new pom):", (find_edits_ignoring_own_diagnosis(analysis, None, new_pom, old_pom) or "None")[:160])
print("RESULT(old pom as new):", (find_edits_ignoring_own_diagnosis(analysis, None, old_pom, old_pom) or "None")[:80])
