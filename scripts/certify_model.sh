#!/usr/bin/env bash
# PRD-035: run the live local-model certification matrix on the target
# machine and produce the raw test result, the JSON certification report
# and its Markdown summary. On the qualified target identity it also stores
# the certification record (~/.kriya/certifications/, KRIYA_CERTIFICATION_HOME),
# CERTIFIED only when every case passed; `kriya model certification` then
# reports it CURRENT until the runtime, settings, environment or case set change.
#
#   scripts/certify_model.sh [OUT_DIR]
#
# Endpoint and model: KRIYA_LIVE_BASE_URL (default http://localhost:11434/v1),
# KRIYA_LIVE_LLM_MODEL (default qwen3-coder:30b), KRIYA_LIVE_FALLBACK_MODEL
# (C6; default the demo-03 qualified qwen3.6 identity). Needs Docker (C7) and
# Semgrep 1.178.0 (C11). Never run by CI (marker live_certification).
#
# PRD-036 release trial: with KRIYA_RELEASE_CANDIDATE (the frozen identity from
# `scripts/release_candidate.py identity`) plus KRIYA_RELEASE_CONFIG,
# KRIYA_RELEASE_TRUST_FILE and KRIYA_RELEASE_WORKSPACE, the matrix runs only if
# the frozen candidate is still CURRENT, takes its model bindings from the
# release config, and is appended to the candidate's streak log as one trial
# (identity re-taken at the end; a change mid-trial fails it).
set -uo pipefail
cd "$(dirname "$0")/.."
OUT="${1:-certification-out/model-$(date -u +%Y%m%dT%H%M%SZ)}"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
export PATH="$PWD/.venv/bin:$PATH"
if [ -n "${KRIYA_RELEASE_CANDIDATE:-}" ]; then
  if ! .venv/bin/python scripts/release_candidate.py check --recorded "$KRIYA_RELEASE_CANDIDATE" > "$OUT/candidate-check.json"; then
    cat "$OUT/candidate-check.json"
    echo "[certify-model] the frozen release candidate is not CURRENT: no trial run" >&2
    exit 2
  fi
  cp "$KRIYA_RELEASE_CANDIDATE" "$OUT/release-candidate.json"
  STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
fi
.venv/bin/pytest -m live_certification -p no:cacheprovider -ra tests/test_live_prd035_certification.py \
  --junitxml "$OUT/junit.xml" --model-certification "$OUT" 2>&1 | tee "$OUT/pytest.txt"
status=${PIPESTATUS[0]}
.venv/bin/python - "$OUT" <<'PY'
import json, sys
from kriya.core.model_certification import CASE_SET_VERSION, CertificationKey, save_certification
report = json.load(open(f"{sys.argv[1]}/model-certification.json"))
content = report["content"]
identity = content.get("identity") or {}
if content["tier"] != "target_production":
    print(f"[certify-model] tier {content['tier']}: no certification record stored")
    sys.exit(0)
key = CertificationKey(model=identity["model"], runtime_digest=identity["runtime_fingerprint"],
                       inference_settings_digest=identity["inference_settings_digest"],
                       environment_digest=content["execution_environment"]["digest"],
                       case_set_version=CASE_SET_VERSION)
print(f"[certify-model] {content['status']}: record {save_certification(key, report)}")
PY
if [ -n "${KRIYA_RELEASE_CANDIDATE:-}" ]; then
  ENDED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  .venv/bin/python scripts/release_candidate.py identity --out "$OUT/release-candidate-after.json"
  .venv/bin/python scripts/release_candidate.py record-trial --candidate "$KRIYA_RELEASE_CANDIDATE" \
    --after "$OUT/release-candidate-after.json" --report "$OUT/model-certification.json" \
    --started "$STARTED" --ended "$ENDED" --evidence "$OUT" | tee "$OUT/release-trial.json"
  trial=${PIPESTATUS[0]}
  .venv/bin/python scripts/release_candidate.py status --candidate "$KRIYA_RELEASE_CANDIDATE" | tee "$OUT/release-streak.json"
  [ "$trial" -eq 0 ] || status=1
fi
exit $status
