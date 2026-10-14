#!/usr/bin/env python3
"""Task-bank prep + certification probe (experiment-only, no model calls).

  prep  CID...   build tasks/<CID>/{base_tree,ref_tree,hidden,ref_prod.patch,ref_test.patch,task.json}
  probe CID...   run hidden tests on BASE and REF, the module suite on BASE and REF(+orig tests), derive
                 F2P / P2P sets per test ID from junit XML, record timings and environment verdicts
  Both read the shallow repos made by fetch_candidates.py. Evaluation is offline after one acquisition run
  (`probe --online` on the first execution of a candidate), against pinned caches under bank/.

Verdicts per run use oracle_preflight.classify_run only as the OUTER environment screen; baseline brokenness is
decided from per-test junit results (F2P executed-and-failed while P2P passed in the same run).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

BANK = Path.home() / "kriya-m1-live/model-routing-v1/bank"
TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))  # oracle_preflight.py is vendored next to this file (byte copy of BACKEND-READINESS-V2/gate-certification)
from oracle_preflight import classify_run, ENVIRONMENT_UNAVAILABLE  # noqa: E402

M2, GRADLE_HOME, VENVS = BANK / "m2", BANK / "gradle-home", BANK / "venvs"
MVN_SKIP = ["-Drat.skip=true", "-Dcheckstyle.skip=true", "-Dspotbugs.skip=true", "-Dpmd.skip=true", "-Djacoco.skip=true",
            "-Danimal.sniffer.skip=true", "-Denforcer.skip=true", "-Dmaven.javadoc.skip=true", "-Drevapi.skip=true",
            "-Dcyclonedx.skip=true", "-Dlicense.skip=true", "-Dspotless.check.skip=true", "-Djapicmp.skip=true",
            "-Dmaven.source.skip=true", "-Dsurefire.failIfNoSpecifiedTests=false", "-DfailIfNoTests=false",
            "-Dcheckstyle.skip", "-Dskip.npm=true", "-Dclirr.skip=true", "-Dbnd.skip=true"]
TIMEOUT = 1500
# JSON-java's wrapper pins Gradle 6.3 (needs JDK<=14; only 17/21 installed); its upstream pom.xml is used instead.
BUILD_OVERRIDE = {"stleary/JSON-java": "maven"}
JDK_BY_REPO = {"jdbi/jdbi": "21", "resilience4j/resilience4j#2507": "21"}  # jdbi3-e2e enforcer requires JDK 21; everything else builds on 17
DIST = BANK / "dist"


def sh(args, cwd=None, env=None, timeout=TIMEOUT, log=None):
    t0 = time.time()
    try:
        p = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, check=False)
        out, rc = p.stdout + p.stderr, p.returncode
    except subprocess.TimeoutExpired as exc:
        out, rc = (exc.stdout or b"").decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or ""), 124
        out += "\nTIMEOUT"
    if log:
        Path(log).write_text(out, encoding="utf-8")
    return rc, out, round(time.time() - t0, 1)


def git(rdir, *args):
    return subprocess.run(["git", *args], cwd=rdir, capture_output=True, text=True, check=False).stdout


# ---------------------------------------------------------------- prep
def detect_build(tree: Path):
    if (tree / "gradlew").exists():
        return "gradle"
    if (tree / "pom.xml").exists():
        return "maven"
    if (tree / "build.gradle").exists() or (tree / "build.gradle.kts").exists():
        return "gradle"
    return "python"


def prep(cid: str):
    rec = json.loads((BANK / "cands" / (cid.replace("#", "_") + ".json")).read_text())
    rdir = BANK / "repos" / rec["repo"].replace("/", "_")
    tdir = BANK / "tasks" / cid.replace("#", "_")
    if tdir.exists():
        shutil.rmtree(tdir)
    for name, rev in (("base_tree", rec["base_sha"]), ("ref_tree", rec["fix_sha"])):
        (tdir / name).mkdir(parents=True)
        tar = subprocess.run(["git", "archive", rev], cwd=rdir, capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", str(tdir / name)], input=tar, check=True)
    tests, prod = rec["tests"], rec["prod"]
    other = rec["other"]
    hidden = tdir / "hidden"
    for t in tests:
        src = tdir / "ref_tree" / t
        if src.exists():
            (hidden / t).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, hidden / t)
    for fname, paths in (("ref_prod.patch", prod + other), ("ref_test.patch", tests)):
        (tdir / fname).write_text(git(rdir, "diff", rec["base_sha"], rec["fix_sha"], "--", *paths))
    build = BUILD_OVERRIDE.get(rec["repo"], detect_build(tdir / "ref_tree"))
    java_tests = [t for t in tests if t.endswith(".java") and "src/test/java/" in t]
    py_tests = [t for t in tests if t.endswith(".py")]
    py_fallback = sorted({str(Path(t).parent.parts[0]) + "" for t in tests if t.endswith((".txt", ".md", ".json", ".yaml", ".html"))})
    meta = {"id": cid, "repo": rec["repo"], "pr": rec["pr"], "title": rec["title"], "base": rec["base_sha"],
            "fix": rec["fix_sha"], "build": build, "lang": "java" if java_tests or build != "python" else "python",
            "prod_files": prod, "test_files": tests, "other_files": other, "churn": rec["churn"],
            "issues": [i["number"] for i in rec["issues"]], "n_parents": rec["n_parents"]}
    if meta["lang"] == "java":
        meta["module"] = os.path.commonprefix([t.split("src/test/java/")[0] for t in java_tests]).rstrip("/") if java_tests else ""
        meta["classes"] = sorted({t.split("src/test/java/")[1][:-5].replace("/", ".") for t in java_tests})
        meta["prod_modules"] = sorted({p.split("/src/")[0] if "/src/" in p else "" for p in prod})
    else:
        meta["test_modules"] = [t for t in py_tests if re.search(r"(^|/)(test_[^/]*|[^/]*_test)\.py$", t)] or py_tests or py_fallback
        first = next((f for f in prod if f.endswith(".py") and not f.endswith("setup.py")), prod[0]).split("/")
        meta["pkg"] = first[1] if first[0] == "src" and len(first) > 2 else first[0].removesuffix(".py")
        if not any(f.endswith(".py") and not f.endswith("setup.py") for f in prod):  # config-only fix: derive the package from the project name
            import tomllib
            name = tomllib.loads((tdir / "ref_tree/pyproject.toml").read_text()).get("project", {}).get("name", "").replace("-", "_")
            layout = (tdir / "ref_tree/src" / name).is_dir()
            meta["pkg"], first = name, (["src", name] if layout else [name])
        meta["src_layout"] = first[0] == "src"
        meta["needs_build"] = any(re.search(r"(^|/)(pyproject\.toml|setup\.(py|cfg))$", f) for f in prod + other)
    (tdir / "task.json").write_text(json.dumps(meta, indent=1))
    print(f"{cid}: build={build} lang={meta['lang']} classes/tests={meta.get('classes') or meta.get('test_modules')}")


# ---------------------------------------------------------------- junit
def parse_junit(files):
    res = {}
    for f in files:
        try:
            root = ET.parse(f).getroot()
        except ET.ParseError:
            continue
        for tc in root.iter("testcase"):
            tid = f"{tc.get('classname', '')}::{tc.get('name', '')}"
            bad = tc.find("failure") is not None or tc.find("error") is not None
            skipped = tc.find("skipped") is not None
            res[tid] = "fail" if bad else "skip" if skipped else "pass"
    return res


def junit_files(tree: Path, build: str, extra=None):
    if build == "maven":
        return list(tree.glob("**/target/surefire-reports/TEST-*.xml"))
    if build == "gradle":
        return list(tree.glob("**/build/test-results/**/TEST-*.xml"))
    return [Path(extra)] if extra and Path(extra).exists() else []


# ---------------------------------------------------------------- runners
def jvm_env(meta=None):
    env = dict(os.environ)
    env["GRADLE_USER_HOME"] = str(GRADLE_HOME)
    jdk = JDK_BY_REPO.get(f"{meta['repo']}#{meta['pr']}", JDK_BY_REPO.get(meta["repo"], "17")) if meta else "17"
    env["JAVA_HOME"] = subprocess.run(["/usr/libexec/java_home", "-v", jdk], capture_output=True, text=True).stdout.strip()
    return env


def gradle_bin(tree: Path):
    """The wrapper's own Gradle version, unpacked from services.gradle.org with curl (the wrapper's JVM download times out here)."""
    ver = re.search(r"gradle-([\d.]+)-(?:bin|all)\.zip", (tree / "gradle/wrapper/gradle-wrapper.properties").read_text()).group(1)
    home = DIST / f"gradle-{ver}"
    if not home.exists():
        DIST.mkdir(exist_ok=True)
        zpath = DIST / f"gradle-{ver}-bin.zip"
        if not zpath.exists():
            sh(["curl", "-sL", "--max-time", "600", "-o", str(zpath), f"https://services.gradle.org/distributions/gradle-{ver}-bin.zip"])
        sh(["unzip", "-q", "-o", str(zpath), "-d", str(DIST)])
    return str(home / "bin/gradle")


def neutral_git(tree: Path):
    """Build plugins (git-commit-id, git-build-hook, grgit) need a repository with a HEAD; give scratch trees an empty neutral one."""
    if not (tree / ".git").exists():
        env = dict(os.environ, GIT_AUTHOR_NAME="bank", GIT_AUTHOR_EMAIL="bank@localhost", GIT_COMMITTER_NAME="bank", GIT_COMMITTER_EMAIL="bank@localhost")
        subprocess.run(["git", "init", "-q"], cwd=tree, check=False, env=env)
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "scratch"], cwd=tree, check=False, env=env)


def run_java(meta, tree: Path, classes, scope: str, online: bool, log: Path):
    mod = meta["module"]
    if meta["build"] == "maven":
        cmd = ["mvn", "-B", "-ntp", f"-Dmaven.repo.local={M2}", *MVN_SKIP]
        if not online:
            cmd.append("-o")
        if mod:
            cmd += ["-pl", mod, "-am"]
        if classes:
            cmd.append("-Dtest=" + ",".join(c.split(".")[-1] for c in classes))
        cmd += ["-Dmaven.test.failure.ignore=true", "test"]
        neutral_git(tree)
        rc, out, secs = sh(cmd, tree, jvm_env(meta), log=log)
    else:
        neutral_git(tree)
        gw = gradle_bin(tree)
        task = (":" + mod.replace("/", ":") + ":test") if mod else "test"
        cmd = [gw, "--console=plain", "--continue", "--no-build-cache", task]  # never serve test results FROM-CACHE
        if not online:
            cmd.insert(1, "--offline")
        for c in classes or []:
            cmd += ["--tests", c]
        rc, out, secs = sh(cmd, tree, jvm_env(meta), log=log)
    return rc, out, secs, parse_junit(junit_files(tree, meta["build"]))


def py_env(meta, tree: Path):
    env = dict(os.environ)
    sp = str(tree / "src") if meta.get("src_layout") else str(tree)
    env.update({"PYTHONPATH": sp, "SETUPTOOLS_SCM_PRETEND_VERSION": "0.0.1", "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONHASHSEED": "0", "PIP_NO_INDEX": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1"})
    return env


def venv_py(meta):
    return VENVS / meta["repo"].replace("/", "_") / "bin/python"


def ensure_venv(meta, ref_tree: Path, log: Path):
    py = venv_py(meta)
    if py.exists():
        return
    py.parent.parent.mkdir(parents=True, exist_ok=True)
    sh(["python3.12", "-m", "venv", str(py.parent.parent)])
    reqs = []
    for rel in ("requirements/tests.txt", "requirements/dev.txt", "requirements-dev.txt", "requirements-test.txt",
                "tests/requirements.txt", "requirements/test.txt", "requirements.txt"):
        if (ref_tree / rel).exists():
            reqs += ["-r", str(ref_tree / rel)]
            break
    extras = ""
    pp = ref_tree / "pyproject.toml"
    if pp.exists():
        import tomllib
        data = tomllib.loads(pp.read_text())
        names = [k for k in data.get("project", {}).get("optional-dependencies", {}) if k in ("test", "tests", "testing", "dev")]
        extras = "[" + ",".join(names) + "]" if names else ""
        groups = data.get("dependency-groups", {})
        for g in ("tests", "test", "dev"):
            if g in groups:
                reqs += [d for d in groups[g] if isinstance(d, str)]
                break
    base = [str(py), "-m", "pip", "install", "-q"]
    before = {str(p.relative_to(ref_tree)) for p in ref_tree.rglob("*") if p.is_file()}
    env = dict(os.environ, SETUPTOOLS_SCM_PRETEND_VERSION="0.0.1")
    sh(base + ["pytest", "setuptools", "wheel"], log=log, env=env)
    if (ref_tree / "pyproject.toml").exists():
        import tomllib
        breq = tomllib.loads((ref_tree / "pyproject.toml").read_text()).get("build-system", {}).get("requires", [])
        if breq:
            sh(base + breq, log=log, env=env)
    if reqs:
        flat = []
        for r in reqs:
            flat.append(r)
        sh(base + flat, log=log, env=env)
    rc, out, _ = sh(base + ["-e", f"{ref_tree}{extras}"], log=log, env=env)
    if rc:
        rc, out, _ = sh(base + ["-e", str(ref_tree)], log=log, env=env)
    Path(log).write_text(out)
    # the editable install only exists to pull in the project's dependencies; it must not stay importable (oracle fallback hazard)
    listing = sh([str(py), "-m", "pip", "list", "--editable", "--format=json"])[1]
    try:
        for dist in json.loads(listing[listing.index("["):]):
            sh([str(py), "-m", "pip", "uninstall", "-y", dist["name"]])
    except ValueError:
        pass
    gen = sorted(str(p.relative_to(ref_tree)) for p in ref_tree.rglob("*") if p.is_file()
                 and str(p.relative_to(ref_tree)) not in before and ".egg-info" not in str(p) and "/build/" not in str(p))
    (py.parent.parent / "gen_files.json").write_text(json.dumps({"from": str(ref_tree), "files": gen, "install_rc": rc}))


def run_python(meta, tree: Path, selectors, scope: str, online: bool, log: Path):
    """Build the scratch tree's own package into <tree>/.dist (offline, no build isolation) and test THAT code. Fail closed:
    a failed build is PACKAGE_BUILD_FAILED, and an import that does not resolve inside the scratch tree is ORACLE_ENVIRONMENT
    (the venvs hold no project install, so there is no fallback to any other copy of the package)."""
    py = venv_py(meta)
    xml = tree / ".junit.xml"
    penv = py_env(meta, tree)
    dist = tree / ".dist"
    rc0, out0, secs0 = sh([str(py), "-m", "pip", "install", "-q", "--no-deps", "--no-build-isolation", "--no-index", "--target", str(dist), str(tree)],
                          tree, dict(penv, PIP_NO_INDEX="1"))
    if rc0:
        out = f"PACKAGE_BUILD_FAILED rc={rc0}\n{out0[-3000:]}"
        Path(log).write_text(out, encoding="utf-8")
        return 2, out, secs0, {}
    penv["PYTHONPATH"] = f"{dist}{os.pathsep}{penv['PYTHONPATH']}"
    cmd = [str(py), "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={xml}", "--maxfail=1000", "--continue-on-collection-errors"]
    cmd += selectors if selectors else []
    rc, out, secs = sh(cmd, tree, penv, log=log)
    chk = sh([str(py), "-c", f"import {meta['pkg']},sys;print('IMPORTED_FROM',{meta['pkg']}.__file__)"], tree, penv)[1]
    origin = re.search(r"IMPORTED_FROM (\S+)", chk)
    if origin and origin.group(1).startswith("/") and str(tree) not in origin.group(1):  # resolved from somewhere else: the oracle environment is wrong (fail closed)
        out += "\nORACLE_ENVIRONMENT package imported from outside the scratch tree: " + chk
        Path(log).write_text(out, encoding="utf-8")
        rc = rc or 3
    # no IMPORTED_FROM at all (ImportError): the candidate tree has no importable package -> the tests fail/are absent -> REJECT
    return rc, out, round(secs + secs0, 1), parse_junit(junit_files(tree, "python", xml))


def revert_tests(meta, tree: Path):
    """tree := REF with test files restored to BASE versions (regression oracle shape)."""
    tdir = BANK / "tasks" / meta["id"].replace("#", "_")
    for t in meta["test_files"]:
        b, dst = tdir / "base_tree" / t, tree / t
        if b.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(b, dst)
        elif dst.exists():
            dst.unlink()


def scratch(tdir: Path, name: str, src: str, overlay_hidden=False):
    dst = tdir / "scratch" / name
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(tdir / src, dst, symlinks=True)
    if overlay_hidden:
        shutil.copytree(tdir / "hidden", dst, dirs_exist_ok=True)
    return dst


def probe(cid: str, online: bool):
    tdir = BANK / "tasks" / cid.replace("#", "_")
    meta = json.loads((tdir / "task.json").read_text())
    logs = tdir / "logs"
    logs.mkdir(exist_ok=True)
    py = meta["lang"] == "python"
    runner = run_python if py else run_java
    sel = meta["test_modules"] if py else meta["classes"]
    out = {"id": cid, "online": online, "runs": {}}
    t0 = time.time()
    if py:
        ensure_venv(meta, tdir / "ref_tree", logs / "venv.log")
    out["setup_secs"] = round(time.time() - t0, 1)

    def go(name, src, selectors, hidden=False, revert=False):
        tree = scratch(tdir, name, src, overlay_hidden=hidden)
        if revert:
            revert_tests(meta, tree)
        rc, log, secs, res = runner(meta, tree, selectors, name, online, logs / f"{name}.log")
        verdict, reason = classify_run(rc, log)
        out["runs"][name] = {"rc": rc, "secs": secs, "verdict": verdict, "reason": reason,
                             "n": len(res), "failed": sorted(k for k, v in res.items() if v == "fail")[:40],
                             "results": res}
        shutil.rmtree(tree, ignore_errors=True)
        return res

    ref_acc = go("ref_accept", "ref_tree", sel)
    base_acc = go("base_accept", "base_tree", sel, hidden=True)
    base_full = go("base_regress", "base_tree", None)
    ref_full = go("ref_regress", "ref_tree", None, revert=True)
    f2p = sorted(t for t, s in ref_acc.items() if s == "pass" and base_acc.get(t) != "pass")
    p2p = sorted(t for t, s in ref_acc.items() if s == "pass" and base_acc.get(t) == "pass")
    out["F2P"], out["P2P_accept"] = f2p, p2p
    out["f2p_absent_at_base"] = sum(1 for t in f2p if t not in base_acc)
    out["baseline_compile_break"] = bool(f2p) and (not base_acc or out["f2p_absent_at_base"] == len(f2p))
    reg_set = sorted(t for t, s in base_full.items() if s == "pass" and ref_full.get(t) == "pass")
    out["regression_set_size"] = len(reg_set)
    out["superseded_by_fix"] = sorted(t for t, s in base_full.items() if s == "pass" and ref_full.get(t) != "pass")[:40]
    out["regression_ref_failures"] = [t for t in out["superseded_by_fix"] if t not in f2p]
    out["total_secs"] = round(time.time() - t0, 1)
    for r in out["runs"].values():
        r.pop("results")
    (tdir / "probe.json").write_text(json.dumps(out, indent=1))
    runs = out["runs"]
    print(f"{cid}: F2P={len(f2p)} P2P={len(p2p)} compile_break={out['baseline_compile_break']} "
          f"reg={len(reg_set)} reg_ref_fail={len(out['regression_ref_failures'])} "
          f"verdicts={ {k: v['verdict'][:3] for k, v in runs.items()} } secs={ {k: v['secs'] for k, v in runs.items()} }")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["prep", "probe"])
    ap.add_argument("ids", nargs="+")
    ap.add_argument("--online", action="store_true")
    a = ap.parse_args()
    for cid in a.ids:
        try:
            prep(cid) if a.cmd == "prep" else probe(cid, a.online)
        except Exception as exc:  # report and continue the batch
            print(f"{cid}: ERROR {type(exc).__name__}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
