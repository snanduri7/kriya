"""Merge a slice's coverage overrides into the one overrides file the generator reads. A slice entry replaces the
earlier entry of the same row (the earlier text stays in git history).

Usage: python3 merge_overrides.py <main overrides.json> <slice overrides.json>
"""
import json
import sys
from pathlib import Path

main_path, slice_path = Path(sys.argv[1]), Path(sys.argv[2])
merged = json.loads(main_path.read_text())
incoming = json.loads(slice_path.read_text())
replaced = sorted(set(merged) & set(incoming))
merged.update(incoming)
main_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n")
print(f"entries={len(merged)} added={len(incoming) - len(replaced)} replaced={replaced}")
