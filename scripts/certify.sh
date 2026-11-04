#!/usr/bin/env bash
# PRD-034: the canonical production-certification procedure. The CI job
# `production-certification` runs exactly this, and so does the target
# machine; the local run is the executed evidence.
#
#   scripts/certify.sh [--maven-repo DIR] [--thresholds FILE] [--doctor-config FILE --doctor-workspace DIR [--doctor-trust-file FILE]] [OUT_DIR]
#                                       (OUT_DIR default: certification-out/<UTC stamp>; pass one OUTSIDE the work tree for a canonical run)
#
# Stages, each recorded in OUT_DIR/stages.jsonl:
#   static        ruff + pylint (zero findings)
#   (progress)    during pytest, OUT_DIR/pytest/progress.json is kept current; watch it with scripts/certification_watch.py OUT_DIR
#   maven_repository  the frozen, ATTESTED certification Maven repository (tests/certification/maven_repository.manifest.json) is present and byte-exact; it is then served
#                 on loopback for the whole run and every contained Maven acquisition goes through it (autonomy.acquisition_registry_mirror seam; Central is not in the proxy
#                 ACL). Provision it first with `scripts/certification_maven_repo.py fetch` (needs Central ONCE, bootstrap - never during the run).
#   collection    collection/deselection audit: the canonical population is exactly `not live_model`, every deselected test is live_model,
#                 no real_toolchain / live_static_analysis / Docker / JVM / Gradle-container test disappeared (tests/_certification_collection.py)
#   perf_probe    bounded representative probe (fresh temp root, basetemp not growing, listing a small fraction of time); FAIL blocks pytest
#   pytest        THE canonical population `pytest -o addopts= -m "not live_model"` in certification mode (an unexpected skip fails), run as a deterministic static SHARDING of
#                 the one audited census (scripts/certification_shards.py; CERT_TOPOLOGY=serial runs the single-process reference), merged and audited before any verdict
#   tier_execution  every test of every mandatory tier appears in the junit file and PASSED; the run's population equals the audited one
#   images        every digest-pinned image present with exactly its digest (never pulled here)
#   scanner       Semgrep is exactly 1.178.0 and the live_static_analysis tests of the pytest run passed (no second pytest run)
#   release       sdist/wheel build, integrity and clean-install smoke (PRD-002)
#   thresholds    (with --thresholds) the owner's release-threshold file frozen: path, text, digest, loader values
#   doctor        `kriya doctor --production --json` - recorded, never turned into PASS
# A stage whose tool is missing is UNAVAILABLE and fails the certification; nothing is skipped silently. Refuses to run as root.
#
# AUTHORITY (SEC-009): this script NEVER creates, signs, mints or substitutes an operator approval. The doctor runs in one of two recorded modes:
#   operator_config   --doctor-config FILE --doctor-workspace DIR [--doctor-trust-file FILE]: the canonical loader decides. If the approval for that
#                     exact config and workspace is absent or stale the stage FAILS with the exact owner command in OUT_DIR/doctor-owner-action.txt;
#                     the owner runs it, and certification is run again.
#   packaged_defaults no doctor options: packaged defaults only (no approval needed); the doctor reports production_ready as it is
#                     (profile.production is not asserted on defaults). This is the mode a hosted runner without an owner-shipped artifact can use.
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
THRESHOLDS=""; DOCTOR_CONFIG=""; DOCTOR_WORKSPACE=""; DOCTOR_TRUST=""; OUT_ARG=""; MAVEN_REPO="${KRIYA_CERT_MAVEN_REPO:-$HOME/.cache/kriya-cert-maven/repo}"; PY_WHEELS="${KRIYA_CERT_PYTHON_WHEELS:-$HOME/.cache/kriya-cert-wheels}"
while [ $# -gt 0 ]; do
  case "$1" in
    --thresholds) THRESHOLDS="${2:?--thresholds needs a file}"; shift 2 ;;
    --doctor-config) DOCTOR_CONFIG="${2:?--doctor-config needs a file}"; shift 2 ;;
    --doctor-workspace) DOCTOR_WORKSPACE="${2:?--doctor-workspace needs a directory}"; shift 2 ;;
    --doctor-trust-file) DOCTOR_TRUST="${2:?--doctor-trust-file needs a file}"; shift 2 ;;
    --maven-repo) MAVEN_REPO="${2:?--maven-repo needs a directory}"; shift 2 ;;
    --python-wheels) PY_WHEELS="${2:?--python-wheels needs a directory}"; shift 2 ;;
    -*) echo "[certify] unknown option: $1" >&2; exit 2 ;;
    *) OUT_ARG="$1"; shift ;;
  esac
done
if [ -n "$DOCTOR_CONFIG" ] && [ -z "$DOCTOR_WORKSPACE" ]; then
  echo "[certify] --doctor-config needs --doctor-workspace (an approval is bound to the exact workspace)" >&2; exit 2
fi
OUT="${OUT_ARG:-certification-out/$(date -u +%Y%m%dT%H%M%SZ)}"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
VENV_BIN="$ROOT/.venv/bin"
: > "$OUT/stages.jsonl"

stage() {  # stage NAME STATUS [DETAIL]
  printf '{"stage": "%s", "status": "%s", "detail": %s}\n' "$1" "$2" \
    "$("$VENV_BIN/python" -c 'import json,sys; print(json.dumps(sys.argv[1]))' "${3:-}")" >> "$OUT/stages.jsonl"
  echo "[certify] $1: $2 ${3:-}"
}

if [ "$(id -u)" = "0" ]; then
  echo "[certify] refusing to run as root: production certification runs as an ordinary user" >&2
  exit 2
fi
if [ ! -x "$VENV_BIN/python" ]; then
  echo "[certify] no venv at $ROOT/.venv (python3 -m venv .venv && .venv/bin/pip install -e '.[dev]')" >&2
  exit 2
fi
# The environment under test: the venv's tools first, never the caller's shell.
export PATH="$VENV_BIN:$PATH"
export KRIYA_CERTIFICATION=1
export PYTHONDONTWRITEBYTECODE=1  # a real pytest run inside a bound candidate: __pycache__ would be flagged by the verification-tree binding
# CERT-TMP-GROWTH-001: the run gets its OWN fresh temp root, outside the ambient system temp area. MEASURED: path-identity checks list every ancestor
# directory of a workspace in full, and an ambient temp area holding the leftovers of earlier runs (23k entries) made the same 47 tests take 302 s instead
# of 80 s. Only this directory is removed on exit - never anything of the operator's.
CERT_TMP="$(mktemp -d /tmp/kriya-cert.XXXXXX)"
DOCTOR_HOME=""
cleanup() { [ -n "${SHARD_RUN_PID:-}" ] && kill "$SHARD_RUN_PID" 2>/dev/null; [ -n "${MAVEN_SERVER_PID:-}" ] && kill "$MAVEN_SERVER_PID" 2>/dev/null; [ -n "${PIP_SERVER_PID:-}" ] && kill "$PIP_SERVER_PID" 2>/dev/null; rm -rf "$CERT_TMP" ${DOCTOR_HOME:+"$DOCTOR_HOME"}; }
trap cleanup EXIT
export TMPDIR="$CERT_TMP"

# --- the authoritative host Maven (PRD-034, owner ruling 2026-11-04) ----------------------------------------------------------------------------
# Exactly ONE host Maven: Apache Maven 3.10.0, provisioned by scripts/certification_maven_toolchain.py (pinned Apache SHA-512 verified before extraction) and exposed as
# MAVEN_HOME. Anything else - no MAVEN_HOME, a tree without the provisioning marker (the runner image's /usr/bin/mvn), an altered tree, another version - is a FAIL here and
# the canonical pytest stage is then UNAVAILABLE. The authority's bin goes FIRST on PATH, so every host-side `mvn` of the run (and the environment record below) is its.
MAVEN_AUTHORITY_OK=0
if [ -n "${MAVEN_HOME:-}" ] && maven_verdict="$("$VENV_BIN/python" -I scripts/certification_maven_toolchain.py verify "$MAVEN_HOME" 2>&1)"; then
  printf '%s\n' "$maven_verdict" > "$OUT/maven-toolchain.json"
  export PATH="$(cd "$MAVEN_HOME" && pwd -P)/bin:$PATH"
  MAVEN_AUTHORITY_OK=1
  stage maven_toolchain PASS "$maven_verdict"
else
  [ -n "${MAVEN_HOME:-}" ] || maven_verdict="REFUSED: MAVEN_HOME is not set (provision the authority: scripts/certification_maven_toolchain.py provision DIR; export MAVEN_HOME=DIR)"
  stage maven_toolchain FAIL "$maven_verdict"
fi

# --- environment identity ---------------------------------------------------
"$VENV_BIN/python" - "$OUT/environment.json" "$OUT/maven-toolchain.json" <<'PY'
import json, os, platform, shutil, subprocess, sys, hashlib
def run(*cmd):
    if shutil.which(cmd[0]) is None:
        return None
    out = subprocess.run(cmd, capture_output=True, text=True)
    text = (out.stdout or out.stderr).strip().splitlines()
    return text[0] if text else None
freeze = subprocess.run([sys.executable, "-m", "pip", "freeze"], capture_output=True, text=True).stdout
env = {
    "os": platform.platform(), "machine": platform.machine(), "uid_is_root": False,
    "python": platform.python_version(), "python_executable": sys.executable,
    "pip_freeze_sha256": hashlib.sha256(freeze.encode()).hexdigest(),
    "java": run("java", "-version"), "maven": run("mvn", "-v"), "maven_authority": json.load(open(sys.argv[2])) if os.path.exists(sys.argv[2]) else None, "gradle": run("gradle", "--version"),
    "docker_client": run("docker", "version", "--format", "{{.Client.Version}}"),
    "docker_server": run("docker", "version", "--format", "{{.Server.Version}}"),
    "semgrep": run("semgrep", "--version"), "git": run("git", "--version"),
    # The Maven INSIDE each pinned Maven image is a separate supplied-toolchain identity (owner ruling 2026-11-04): recorded, not forced to the host authority's version.
    "container_maven": {line.split("@")[0]: run("docker", "run", "--rm", "--pull", "never", "--network", "none", "--entrypoint", "mvn", line, "-v")
                        for line in (raw.split("#")[0].strip() for raw in open("tests/certification/pinned_images.txt")) if line.startswith("maven:")},
    "git_revision": run("git", "rev-parse", "HEAD"),
}
json.dump(env, open(sys.argv[1], "w"), indent=2, sort_keys=True)
PY
stage environment RECORDED "$(cat "$OUT/environment.json" | tr -d '\n' | cut -c1-200)"

# --- release identity (PLAT-RELEASE-IDENTITY-001) ------------------------------
# Kriya revision, platform providers + capability statuses, containment backend
# (certification exercises OCI), environment. A material change later makes this
# certification stale (kriya.core.release_identity.compare_release_identity).
if "$VENV_BIN/python" -c 'import json, sys; from kriya.core.release_identity import release_identity; json.dump(release_identity(sys.argv[2]), open(sys.argv[1], "w"), indent=2, sort_keys=True)' \
    "$OUT/release-identity.json" oci 2> "$OUT/release-identity.stderr.txt"; then
  stage release_identity RECORDED "$("$VENV_BIN/python" -c 'import json, sys; print(json.load(open(sys.argv[1]))["digest"])' "$OUT/release-identity.json")"
else
  stage release_identity FAIL "see release-identity.stderr.txt"
fi

# --- static checks -------------------------------------------------------------
if ruff check . > "$OUT/ruff.txt" 2>&1 && pylint kriya plugins/core_tools tests > "$OUT/pylint.txt" 2>&1; then
  stage static PASS
else
  stage static FAIL "see ruff.txt / pylint.txt"
fi

# --- deterministic Maven provisioning (MAVEN-CENTRAL-CERTIFICATION-NONDETERMINISM-001) --------------------------------------------
# The canonical run must not depend on live Maven Central: every contained Maven acquisition goes through the product's operator-owned registry mirror
# (tests/conftest.py::_certification_maven_mirror, env KRIYA_CERT_MAVEN_MIRROR) to the frozen repository served here. No network is used by this stage.
MANIFEST="$ROOT/tests/certification/maven_repository.manifest.json"
MAVEN_SERVER_PID=""
MAVEN_REPO_OK=0
if "$VENV_BIN/python" scripts/certification_maven_repo.py verify "$MAVEN_REPO" "$MANIFEST" --require-attested > "$OUT/maven-repo-verify.txt" 2>&1; then
  MAVEN_PORT="$("$VENV_BIN/python" -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
  case "$(uname -s)" in Linux) MAVEN_BIND="0.0.0.0" ;; *) MAVEN_BIND="127.0.0.1" ;; esac  # Docker Engine reaches the host through host-gateway; Docker Desktop reaches loopback
  "$VENV_BIN/python" scripts/certification_maven_repo.py serve "$MAVEN_REPO" "$MANIFEST" --port "$MAVEN_PORT" --bind "$MAVEN_BIND" --require-attested \
      --log "$OUT/maven-repo-served.jsonl" --progress "$OUT/pytest/progress.json" > "$OUT/maven-repo-server.txt" 2>&1 &
  MAVEN_SERVER_PID=$!
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    curl -s -o /dev/null "http://127.0.0.1:$MAVEN_PORT/maven2/" && break
    kill -0 "$MAVEN_SERVER_PID" 2>/dev/null || break
    sleep 1
  done
  if kill -0 "$MAVEN_SERVER_PID" 2>/dev/null; then
    export KRIYA_CERT_MAVEN_MIRROR="http://host.docker.internal:$MAVEN_PORT/maven2" KRIYA_CERT_MAVEN_REPO="$MAVEN_REPO" KRIYA_CERT_MAVEN_MANIFEST="$MANIFEST"
    MAVEN_REPO_OK=1
    stage maven_repository PASS "content digest $("$VENV_BIN/python" -c 'import json, sys; print(json.load(open(sys.argv[1]))["content_digest"])' "$MANIFEST") served on $MAVEN_BIND:$MAVEN_PORT"
  else
    stage maven_repository FAIL "the repository server did not start: $(tail -2 "$OUT/maven-repo-server.txt" | tr '\n' ' ')"
  fi
else
  stage maven_repository FAIL "$(head -3 "$OUT/maven-repo-verify.txt" | tr '\n' ' ')"
fi

# --- frozen Python wheelhouse (PRD034-EXTERNAL-ACQUISITION: the release stage must not depend on live PyPI) ---------------------------------------------
HASHED_REQUIREMENTS="$ROOT/tests/certification/requirements.release.hashes.txt"
PY_WHEELS_OK=0
PIP_SERVER_PID=""
if "$VENV_BIN/python" scripts/certification_python_wheels.py verify "$HASHED_REQUIREMENTS" "$PY_WHEELS" > "$OUT/python-wheelhouse-verify.txt" 2>&1; then
  "$VENV_BIN/python" scripts/certification_python_wheels.py digest "$HASHED_REQUIREMENTS" "$PY_WHEELS" > "$OUT/python-wheelhouse.json"
  export KRIYA_RELEASE_WHEELHOUSE="$PY_WHEELS" KRIYA_RELEASE_HASHED_REQUIREMENTS="$HASHED_REQUIREMENTS"
  PY_WHEELS_OK=1
  # the SAME frozen wheelhouse also serves the contained pip tests as a PEP 503 index (hash-listed files only, re-hashed at every request): one content authority for PyPI
  PIP_PORT="$("$VENV_BIN/python" -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
  case "$(uname -s)" in Linux) PIP_BIND="0.0.0.0" ;; *) PIP_BIND="127.0.0.1" ;; esac
  "$VENV_BIN/python" scripts/certification_python_wheels.py serve "$HASHED_REQUIREMENTS" "$PY_WHEELS" --port "$PIP_PORT" --bind "$PIP_BIND" \
      --log "$OUT/python-index-served.jsonl" --progress "$OUT/pytest/progress.json" > "$OUT/python-index-server.txt" 2>&1 &
  PIP_SERVER_PID=$!
  sleep 2
  if kill -0 "$PIP_SERVER_PID" 2>/dev/null; then
    export KRIYA_CERT_PIP_INDEX="http://host.docker.internal:$PIP_PORT/simple"
    stage python_wheelhouse PASS "$(cat "$OUT/python-wheelhouse.json") index on $PIP_BIND:$PIP_PORT"
  else
    PY_WHEELS_OK=0
    stage python_wheelhouse FAIL "the wheelhouse index server did not start: $(tail -2 "$OUT/python-index-server.txt" | tr '\n' ' ')"
  fi
else
  stage python_wheelhouse FAIL "$(head -3 "$OUT/python-wheelhouse-verify.txt" | tr '\n' ' ') (provision: scripts/certification_python_wheels.py provision)"
fi

# --- product-built containment images are PRE-PROVISIONED (PRD034-EXTERNAL-ACQUISITION APT): the canonical run must not run apt-get ---------------------------
IMAGES_OK=0
if "$VENV_BIN/python" scripts/provision_containment_images.py record "$OUT/product-images.json" > "$OUT/product-images.txt" 2>&1 \
   && "$VENV_BIN/python" - "$OUT/product-images.json" <<'PY'
import json, sys
sys.path.insert(0, "scripts")
from provision_containment_images import BASES
have = json.load(open(sys.argv[1]))["images"]
from kriya.tools import containment_oci as oci
need = [oci._acquisition_image_tag(base) for base in BASES] + [oci._PROXY_IMAGE_TAG]
missing = [tag for tag in need if tag not in have]
print("missing:", missing) if missing else None
sys.exit(1 if missing else 0)
PY
then
  IMAGES_OK=1
  stage product_images PASS "$(wc -l < "$OUT/product-images.json" | tr -d ' ') lines; ids recorded (provision: scripts/provision_containment_images.py build)"
else
  stage product_images FAIL "a product-built containment image is missing: $(tail -2 "$OUT/product-images.txt" | tr '\n' ' ') (scripts/provision_containment_images.py build)"
fi

# --- collection truth (before the long run) -------------------------------------
mkdir -p "$OUT/collection" "$OUT/pytest"
if pytest -o addopts= --collect-only -q -p no:cacheprovider --collection-census="$OUT/collection/all.json" > "$OUT/collection/all.txt" 2>&1 \
   && pytest -o addopts= -m "not live_model" --collect-only -q -p no:cacheprovider --collection-census="$OUT/collection/selected.json" > "$OUT/collection/selected.txt" 2>&1 \
   && "$VENV_BIN/python" tests/_certification_collection.py audit "$OUT/collection/all.json" "$OUT/collection/selected.json" "$OUT/collection" > "$OUT/collection/audit.txt" 2>&1; then
  stage collection PASS "$(head -1 "$OUT/collection/audit.txt")"
else
  stage collection FAIL "$(head -3 "$OUT/collection/audit.txt" 2>/dev/null | tr '\n' ' ')"
fi

# --- performance probe (CERT-TMP-GROWTH-001): the run-1 crawl must not be able to recur before hours are spent ---------------------
# A small fixed representative population judged by mechanism invariants (fresh temp root, basetemp not growing, directory listing a small fraction of
# the time) - not a wall-clock budget. FAIL blocks the canonical pytest stage.
PERF_PROBE_OK=1
if "$VENV_BIN/python" scripts/certification_perf_probe.py "$OUT/perf-probe" > "$OUT/perf-probe.txt" 2>&1; then
  stage perf_probe PASS "$(grep -c '^\[perf-probe\] ' "$OUT/perf-probe.txt") classes; see perf-probe/perf-probe.json"
else
  PERF_PROBE_OK=0
  stage perf_probe FAIL "$(tail -3 "$OUT/perf-probe.txt" | tr '\n' ' ')"
fi

# --- THE canonical population: pytest -o addopts= -m "not live_model" ------------
# Includes real_toolchain, live_static_analysis, Docker and JDK/Maven/Gradle-container tests; the repository addopts must not remove a tier.
missing=""
for tool in docker java mvn semgrep; do command -v "$tool" >/dev/null 2>&1 || missing="$missing $tool"; done
SEMGREP_VERSION="$(semgrep --version 2>/dev/null | head -1)"
if [ -n "$missing" ]; then
  stage pytest UNAVAILABLE "required tools missing:$missing"
elif ! docker info >/dev/null 2>&1; then
  stage pytest UNAVAILABLE "docker daemon not reachable"
elif [ "$SEMGREP_VERSION" != "1.178.0" ]; then
  stage pytest UNAVAILABLE "semgrep must be exactly 1.178.0 (found: ${SEMGREP_VERSION:-none})"
elif [ "$MAVEN_AUTHORITY_OK" != "1" ]; then
  stage pytest UNAVAILABLE "the authoritative host Maven (3.10.0) is not available (stage maven_toolchain): the run would use whatever mvn the host happens to have"
elif [ "$MAVEN_REPO_OK" != "1" ]; then
  stage pytest UNAVAILABLE "the frozen, attested Maven certification repository is not available (stage maven_repository): the run would depend on live Maven Central"
elif [ "$PY_WHEELS_OK" != "1" ]; then
  stage pytest UNAVAILABLE "the frozen Python wheelhouse is not available (stage python_wheelhouse): the release stage would depend on live PyPI, hours after the pytest stage"
elif [ "$IMAGES_OK" != "1" ]; then
  stage pytest UNAVAILABLE "the product-built containment images are not provisioned (stage product_images): the run would execute apt-get on a cold host"
elif [ "$PERF_PROBE_OK" != "1" ]; then
  stage pytest UNAVAILABLE "the performance probe failed (CERT-TMP-GROWTH-001): hours are not spent on a run that can crawl; see perf-probe/perf-probe.json"
elif [ "${CERT_TOPOLOGY:-sharded}" = "serial" ]; then
  # the SERIAL reference topology (c3-run4: 4h58m). Same population, one process.
  printf '%s\n' "pytest -o addopts= -m \"not live_model\" -p no:cacheprovider -q --certification --certification-report=$OUT/pytest --collection-census=$OUT/pytest/collection-census.json --junitxml=$OUT/pytest/junit.xml --durations=50" > "$OUT/pytest/command.txt"
  if pytest -o addopts= -m "not live_model" -p no:cacheprovider -q --certification --certification-report="$OUT/pytest" \
      --collection-census="$OUT/pytest/collection-census.json" --junitxml="$OUT/pytest/junit.xml" --durations=50 > "$OUT/pytest.txt" 2>&1; then
    stage pytest PASS "$(tail -1 "$OUT/pytest.txt")"
  else
    stage pytest FAIL "$(tail -1 "$OUT/pytest.txt")"
  fi
else
  # PRD-034 static sharding (scripts/certification_shards.py): ONE authoritative census (stage collection), a deterministic disjoint partition into one EXCLUSIVE shard
  # (every Docker / JVM / Maven / toolchain / Semgrep test and every measured container user: their leak checks list every kriya container on the daemon) and N ordinary
  # shards, each its own pytest process with its own TMPDIR, basetemp, progress, junit and census. Ordinary shards see a `docker` that refuses container-mutating
  # subcommands. NO shard declares PASS: the verdict is derived from the merged junit after the merge audit proved union == census, disjoint, one result per node, tiers equal.
  "$VENV_BIN/python" scripts/certification_shards.py run "$OUT/collection/selected.json" "$OUT/shards" "$OUT/pytest" --ordinary "${CERT_ORDINARY_SHARDS:-3}" \
      --exclusive-files tests/certification/shard_exclusive_files.json --timings tests/certification/shard_timings.json --tmp-root "$CERT_TMP" > "$OUT/pytest-run.txt" 2>&1 &
  SHARD_RUN_PID=$!
  wait "$SHARD_RUN_PID"; run_status=$?
  SHARD_RUN_PID=""
  if [ "$run_status" = "0" ]; then stage pytest PASS "$(tail -1 "$OUT/pytest.txt")"
  else stage pytest FAIL "$( [ -f "$OUT/pytest.txt" ] && tail -1 "$OUT/pytest.txt" || tail -2 "$OUT/pytest-run.txt" | tr '\n' ' ')"; fi
fi

# --- what the canonical run actually fetched: only manifest bytes, no tamper refusal, nothing outside the frozen set that mattered ---------------------------------
[ -n "$MAVEN_SERVER_PID" ] && kill "$MAVEN_SERVER_PID" 2>/dev/null
[ -n "$PIP_SERVER_PID" ] && kill "$PIP_SERVER_PID" 2>/dev/null
if [ "$MAVEN_REPO_OK" != "1" ]; then
  stage maven_served UNAVAILABLE "no frozen repository was served (stage maven_repository)"
elif "$VENV_BIN/python" - "$OUT" "$MANIFEST" <<'PY'
import collections, json, os, sys
out, manifest = sys.argv[1], json.load(open(sys.argv[2]))
rows = [json.loads(l) for l in open(f"{out}/maven-repo-served.jsonl")] if os.path.exists(f"{out}/maven-repo-served.jsonl") else []
counts = collections.Counter(r["status"] for r in rows)
json.dump({"manifest_content_digest": manifest["content_digest"], "files": len(manifest["files"]), "attested": manifest.get("attested"), "requests": len(rows),
           "status_counts": dict(counts), "bytes_differ_refusals": counts.get(409, 0),
           "not_in_manifest_requests": sorted({r["path"] for r in rows if r["status"] == 404})[:200],
           "served_paths": len({r["path"] for r in rows if r["status"] == 200})}, open(f"{out}/maven-repo.json", "w"), indent=2, sort_keys=True)
sys.exit(1 if counts.get(409, 0) else 0)
PY
then
  stage maven_served PASS "$("$VENV_BIN/python" -c 'import json, sys; d = json.load(open(sys.argv[1])); print(d["requests"], "requests,", d["served_paths"], "distinct files, 0 bytes-differ refusals; statuses", d["status_counts"])' "$OUT/maven-repo.json")"
else
  stage maven_served FAIL "the frozen repository refused to serve altered bytes during the run (see maven-repo.json)"
fi

# --- what the contained pip tests fetched: hash-listed wheelhouse files only, no tamper refusal ------------------------------------------------------------------
if [ "$PY_WHEELS_OK" != "1" ]; then
  stage python_served UNAVAILABLE "no frozen wheelhouse index was served (stage python_wheelhouse)"
elif "$VENV_BIN/python" - "$OUT" <<'PY'
import collections, json, os, sys
out = sys.argv[1]
rows = [json.loads(l) for l in open(f"{out}/python-index-served.jsonl")] if os.path.exists(f"{out}/python-index-served.jsonl") else []
counts = collections.Counter(r["status"] for r in rows)
json.dump({"requests": len(rows), "status_counts": dict(counts), "bytes_differ_refusals": counts.get(409, 0),
           "not_listed_requests": sorted({r["path"] for r in rows if r["status"] == 404})[:100],
           "files_served": sorted({r["path"].rsplit("/", 1)[-1] for r in rows if r["status"] == 200 and r["path"].startswith("/packages/")})}, open(f"{out}/python-index.json", "w"), indent=2, sort_keys=True)
sys.exit(1 if counts.get(409, 0) else 0)
PY
then
  stage python_served PASS "$("$VENV_BIN/python" -c 'import json, sys; d = json.load(open(sys.argv[1])); print(d["requests"], "requests,", len(d["files_served"]), "distinct files, 0 bytes-differ refusals; statuses", d["status_counts"])' "$OUT/python-index.json")"
else
  stage python_served FAIL "the frozen wheelhouse refused to serve altered bytes during the run (see python-index.json)"
fi

# --- the canonical run built no image (no live apt-get happened inside it) -------------------------------------------------------------------------------------
if [ "$IMAGES_OK" != "1" ]; then
  stage images_unchanged UNAVAILABLE "no image record (stage product_images)"
elif "$VENV_BIN/python" scripts/provision_containment_images.py check "$OUT/product-images.json" > "$OUT/product-images-after.txt" 2>&1; then
  stage images_unchanged PASS "no product image was built or rebuilt during the run"
else
  stage images_unchanged FAIL "$(tail -3 "$OUT/product-images-after.txt" | tr '\n' ' ')"
fi

# --- every mandatory tier executed (junit), and the run's population is the audited one ---------
if [ -f "$OUT/pytest/junit.xml" ] && [ -f "$OUT/pytest/collection-census.json" ]; then
  "$VENV_BIN/python" - "$OUT" <<'PY'
import json, sys
out = sys.argv[1]
audited = {t["id"] for t in json.load(open(f"{out}/collection/selected.json"))["tests"]} if __import__("os").path.exists(f"{out}/collection/selected.json") else set()
ran = {t["id"] for t in json.load(open(f"{out}/pytest/collection-census.json"))["tests"]}
json.dump({"audited": len(audited), "ran": len(ran), "same": audited == ran,
           "only_audited": sorted(audited - ran)[:20], "only_ran": sorted(ran - audited)[:20]}, open(f"{out}/population.json", "w"), indent=2)
sys.exit(0 if audited and audited == ran else 1)
PY
  population_status=$?
  if [ "$population_status" = "0" ] && "$VENV_BIN/python" tests/_certification_collection.py executed "$OUT/pytest/collection-census.json" "$OUT/pytest/junit.xml" "$OUT/tier-execution.json" > "$OUT/tier-execution.txt" 2>&1; then
    stage tier_execution PASS "$(head -1 "$OUT/tier-execution.txt")"
  else
    stage tier_execution FAIL "population identical: $([ "$population_status" = "0" ] && echo yes || echo NO); $(head -2 "$OUT/tier-execution.txt" 2>/dev/null | tr '\n' ' ')"
  fi
else
  stage tier_execution UNAVAILABLE "the pytest stage produced no junit/census"
fi

# --- pinned scanner: exact version, and its tier PASSED in the canonical run (no second pytest run) --
if [ "$SEMGREP_VERSION" != "1.178.0" ]; then
  stage scanner UNAVAILABLE "semgrep 1.178.0 not on PATH (found: ${SEMGREP_VERSION:-none})"
elif "$VENV_BIN/python" -c 'import json,sys; r=json.load(open(sys.argv[1]))["per_tier"]["live_static_analysis"]; sys.exit(0 if r["passed"]>=11 and not (r["skipped"] or r["failed"] or r["missing"]) else 1)' "$OUT/tier-execution.json" 2>/dev/null; then
  stage scanner PASS "semgrep $SEMGREP_VERSION; live_static_analysis tier passed in the canonical run"
else
  stage scanner FAIL "live_static_analysis tier did not fully pass in the canonical run (see tier-execution.json)"
fi

# --- pinned images (present with the exact digest; certification never pulls) ----
if "$VENV_BIN/python" scripts/pinned_images.py --verify > "$OUT/images.txt" 2>&1; then
  stage images PASS "$(tail -1 "$OUT/images.txt")"
else
  stage images FAIL "$(grep -m1 -E 'MISSING|UNAVAILABLE' "$OUT/images.txt" || tail -1 "$OUT/images.txt")"
fi

# --- release: sdist/wheel integrity and clean install (PRD-002) ------------------
if [ "$PY_WHEELS_OK" != "1" ]; then
  stage release UNAVAILABLE "no frozen Python wheelhouse (stage python_wheelhouse)"
elif KRIYA_PYTHON="$VENV_BIN/python" bash scripts/verify_release.sh > "$OUT/release.txt" 2>&1; then
  stage release PASS
else
  stage release FAIL "see release.txt"
fi

# --- PRD-033 release thresholds: frozen as used (path, text, digest, loader values) -------------------------
if [ -n "$THRESHOLDS" ]; then
  if "$VENV_BIN/python" scripts/certification_thresholds.py "$THRESHOLDS" "$OUT/thresholds.json" > "$OUT/thresholds.txt" 2>&1; then
    stage thresholds RECORDED "$(head -1 "$OUT/thresholds.txt")"
  else
    stage thresholds FAIL "$(head -1 "$OUT/thresholds.txt")"
  fi
fi

# --- production doctor (recorded as it is, never converted to PASS) -------------------------------
# SEC-009: certification is not the operator. This stage NEVER runs `kriya authority approve`; it reads the canonical approval state.
if [ -n "$DOCTOR_CONFIG" ]; then
  DOCTOR_MODE="operator_config"
  trust_args=()
  [ -n "$DOCTOR_TRUST" ] && trust_args=(--trust-file "$DOCTOR_TRUST")
  ( cd "$DOCTOR_WORKSPACE" && "$VENV_BIN/kriya" --config "$DOCTOR_CONFIG" ${trust_args[@]+"${trust_args[@]}"} doctor --production --json ) \
    > "$OUT/doctor.json" 2> "$OUT/doctor.stderr.txt"
  OWNER_CMD="cd '$DOCTOR_WORKSPACE' && '$VENV_BIN/kriya' --config '$DOCTOR_CONFIG' authority approve${DOCTOR_TRUST:+ --out '$DOCTOR_TRUST'}"
else
  DOCTOR_MODE="packaged_defaults"
  DOCTOR_HOME="$(mktemp -d "${TMPDIR:-/tmp}/kriya-cert-doctor.XXXXXXXX")"  # disposable (removed by cleanup): its outputs are captured under $OUT (LEAK-RELEASE-TMP-001)
  mkdir -p "$DOCTOR_HOME/workspace"
  git -C "$DOCTOR_HOME/workspace" init -q  # a real (empty) repository, as a deployment has
  ( cd "$DOCTOR_HOME/workspace" && "$VENV_BIN/kriya" doctor --production --json ) > "$OUT/doctor.json" 2> "$OUT/doctor.stderr.txt"
  OWNER_CMD=""
fi
printf '{"mode": "%s", "config": "%s", "workspace": "%s", "trust_file": "%s"}\n' "$DOCTOR_MODE" "$DOCTOR_CONFIG" "${DOCTOR_WORKSPACE:-}" "$DOCTOR_TRUST" > "$OUT/doctor-mode.json"
if grep -q "Configuration-authority denied" "$OUT/doctor.json" "$OUT/doctor.stderr.txt" 2>/dev/null; then
  printf '%s\n' "The production doctor was refused by SEC-009: no valid approval for this exact config and workspace." \
    "Owner action (certification never does this for you):" "  $OWNER_CMD" "then run scripts/certify.sh again." > "$OUT/doctor-owner-action.txt"
  stage doctor FAIL "SEC-009 approval absent or stale; see doctor-owner-action.txt"
elif "$VENV_BIN/python" -c 'import json,sys; json.load(open(sys.argv[1]))' "$OUT/doctor.json" 2>/dev/null; then
  stage doctor RECORDED "production doctor JSON archived (mode: $DOCTOR_MODE)"
else
  stage doctor FAIL "doctor produced no parseable JSON"
fi

"$VENV_BIN/python" scripts/certification_summary.py "$OUT"
