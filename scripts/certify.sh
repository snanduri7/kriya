#!/usr/bin/env bash
# PRD-034: the canonical production-certification procedure. The CI job
# `production-certification` runs exactly this, and so does the target
# machine; the local run is the executed evidence.
#
#   scripts/certify.sh [--thresholds FILE] [--doctor-config FILE --doctor-workspace DIR [--doctor-trust-file FILE]] [OUT_DIR]
#                                       (OUT_DIR default: certification-out/<UTC stamp>; pass one OUTSIDE the work tree for a canonical run)
#
# Stages, each recorded in OUT_DIR/stages.jsonl:
#   static        ruff + pylint (zero findings)
#   collection    collection/deselection audit: the canonical population is exactly `not live_model`, every deselected test is live_model,
#                 no real_toolchain / live_static_analysis / Docker / JVM / Gradle-container test disappeared (tests/_certification_collection.py)
#   pytest        THE canonical population, literally `pytest -o addopts= -m "not live_model"` in certification mode (an unexpected skip fails)
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
THRESHOLDS=""; DOCTOR_CONFIG=""; DOCTOR_WORKSPACE=""; DOCTOR_TRUST=""; OUT_ARG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --thresholds) THRESHOLDS="${2:?--thresholds needs a file}"; shift 2 ;;
    --doctor-config) DOCTOR_CONFIG="${2:?--doctor-config needs a file}"; shift 2 ;;
    --doctor-workspace) DOCTOR_WORKSPACE="${2:?--doctor-workspace needs a directory}"; shift 2 ;;
    --doctor-trust-file) DOCTOR_TRUST="${2:?--doctor-trust-file needs a file}"; shift 2 ;;
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

# --- collection truth (before the long run) -------------------------------------
mkdir -p "$OUT/collection" "$OUT/pytest"
if pytest -o addopts= --collect-only -q -p no:cacheprovider --collection-census="$OUT/collection/all.json" > "$OUT/collection/all.txt" 2>&1 \
   && pytest -o addopts= -m "not live_model" --collect-only -q -p no:cacheprovider --collection-census="$OUT/collection/selected.json" > "$OUT/collection/selected.txt" 2>&1 \
   && "$VENV_BIN/python" tests/_certification_collection.py audit "$OUT/collection/all.json" "$OUT/collection/selected.json" "$OUT/collection" > "$OUT/collection/audit.txt" 2>&1; then
  stage collection PASS "$(head -1 "$OUT/collection/audit.txt")"
else
  stage collection FAIL "$(head -3 "$OUT/collection/audit.txt" 2>/dev/null | tr '\n' ' ')"
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
else
  printf '%s\n' "pytest -o addopts= -m \"not live_model\" -p no:cacheprovider -q --certification --certification-report=$OUT/pytest --collection-census=$OUT/pytest/collection-census.json --junitxml=$OUT/pytest/junit.xml --durations=50" > "$OUT/pytest/command.txt"
  if pytest -o addopts= -m "not live_model" -p no:cacheprovider -q --certification --certification-report="$OUT/pytest" \
      --collection-census="$OUT/pytest/collection-census.json" --junitxml="$OUT/pytest/junit.xml" --durations=50 > "$OUT/pytest.txt" 2>&1; then
    stage pytest PASS "$(tail -1 "$OUT/pytest.txt")"
  else
    stage pytest FAIL "$(tail -1 "$OUT/pytest.txt")"
  fi
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
if KRIYA_PYTHON="$VENV_BIN/python" bash scripts/verify_release.sh > "$OUT/release.txt" 2>&1; then
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
  DOCTOR_HOME="$(mktemp -d "${TMPDIR:-/tmp}/kriya-cert-doctor.XXXXXXXX")"
  trap 'rm -rf "$DOCTOR_HOME"' EXIT  # disposable: its outputs are captured under $OUT (LEAK-RELEASE-TMP-001)
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
