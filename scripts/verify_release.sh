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
"$python_bin" -m build --no-isolation --outdir "$release_work/dist" 2>&1 | tee "$release_work/build.txt"
for artifact in "$release_work"/dist/*.whl; do
  "$python_bin" -m kriya.distribution "$artifact" --source-root "$release_root" \
    | tee -a "$release_work/integrity.jsonl"
done
for artifact in "$release_work"/dist/*.tar.gz; do
  "$python_bin" -m kriya.distribution "$artifact" --source-root "$release_root" \
    | tee -a "$release_work/integrity.jsonl"
done
"$python_bin" -m venv "$release_work/venv"
"$release_work/venv/bin/python" -m pip install -r requirements.txt
"$release_work/venv/bin/python" -m pip install --no-deps "$release_work"/dist/*.whl
"$release_work/venv/bin/python" -m pip check
cd "$release_work"
"$release_work/venv/bin/python" "$release_root/scripts/release_smoke.py" 2>&1 | tee "$release_work/smoke.txt"
