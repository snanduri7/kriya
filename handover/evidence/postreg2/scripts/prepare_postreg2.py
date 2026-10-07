"""POST-REG-R2 PRIMARY CODING PROFILE COMPARISON: prepare one arm (no model call, no SEC-009 approval).

Arm A = qwen3-coder (source config gr1-graphify.yaml, the canonical qualified primary profile);
Arm B = qwen3.8 (source config qwen38-matched-developer.yaml, the qualified matched profile).
Creates: fresh workspace ws-postreg2-<arm>/gr1-graphify (git init + fetch of exactly 67f99bd from the canonical
source workspace, as the earlier fresh workspaces), pre-seeded frozen auto-skill skills-postreg2-<arm>, run config
config/postreg2-<arm>.yaml (source config with ONLY its paths.* lines changed), env-postreg2-<arm>.sh (Kriya 56ae8d3).
Refuses to overwrite anything.

usage: python3 prepare_postreg.py <a|b> <out.json>
"""
import difflib
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys

D = pathlib.Path.home() / "kriya-m1-live"
SOURCE_WS = pathlib.Path.home() / "kriya-live-validation/graphify-canonical-853aa43/workspace"
BASE = "67f99bd0059dd1bac9e44382907ef9f10098b39f"
TREE = "975f0667afa68908d7ab758551cb8cec6a9456f2"
CONTENT = "bba7689c625508feb81b3e8c024c8ec52ea0f844db0462dd789d5bb803c7600b"
MAINTAINER_FIX = "5d09dce42cc2945c9521360026c16845268ab77a"
FROZEN_SKILL = D / "skills-qwen38m" / "auto-gr1-graphify"
SKILL_FILES = {"instructions.md": "ea34cb4278ed29f0227a5de3ce1faa67359613699639233b041e29f9b9df62d1",
               "rules.txt": "4b167e8021312bdbbe30d7e077f7711a96668c9eedfc29ceffb774838ba7d814",
               "skill.yaml": "43d0527b2e8f42fe1eb73ee647a63df48c2c659befe6ce2591876fb2f09b7164"}
SKILL_AGGREGATE = "b4a91e1aaefc8653c635c35c636d373bb7c06a0fc2eebd0674daabff6e122a9f"
ARMS = {"a": ("postreg2-a", D / "config/gr1-graphify.yaml",
              "4ebd97b49143322307378a834a16d2897f7cafbdcff1cddd05090e73abd1c1e6"),
        "b": ("postreg2-b", D / "config/qwen38-matched-developer.yaml",
              "7e5952077c45a7e579dac398ec8095c9e5485ea11a3dc94a706fab0480f622d4")}


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def git(ws, *args):
    return subprocess.run(["git", "-C", str(ws), *args], capture_output=True, text=True, check=True).stdout.strip()


def skill_aggregate(skill):
    lines = [f"{sha(skill / n)}  auto-gr1-graphify/{n}" for n in sorted(SKILL_FILES)] + ["DIR auto-gr1-graphify/examples"]
    return hashlib.sha256("".join(line + "\n" for line in lines).encode()).hexdigest()


def main(arm, out):
    name, source_config, source_sha = ARMS[arm]
    ws_parent, skills, memory, state = D / f"ws-{name}", D / f"skills-{name}", D / "memory" / name, D / f"state-{name}"
    ws, config, env = ws_parent / "gr1-graphify", D / "config" / f"{name}.yaml", D / f"env-{name}.sh"
    for path in (ws_parent, skills, memory, state, config, env, D / f"logs-{name}"):
        assert not path.exists(), f"refusing to overwrite {path}"
    assert sha(source_config) == source_sha, f"source config digest changed: {source_config}"
    report = {"arm": arm, "name": name}

    # fresh workspace at exactly the frozen base
    ws_parent.mkdir()
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    git(ws, "fetch", "-q", "--no-tags", str(SOURCE_WS), BASE)
    git(ws, "checkout", "-q", "--detach", BASE)
    (ws_parent / "gr1-graphify.base").write_text(git(ws, "rev-parse", "HEAD") + "\n")
    fix_absent = subprocess.run(["git", "-C", str(ws), "cat-file", "-e", MAINTAINER_FIX],
                                capture_output=True, check=False).returncode != 0
    content = subprocess.run([str(D / "venv-postreg2/bin/python"), "-c",
                              f"from kriya.workflow.checkpoint import compute_workspace_content_hash as h;print(h({str(ws)!r}))"],
                             capture_output=True, text=True, check=True, cwd="/tmp").stdout.strip()
    report["workspace"] = {"path": str(ws), "head": git(ws, "rev-parse", "HEAD"), "tree": git(ws, "rev-parse", "HEAD^{tree}"),
                           "status": git(ws, "status", "--porcelain", "--ignored"), "remotes": git(ws, "remote"),
                           "maintainer_fix_absent": fix_absent, "content_hash": content}
    assert report["workspace"]["head"] == BASE and report["workspace"]["tree"] == TREE
    assert report["workspace"]["status"] == "" and report["workspace"]["remotes"] == "" and fix_absent and content == CONTENT

    # the frozen auto-skill, pre-seeded byte-for-byte
    target = skills / "auto-gr1-graphify"
    target.mkdir(parents=True)
    for n in SKILL_FILES:
        shutil.copy2(FROZEN_SKILL / n, target / n)
    (target / "examples").mkdir()
    assert sorted(p.name for p in target.iterdir()) == sorted([*SKILL_FILES, "examples"])
    assert all(sha(target / n) == h for n, h in SKILL_FILES.items())
    assert skill_aggregate(target) == SKILL_AGGREGATE and not any((target / "examples").iterdir())
    report["auto_skill"] = {"path": str(target), "files": {n: sha(target / n) for n in SKILL_FILES},
                            "aggregate": skill_aggregate(target)}

    # run config: the source config with only its paths.* values changed
    source = source_config.read_text()
    lines = source.splitlines(keepends=True)
    replaced = 0
    for i, line in enumerate(lines):
        for key, value in (("skills", skills), ("state", state), ("memory", memory)):
            if line.startswith(f"  {key}: ") and i > 0 and any(prev.startswith("paths:") for prev in lines[max(0, i - 4):i]):
                lines[i] = f"  {key}: {value}\n"
                replaced += 1
    assert replaced == 3, replaced
    config.write_text("".join(lines))
    diff = [row for row in difflib.unified_diff(source.splitlines(), config.read_text().splitlines(), lineterm="", n=0)
            if row[:1] in "+-" and not row.startswith(("+++", "---"))]
    assert len(diff) == 6 and all(row[1:].startswith(("  skills: ", "  state: ", "  memory: ")) for row in diff), diff
    report["config"] = {"path": str(config), "sha256": sha(config), "source": str(source_config),
                        "source_sha256": source_sha, "diff_vs_source": diff}

    env.write_text(
        f"# POST-REG-R2 comparison arm {arm.upper()} (Kriya 56ae8d3, venv-postreg2): source before any kriya command.\n"
        f"export KRIYA_STATE_DIR={state} KRIYA_LOG_DIR={D}/logs-{name} KRIYA_AUTHORITY_HOME={D}/authority\n"
        f"export KRIYA_STATIC_ANALYSIS_HOME={D}/static-analysis KRIYA_MCP_APPROVAL_HOME={D}/mcp-approvals\n"
        "unset KRIYA_CERTIFICATION_HOME PYTHONPATH\n"
        f"export KRIYA_QUALIFICATION_HOME={D}/qual-view-{name}   # run-specific READ-ONLY qualification view\n"
        f"export PATH={D}/venv-postreg2/bin:$PATH\n")
    state.mkdir()
    (D / f"logs-{name}").mkdir()
    report["env"] = {"path": str(env), "sha256": sha(env)}
    pathlib.Path(out).write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
