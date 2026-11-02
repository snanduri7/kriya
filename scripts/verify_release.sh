#!/usr/bin/env bash
# User/CI command: builds an sdist and its wheel, then tests a fresh install.
set -euo pipefail
cd "$(dirname "$0")/.."
release_root="$PWD"
python_bin="${KRIYA_PYTHON:-python3}"
release_work="$(mktemp -d "${TMPDIR:-/tmp}/kriya-release.XXXXXXXX")"
echo "Release evidence and disposable environment: $release_work"
# The ~200 MB install venv is disposable; the evidence (dist/, build.txt, integrity.jsonl, smoke.txt,
# all also on stdout) stays. Without this every run left the whole venv in TMPDIR (LEAK-RELEASE-TMP-001).
trap 'rm -rf "$release_work/venv"' EXIT
KRIYA_BUILD_REQUIRE_CLEAN=1 "$python_bin" -m build --no-isolation --outdir "$release_work/dist" 2>&1 | tee "$release_work/build.txt"
for artifact in "$release_work"/dist/*.whl; do
  "$python_bin" -m kriya.distribution "$artifact" --source-root "$release_root" \
    | tee -a "$release_work/integrity.jsonl"
done
for artifact in "$release_work"/dist/*.tar.gz; do
  "$python_bin" -m kriya.distribution "$artifact" --source-root "$release_root" \
    | tee -a "$release_work/integrity.jsonl"
done
"$python_bin" -m venv "$release_work/venv"
if [ -n "${KRIYA_RELEASE_WHEELHOUSE:-}" ]; then
  # PRD-034 (PYPI): the canonical certification certifies PACKAGING, not PyPI. A fresh venv installs the locked dependencies from the frozen, hash-verified wheelhouse:
  # no index, no fallback (scripts/certification_python_wheels.py; the hashed lock is the content authority). Unset = the ordinary online install below.
  "$release_work/venv/bin/python" -m pip install --no-index --find-links "$KRIYA_RELEASE_WHEELHOUSE" --require-hashes --no-deps \
      -r "${KRIYA_RELEASE_HASHED_REQUIREMENTS:?KRIYA_RELEASE_HASHED_REQUIREMENTS must name the hashed lock}"
else
  "$release_work/venv/bin/python" -m pip install -r requirements.txt
fi
"$release_work/venv/bin/python" -m pip install --no-deps "$release_work"/dist/*.whl
"$release_work/venv/bin/python" -m pip check
cd "$release_work"
"$release_work/venv/bin/python" "$release_root/scripts/release_smoke.py" 2>&1 | tee "$release_work/smoke.txt"
