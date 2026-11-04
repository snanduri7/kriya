"""Measurement of the 28 sidecars refused by hosted run 41752818557 (usage: measure_refused_sidecars.py DIR_WITH_refused-28-sidecars.txt MANIFEST).

For each refused `<artifact>.sha1`: the bytes Central serves, the decoded SHA-1 value, whether Central's artifact equals the frozen SHA-256, and whether the frozen
sidecar digest equals a bare (no newline) rendering of the artifact's SHA-1. Reformatted for lint only; the logic is the script that produced the preserved output.
"""
import hashlib
import json
import re
import sys
import urllib.request
from collections import Counter

DIRECTORY, MANIFEST = sys.argv[1], sys.argv[2]
CENTRAL = "https://repo.maven.apache.org/maven2/"
manifest = {f["path"]: f for f in json.load(open(MANIFEST))["files"]}
paths = [line.strip() for line in open(DIRECTORY + "/refused.txt") if line.strip()]


def get(path):
    return urllib.request.urlopen(CENTRAL + path, timeout=60).read()


rows, bad = [], []
for sidecar_path in paths:
    artifact_path = sidecar_path[:-5]
    sidecar, artifact = get(sidecar_path), get(artifact_path)
    artifact_ok = hashlib.sha256(artifact).hexdigest() == manifest[artifact_path]["sha256"]  # Central artifact == frozen artifact?
    text = sidecar.decode("utf-8", "replace")
    found = re.match(r"\s*([0-9a-fA-F]{40})(\s+\*?(\S.*?))?\s*$", text)
    value = found.group(1).lower() if found else None
    sha1 = hashlib.sha1(artifact).hexdigest()  # noqa: S324 - comparing with Central's published SHA-1
    frozen = manifest[sidecar_path]["sha256"]
    candidates = {"bare": sha1.encode(), "bare+nl": (sha1 + "\n").encode()}
    matched = [name for name, content in candidates.items() if hashlib.sha256(content).hexdigest() == frozen]
    if re.fullmatch(r"\s*[0-9a-fA-F]{40}\s*", text):
        kind = "bare"
    elif "*" in text:
        kind = "hash *name"
    elif "/" in text.split(None, 1)[-1]:
        kind = "hash staging-path"
    else:
        kind = "hash name"
    kind += " +nl" if text.endswith("\n") else " no-nl"
    if not (artifact_ok and value == sha1 and matched == ["bare"]):
        bad.append(sidecar_path)
    rows.append((sidecar_path, kind, value == sha1, artifact_ok, matched, repr(sidecar)[:90]))
for row in rows:
    print(row)
print("TOTAL", len(rows), "ALL_OK", not bad, "BAD", bad)
print(Counter(row[1] for row in rows))
