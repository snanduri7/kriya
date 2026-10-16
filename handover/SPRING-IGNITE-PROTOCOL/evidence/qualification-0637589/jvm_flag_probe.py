import re, subprocess, sys, os, tempfile, shutil, json
S = os.path.dirname(os.path.abspath(__file__)); ref = os.path.join(S, "ref1")
pom = open(os.path.join(ref, "pom.xml")).read()
flags = re.findall(r"<argument>(--add-opens=[^<]+)</argument>", pom)
cp = open(os.path.join(S, "cp.txt")).read().strip() + ":" + os.path.join(ref, "target/classes")
JDKS = {"17": "/Library/Java/JavaVirtualMachines/temurin-17.jdk/Contents/Home", "21": "/Library/Java/JavaVirtualMachines/temurin-21.jdk/Contents/Home"}
def run(jdk, fl):
    d = tempfile.mkdtemp(dir=S)
    try:
        p = subprocess.run([JDKS[jdk] + "/bin/java", *fl, "-classpath", cp, "com.example.Main"], cwd=d, capture_output=True, text=True, timeout=90)
        ok = p.returncode == 0 and "[VERIFICATION] PASS" in p.stdout
        why = "" if ok else (re.findall(r"(?:Caused by|Exception|Error)[^\n]{0,160}", p.stdout + p.stderr) or ["?"])[0]
        return ok, why
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT"
    finally:
        shutil.rmtree(d, ignore_errors=True)
out = {}
for jdk in sys.argv[1:]:
    out[jdk] = {"all24": run(jdk, flags), "none": run(jdk, [])}
    print(jdk, "all24", out[jdk]["all24"], "none", out[jdk]["none"], flush=True)
    if jdk == "17":
        needed = []
        for f in flags:
            ok, why = run(jdk, [x for x in flags if x != f])
            print("  drop", f, "->", "PASS" if ok else f"FAIL {why}", flush=True)
            if not ok: needed.append(f)
        out[jdk]["single_drop_fails"] = needed
        print("  minimal-by-single-drop (each needed alone):", needed, flush=True)
        ok, why = run(jdk, needed)
        print("  run with ONLY those:", ok, why, flush=True)
