"""Apply each mutation (file, old, new) alone, run the given tests, require a failure; always restore."""
import json, subprocess, sys, pathlib
spec = json.load(open(sys.argv[1]))
tests = spec["tests"]
results = []
for m in spec["mutations"]:
    p = pathlib.Path(m["file"]); original = p.read_text()
    if original.count(m["old"]) != 1:
        results.append((m["name"], "BAD-ANCHOR(%d)" % original.count(m["old"]))); continue
    try:
        p.write_text(original.replace(m["old"], m["new"]))
        r = subprocess.run([".venv/bin/pytest", "-q", "-x", "-p", "no:cacheprovider", *m.get("tests", tests)], capture_output=True, text=True)
        results.append((m["name"], "KILLED" if r.returncode != 0 else "SURVIVED"))
    finally:
        p.write_text(original)
for name, status in results: print(f"{status:10} {name}")
print(f"{sum(s=='KILLED' for _,s in results)}/{len(results)} killed")
