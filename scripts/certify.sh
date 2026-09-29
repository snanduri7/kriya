#!/usr/bin/env bash
# PRD-034: the canonical production-certification procedure. The CI job
# `production-certification` runs exactly this, and so does the target
# machine; the local run is the executed evidence.
#
#   scripts/certify.sh [OUT_DIR]        (default: certification-out/<UTC stamp>)
#
# Stages, each recorded in OUT_DIR/stages.jsonl:
#   static      ruff + pylint (zero findings)
#   pytest      the deterministic suite in certification mode (every
#               Docker/JDK/Maven test must execute; an unexpected skip fails)
#   images      every digest-pinned image present with exactly its digest (never pulled here)
#   scanner     the pinned static-analysis tier (Semgrep 1.178.0 + the pinned image)
#   release     sdist/wheel build, integrity and clean-install smoke (PRD-002)
#   doctor      `kriya doctor --production --json` - recorded, never turned into PASS
# A stage whose tool is missing is UNAVAILABLE and fails the certification;
# nothing is skipped silently. Refuses to run as root.
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
OUT="${1:-certification-out/$(date -u +%Y%m%dT%H%M%SZ)}"
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

# --- environment identity ---------------------------------------------------
"$VENV_BIN/python" - "$OUT/environment.json" <<'PY'
import json, platform, shutil, subprocess, sys, hashlib
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
    "java": run("java", "-version"), "maven": run("mvn", "-v"), "gradle": run("gradle", "--version"),
    "docker_client": run("docker", "version", "--format", "{{.Client.Version}}"),
    "docker_server": run("docker", "version", "--format", "{{.Server.Version}}"),
    "semgrep": run("semgrep", "--version"), "git": run("git", "--version"),
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

# --- deterministic suite (certification mode) ----------------------------------
missing=""
for tool in docker java mvn; do command -v "$tool" >/dev/null 2>&1 || missing="$missing $tool"; done
if [ -n "$missing" ]; then
  stage pytest UNAVAILABLE "required tools missing:$missing"
elif ! docker info >/dev/null 2>&1; then
  stage pytest UNAVAILABLE "docker daemon not reachable"
else
  if pytest -q -p no:cacheprovider -m "not live_model and not live_static_analysis" --certification \
      --certification-report "$OUT/pytest" --junitxml "$OUT/pytest/junit.xml" > "$OUT/pytest.txt" 2>&1; then
    stage pytest PASS "$(tail -1 "$OUT/pytest.txt")"
  else
    stage pytest FAIL "$(tail -1 "$OUT/pytest.txt")"
  fi
fi

# --- pinned images (present with the exact digest; certification never pulls) ----
if "$VENV_BIN/python" scripts/pinned_images.py --verify > "$OUT/images.txt" 2>&1; then
  stage images PASS "$(tail -1 "$OUT/images.txt")"
else
  stage images FAIL "$(grep -m1 -E 'MISSING|UNAVAILABLE' "$OUT/images.txt" || tail -1 "$OUT/images.txt")"
fi

# --- pinned static-analysis scanner tier ----------------------------------------
if ! command -v semgrep >/dev/null 2>&1; then
  stage scanner UNAVAILABLE "semgrep 1.178.0 not on PATH"
else
  if pytest -q -p no:cacheprovider -m live_static_analysis --certification \
      --certification-report "$OUT/scanner" --junitxml "$OUT/scanner/junit.xml" > "$OUT/scanner.txt" 2>&1; then
    stage scanner PASS "$(tail -1 "$OUT/scanner.txt")"
  else
    stage scanner FAIL "$(tail -1 "$OUT/scanner.txt")"
  fi
fi

# --- release: sdist/wheel integrity and clean install (PRD-002) ------------------
if KRIYA_PYTHON="$VENV_BIN/python" bash scripts/verify_release.sh > "$OUT/release.txt" 2>&1; then
  stage release PASS
else
  stage release FAIL "see release.txt"
fi

# --- production doctor (recorded as it is, never converted to PASS) -------------
# The canonical production configuration is the certifying operator's own:
# it is approved explicitly (SEC-009 P2, bound to its exact security fields)
# into a trust file outside any workspace, exactly as an operator deploys it.
DOCTOR_HOME="$(mktemp -d "${TMPDIR:-/tmp}/kriya-cert-doctor.XXXXXXXX")"
trap 'rm -rf "$DOCTOR_HOME"' EXIT  # disposable: its outputs are captured under $OUT (LEAK-RELEASE-TMP-001)
mkdir -p "$DOCTOR_HOME/operator" "$DOCTOR_HOME/workspace"
git -C "$DOCTOR_HOME/workspace" init -q  # a real (empty) repository, as a deployment has
printf 'runtime_profile: production\n' > "$DOCTOR_HOME/operator/production.yaml"
( cd "$DOCTOR_HOME/workspace" && "$VENV_BIN/kriya" --config "$DOCTOR_HOME/operator/production.yaml" authority approve \
    --out "$DOCTOR_HOME/operator/production.trust.json" --confirm ) > "$OUT/doctor-approval.txt" 2>&1
( cd "$DOCTOR_HOME/workspace" && "$VENV_BIN/kriya" --config "$DOCTOR_HOME/operator/production.yaml" \
    --trust-file "$DOCTOR_HOME/operator/production.trust.json" doctor --production --json ) \
  > "$OUT/doctor.json" 2> "$OUT/doctor.stderr.txt"
if "$VENV_BIN/python" -c 'import json,sys; json.load(open(sys.argv[1]))' "$OUT/doctor.json" 2>/dev/null; then
  stage doctor RECORDED "production doctor JSON archived"
else
  stage doctor FAIL "doctor produced no parseable JSON"
fi

"$VENV_BIN/python" scripts/certification_summary.py "$OUT"
