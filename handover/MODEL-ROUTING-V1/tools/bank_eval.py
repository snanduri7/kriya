#!/usr/bin/env python3
"""Frozen-oracle evaluator + certification for the MODEL-ROUTING-V1 task bank (experiment-only, no model calls).

A task PACKAGE (bank/frozen/<TID>/) is self-contained:
  ws/                      solver workspace (base tree, one neutral commit)       - also the baseline tree and the test-infrastructure reference
  oracle/{meta,spec}.json  runner metadata + frozen accept set (fail-to-pass + pass-to-pass of the hidden files)
  oracle/regression_set.json  tests passing on BASE and on REFERENCE(+original tests) in the regression scope
  oracle/hidden/           the upstream tests changed/added by the fix (overlaid for ACCEPT only)
  evaluator/ref_tree/ ...  reference (upstream fix) tree + patches + certification record (evaluator-only)

  evaluate-pkg PKG KIND TREE [--out LOG]   exit 0 PASS / 1 REJECT / 3 ENVIRONMENT_UNAVAILABLE / 4 INDETERMINATE (usage/internal errors are 4)
  regression-set PKG...                    (re)compute oracle/regression_set.json
  certify-pkg PKG...                       3x known-good + 3x baseline + controls -> evaluator/cert.json

Verdict rules (per-test junit vs the frozen sets):
  * test infrastructure in the candidate tree is RESTORED from ws before running (tests dirs, conftest.py, planted junit output are
    removed); a changed pytest configuration section is a REJECT (TAMPERED);
  * Python: the candidate's own package is built into <scratch>/.dist (offline); a failed build is a REJECT unless an environment
    signature shows; an import that does not resolve inside the scratch tree is ENVIRONMENT_UNAVAILABLE (fail closed);
  * a timeout is INDETERMINATE; REJECT requires executed tests that failed (or a candidate build/compile fault).
"""
import argparse
import configparser
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bank_tool as bt  # noqa: E402
from oracle_preflight import _ENVIRONMENT, ENVIRONMENT_UNAVAILABLE, INDETERMINATE, PASS, REJECT, classify_run  # noqa: E402

EXIT = {PASS: 0, REJECT: 1, ENVIRONMENT_UNAVAILABLE: 3, INDETERMINATE: 4}
_FIXED_IGNORE = shutil.ignore_patterns(".git", ".kriya", ".gradle", "__pycache__", ".pytest_cache", ".dist", ".junit.xml", "*.egg-info")
# Test-infrastructure paths per language. Java is limited to src/test/ and Python to tests/ dirs, a top-level testing/ dir and conftest-style files:
# a production package that merely contains a `testing` helper module (keyring.testing) must stay editable.
TEST_INFRA = {"java": re.compile(r"(^|/)src/test/"),
              "python": re.compile(r"(^|/)tests?/|^testing/|(^|/)conftest\.py$|(^|/)test_[^/]*\.py$|_tests?\.py$|(^|/)(sitecustomize|usercustomize)\.py$|\.pth$")}
SCRATCH = bt.BANK / "scratch"


def IGNORE(directory, names):  # noqa: N802 - used as shutil.copytree(ignore=...)
    """Drop VCS/IDE state and *build output* only: `target` beside a pom.xml, `build` beside a build.gradle(.kts).
    A source package or module that merely happens to be named `build` (jdbi internal/build, mapstruct ..multiple.build) is kept."""
    d = Path(directory)
    skip = set(_FIXED_IGNORE(directory, names))
    if "target" in names and (d / "pom.xml").exists():
        skip.add("target")
    if "build" in names and ((d / "build.gradle").exists() or (d / "build.gradle.kts").exists()):
        skip.add("build")
    return skip


class UsageError(Exception):
    pass


def load(pkg: Path):
    o = pkg / "oracle"
    return (json.loads((o / "meta.json").read_text()), json.loads((o / "spec.json").read_text()))


def files_of(root: Path):
    return {str(p.relative_to(root)): p for p in root.rglob("*") if p.is_file() and not any(x in p.parts for x in (".git", ".dist", "__pycache__"))}


def sanitize(scr: Path):
    """No pre-existing junit output may survive into the run."""
    for p in list(scr.rglob("TEST-*.xml")) + list(scr.rglob(".junit.xml")):
        p.unlink()
    for d in list(scr.rglob("surefire-reports")) + list(scr.rglob("test-results")):
        shutil.rmtree(d, ignore_errors=True)


def restore_test_infra(scr: Path, ws: Path, lang: str):
    """Make every test-infrastructure path of the scratch tree equal to the workspace baseline; return the list of paths that differed."""
    pattern = TEST_INFRA[lang]
    base = {k: v for k, v in files_of(ws).items() if pattern.search(k)}
    cur = {k: v for k, v in files_of(scr).items() if pattern.search(k)}
    changed = []
    for k, v in cur.items():
        if k not in base:
            v.unlink()
            changed.append(f"+{k}")
        elif v.read_bytes() != base[k].read_bytes():
            shutil.copy2(base[k], v)
            changed.append(f"~{k}")
    for k, v in base.items():
        if k not in cur:
            (scr / k).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(v, scr / k)
            changed.append(f"-{k}")
    return changed


def pytest_config(tree: Path):
    """The pytest configuration sections of a tree (pytest.ini, tox.ini, setup.cfg, pyproject.toml)."""
    cfg = {}
    for name in ("pytest.ini", "tox.ini", "setup.cfg"):
        f = tree / name
        if f.exists():
            cp = configparser.ConfigParser(strict=False, interpolation=None)
            try:
                cp.read_string(f.read_text(errors="ignore"))
            except configparser.Error:
                cfg[name] = "UNPARSEABLE"
                continue
            for sec in cp.sections():
                if sec in ("pytest", "tool:pytest"):
                    cfg[f"{name}:{sec}"] = dict(cp.items(sec))
    f = tree / "pyproject.toml"
    if f.exists():
        try:
            cfg["pyproject:tool.pytest"] = tomllib.loads(f.read_text()).get("tool", {}).get("pytest")
        except tomllib.TOMLDecodeError:
            cfg["pyproject:tool.pytest"] = "UNPARSEABLE"
    return cfg


def decide(rc, out, res, required):
    """((verdict, reason), missing) from the run result; shared by every oracle kind."""
    missing = [t for t in required if res.get(t) != "pass"]
    env_hit = any(p.search(out) for p in _ENVIRONMENT) or bool(re.search(r"BackendUnavailable|Cannot import '[\w.]+\.build'|No matching distribution", out))
    if "PACKAGE_BUILD_FAILED" in out:
        return ((ENVIRONMENT_UNAVAILABLE, "package build failed with an environment signature") if env_hit
                else (REJECT, "candidate package does not build")), missing
    if "ORACLE_ENVIRONMENT" in out:
        return (ENVIRONMENT_UNAVAILABLE, "package was not imported from the scratch tree (fail closed)"), missing
    if rc == 124:
        return (INDETERMINATE, "run timed out"), missing
    if not missing:
        return (PASS, f"all {len(required)} required tests passed"), missing
    if res:
        return (REJECT, f"{len(missing)}/{len(required)} required tests failed or were absent (executed {len(res)})"), missing
    verdict, reason = classify_run(rc or 1, out)
    return ((INDETERMINATE, "no tests executed") if verdict == PASS else (verdict, reason)), missing


def evaluate_pkg(pkg: Path, kind: str, tree: Path, log: Path, keep=False):
    if kind not in ("accept", "regress"):
        raise UsageError(f"unknown kind {kind}")
    if not tree.is_dir():
        raise UsageError(f"tree is not a directory: {tree}")
    meta, spec = load(pkg)
    reg = json.loads((pkg / "oracle/regression_set.json").read_text())
    required = spec["accept_set"] if kind == "accept" else reg["ids"]
    SCRATCH.mkdir(exist_ok=True)
    t0 = time.time()
    scr = Path(tempfile.mkdtemp(prefix=f"scr_{meta['id'].replace('#', '_')}_", dir=SCRATCH))
    shutil.rmtree(scr)
    shutil.copytree(tree, scr, ignore=IGNORE, symlinks=True)
    sanitize(scr)
    ws = pkg / "ws"
    changed = restore_test_infra(scr, ws, meta["lang"])
    note = f"test infrastructure restored from ws: {len(changed)} path(s) {changed[:5]}" if changed else ""
    tamper = None
    if meta["lang"] == "python" and pytest_config(scr) != pytest_config(ws):
        tamper = "TAMPERED=['pytest configuration changed']"
    if kind == "accept":
        shutil.copytree(pkg / "oracle/hidden", scr, dirs_exist_ok=True)
    setup = round(time.time() - t0, 1)
    if tamper:
        Path(log).write_text(tamper + "\n" + note)
        shutil.rmtree(scr, ignore_errors=True)
        return {"verdict": REJECT, "reason": tamper, "secs": 0.0, "setup_secs": setup, "n_executed": 0, "n_required": len(required), "missing_head": [],
                "p2p_pass": None, "f2p_failed_or_absent": None, "note": note}
    runner = bt.run_python if meta["lang"] == "python" else bt.run_java
    rc, out, secs, res = runner(meta, scr, spec["accept_selectors"] if kind == "accept" else None, kind, False, Path(log))
    (verdict, reason), missing = decide(rc, out, res, required)
    if not keep:
        shutil.rmtree(scr, ignore_errors=True)
    return {"verdict": verdict, "reason": reason, "secs": secs, "setup_secs": setup, "n_executed": len(res), "n_required": len(required),
            "missing_head": missing[:6], "note": note,
            "p2p_pass": sum(1 for t in spec["P2P_accept"] if res.get(t) == "pass") if kind == "accept" else None,
            "f2p_failed_or_absent": sum(1 for t in spec["F2P"] if res.get(t) != "pass") if kind == "accept" else None}


def regression_set(pkg: Path):
    """Tests passing on BASE (ws) and on REFERENCE with the ORIGINAL tests restored, in the regression scope."""
    meta, _ = load(pkg)
    runner = bt.run_python if meta["lang"] == "python" else bt.run_java
    SCRATCH.mkdir(exist_ok=True)
    sets = []
    for name, src in (("reg_base", pkg / "ws"), ("reg_ref", pkg / "evaluator/ref_tree")):
        scr = Path(tempfile.mkdtemp(prefix=f"{name}_", dir=SCRATCH))
        shutil.rmtree(scr)
        shutil.copytree(src, scr, ignore=IGNORE, symlinks=True)
        sanitize(scr)
        restore_test_infra(scr, pkg / "ws", meta["lang"])
        _, _, _, res = runner(meta, scr, None, name, False, Path(tempfile.gettempdir()) / f"{name}_{meta['id'].replace('#', '_')}.log")
        sets.append(res)
        shutil.rmtree(scr, ignore_errors=True)
    base_pass = {t for t, s in sets[0].items() if s == "pass"}
    ref_pass = {t for t, s in sets[1].items() if s == "pass"}
    out = {"ids": sorted(base_pass & ref_pass), "superseded_by_fix": sorted(base_pass - ref_pass),
           "base_failing_or_skipped": sum(1 for s in sets[0].values() if s != "pass"), "base_executed": len(sets[0])}
    (pkg / "oracle/regression_set.json").write_text(json.dumps(out))
    print(f"{meta['id']}: regression set {len(out['ids'])} (superseded {len(out['superseded_by_fix'])}, base failing/skipped {out['base_failing_or_skipped']})")
    return out


# ------------------------------------------------------------------ controls
def prod_hunks(patch: str):
    out, header, cur = [], None, None
    for line in patch.splitlines(keepends=True):
        if line.startswith("diff --git"):
            header, cur = [line], None
        elif header is not None and cur is None and not line.startswith("@@"):
            header.append(line)
        elif line.startswith("@@"):
            cur = [line]
            out.append((list(header), cur))
        elif cur is not None:
            cur.append(line)
    return out


def _exec_lines(body):
    n = 0
    for ln in body[1:]:
        if ln[:1] in "+-" and not ln.startswith(("+++", "---")):
            t = ln[1:].strip()
            if t and not t.startswith(("//", "#", "*", "/*", "import ", "from ", "<!--")):
                n += 1
    return n


def near_miss_candidates(patch_text):
    hunks = [(h, b) for h, b in prod_hunks(patch_text)
             if re.search(r"\.(java|py|ftl|jj|jjt)\b", h[0]) and not re.search(r"(^|/)(setup\.py)", h[0]) and _exec_lines(b)]
    ranked = sorted(hunks, key=lambda hb: -_exec_lines(hb[1]))
    return ranked[:4] if len(hunks) >= 2 else []


def fresh_copy(src: Path, name: str) -> Path:
    SCRATCH.mkdir(exist_ok=True)
    dst = Path(tempfile.mkdtemp(prefix=f"ctl_{name}_", dir=SCRATCH))
    shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=IGNORE, symlinks=True)
    return dst


def control_trees(pkg: Path, meta):
    """Yield (name, tree, expected): 'PASS' for the harmless edit, 'REJECT' for everything that must not pass."""
    ref, ws = pkg / "evaluator/ref_tree", pkg / "ws"
    for k, (header, body) in enumerate(near_miss_candidates((pkg / "evaluator/ref_prod.patch").read_text())):
        t = fresh_copy(ref, f"nm{k}")
        pf = t / "_nm.patch"
        pf.write_text("".join(header) + "".join(body))
        r = subprocess.run(["git", "apply", "-R", "--recount", "--unidiff-zero", str(pf)], cwd=t, capture_output=True, text=True)
        pf.unlink()
        if r.returncode == 0:
            yield f"near_miss_{k}", t, "REJECT"
        else:
            shutil.rmtree(t, ignore_errors=True)
    t = fresh_copy(ref, "harmless")
    (t / "SCRATCH_NOTE.txt").write_text("unrelated note\n")
    yield "harmless", t, "PASS"
    t = fresh_copy(ws, "fake_junit")
    if meta["lang"] == "java":
        fake = t / "zz_fake/target/surefire-reports"
        fake.mkdir(parents=True)
        spec = json.loads((pkg / "oracle/spec.json").read_text())
        cases = "".join(f'<testcase classname="{i.split("::")[0]}" name="{i.split("::")[1]}"/>' for i in spec["accept_set"])
        (fake / "TEST-fake.xml").write_text(f"<testsuite>{cases}</testsuite>")
    else:
        (t / ".junit.xml").write_text("<testsuite/>")
        (t / "conftest.py").write_text("import pytest\n@pytest.hookimpl(hookwrapper=True)\ndef pytest_runtest_makereport(item, call):\n"
                                       "    out = yield\n    rep = out.get_result()\n    if rep.failed:\n        rep.outcome = 'passed'\n")
    yield "tamper_planted_pass", t, "REJECT"
    if meta["lang"] == "python":
        t = fresh_copy(ref, "delete_pkg")
        pk = meta["pkg"]
        for cand in (t / "src" / pk, t / pk, t / f"{pk}.py", t / "src" / f"{pk}.py"):
            if cand.is_dir():
                shutil.rmtree(cand)
                break
            if cand.is_file():
                cand.unlink()
                break
        yield "delete_package", t, "REJECT"
        if (ref / "pyproject.toml").exists():
            t = fresh_copy(ref, "broken_metadata")
            text = (t / "pyproject.toml").read_text()
            text = re.sub(r'(?m)^requires-python\s*=.*$', 'requires-python = ">=99"', text) if re.search(r'(?m)^requires-python', text) \
                else text.replace("[project]", '[project]\nrequires-python = ">=99"', 1)
            (t / "pyproject.toml").write_text(text)
            yield "unsatisfiable_requires_python", t, "REJECT"


def certify_pkg(pkg: Path, repeats=3):
    meta, spec = load(pkg)
    reg = json.loads((pkg / "oracle/regression_set.json").read_text())
    logs = pkg / "evaluator/cert_logs"
    logs.mkdir(parents=True, exist_ok=True)
    res = {"id": meta["id"], "ref": [], "baseline": []}
    ref_tree, base_tree = pkg / "evaluator/ref_tree", pkg / "ws"
    for i in range(repeats):
        res["ref"].append({"accept": evaluate_pkg(pkg, "accept", ref_tree, logs / f"ref_accept_{i}.log"),
                           "regress": evaluate_pkg(pkg, "regress", ref_tree, logs / f"ref_regress_{i}.log")})
        res["baseline"].append({"accept": evaluate_pkg(pkg, "accept", base_tree, logs / f"base_accept_{i}.log"),
                                "regress": evaluate_pkg(pkg, "regress", base_tree, logs / f"base_regress_{i}.log")})
    controls = {}
    for name, tree, expected in control_trees(pkg, meta):
        r = {"accept": evaluate_pkg(pkg, "accept", tree, logs / f"ctl_{name}.log")}
        if name == "harmless":
            r["regress"] = evaluate_pkg(pkg, "regress", tree, logs / f"ctl_{name}_regress.log")
        shutil.rmtree(tree, ignore_errors=True)
        ok = (r["accept"]["verdict"] == PASS and r["regress"]["verdict"] == PASS) if expected == "PASS" else r["accept"]["verdict"] == REJECT
        r.update({"expected": expected, "as_expected": ok})
        controls[name] = r
    nm_names = [n for n in controls if n.startswith("near_miss")]
    v = lambda x: x["verdict"]  # noqa: E731
    ref_ok = all(v(r["accept"]) == PASS and v(r["regress"]) == PASS for r in res["ref"])
    base_broken = all(v(b["accept"]) == REJECT and b["accept"]["f2p_failed_or_absent"] == len(spec["F2P"])
                      and (b["accept"]["p2p_pass"] == len(spec["P2P_accept"]) or spec["baseline_compile_break"]) for b in res["baseline"])
    env_bad = any(v(x) in (ENVIRONMENT_UNAVAILABLE, INDETERMINATE) for r in res["ref"] + res["baseline"] for x in r.values())
    failed_controls = [n for n, c in controls.items() if not n.startswith("near_miss") and not c["as_expected"]]
    limits = []
    if not nm_names:
        limits.append("no near-miss control (fewer than two executable production hunks, or none applied)")
    elif not any(controls[n]["as_expected"] for n in nm_names):
        limits.append("near-miss control: no single-hunk revert was rejected (oracle may not cover every reference hunk)")
    if spec["baseline_compile_break"]:
        limits.append("baseline fails by compile/import break: the goal must state the public API (reviewer judgement)")
    if reg["superseded_by_fix"]:
        limits.append(f"{len(reg['superseded_by_fix'])} base tests superseded by the fix and excluded from the regression set")
    if reg["base_failing_or_skipped"]:
        limits.append(f"{reg['base_failing_or_skipped']} base tests fail or are skipped in this environment and are outside the regression set")
    cost = {"accept_secs": max(r["accept"]["secs"] for r in res["ref"]), "regress_secs": max(r["regress"]["secs"] for r in res["ref"]),
            "ref_good_total_secs": round(sum(r["accept"]["secs"] + r["regress"]["secs"] for r in res["ref"]) / repeats, 1),
            "baseline_setup_secs": res["baseline"][0]["accept"]["setup_secs"]}
    if not ref_ok or not base_broken or env_bad or failed_controls:
        verdict = "REJECTED"
        limits.append(f"ref_ok={ref_ok} baseline_broken={base_broken} env_problem={env_bad} failed_controls={failed_controls}")
    else:
        verdict = "CERTIFIED-WITH-LIMITS" if limits else "CERTIFIED"
    res.update({"controls": controls, "verdict": verdict, "limits": limits, "cost": cost, "repeats": repeats})
    (pkg / "evaluator/cert.json").write_text(json.dumps(res, indent=1))
    print(f"{meta['id']}: {verdict} cost={cost} limits={limits}")


def provision_python(pkg: Path):
    """One-time acquisition (network allowed here only): the build backends the base AND reference trees ask for."""
    meta, _ = load(pkg)
    py = bt.venv_py(meta)
    reqs = set()
    for tree in (pkg / "ws", pkg / "evaluator/ref_tree"):
        pp = tree / "pyproject.toml"
        if pp.exists():
            reqs.update(tomllib.loads(pp.read_text()).get("build-system", {}).get("requires", []))
    rc = subprocess.run([str(py), "-m", "pip", "install", "-q", "setuptools", "wheel", *sorted(reqs)], capture_output=True, text=True,
                        env=dict(__import__("os").environ, SETUPTOOLS_SCM_PRETEND_VERSION="0.0.1"))
    print(f"{meta['id']}: provisioned build backends {sorted(reqs)} rc={rc.returncode}")


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["evaluate-pkg", "regression-set", "certify-pkg", "provision-python"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    if a.cmd == "evaluate-pkg":
        if len(a.args) != 3:
            raise UsageError("usage: evaluate-pkg PKG accept|regress TREE")
        r = evaluate_pkg(Path(a.args[0]).resolve(), a.args[1], Path(a.args[2]).resolve(), Path(a.out or "/dev/null"))
        print(json.dumps(r))
        return EXIT[r["verdict"]]
    for p in a.args:
        pkg = Path(p).resolve()
        {"regression-set": regression_set, "certify-pkg": certify_pkg, "provision-python": provision_python}[a.cmd](pkg)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # usage and internal errors are INDETERMINATE (4): they never read as a candidate REJECT (1)
        print(f"ORACLE_INTERNAL_ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(4)
