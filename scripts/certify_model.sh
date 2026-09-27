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
set -uo pipefail
cd "$(dirname "$0")/.."
OUT="${1:-certification-out/model-$(date -u +%Y%m%dT%H%M%SZ)}"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
export PATH="$PWD/.venv/bin:$PATH"
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
exit $status
