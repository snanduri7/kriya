"""GR-R1 Graphify context-discrimination replay (owner-authorized experiment; NOT a Kriya change, NOT a Graphify run).

A = the sealed attempt-2 Developer wire body of run 20261006T204410-5388017f, byte for byte.
B = A plus ONE repository-evidence window: frozen engine.py lines 215-232 (the `generic_name` block of
    `_csharp_collect_type_refs`), rendered in Kriya's own exact-source window format, inserted in line order
    before the first window. Nothing else differs (checked before any call).

Exactly one streaming /api/chat call per input, A then B, independent; no retry, no fallback.

usage: python replay_ab.py build <wire_body.json> <base_engine.py> <out_dir>
       python replay_ab.py call <out_dir> <A|B>
"""
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

PATH = "graphify/extractors/engine.py"
START, END = 215, 232
FIRST_HEADER = ("=== EXACT CURRENT SOURCE (authoritative, byte-exact - copy SEARCH text only from here): "
                f"{PATH} lines 5356-5370 [window] ===")
ENDPOINT = "http://localhost:11434/api/chat"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build(wire_path: str, engine_path: str, out: str) -> None:
    a = Path(wire_path).read_bytes()
    lines = Path(engine_path).read_text(encoding="utf-8").splitlines(keepends=True)
    source = "".join(lines[START - 1:END])
    section = (f"=== EXACT CURRENT SOURCE (authoritative, byte-exact - copy SEARCH text only from here): "
               f"{PATH} lines {START}-{END} [block] ===\n{source}"
               f"=== END EXACT SOURCE {PATH} lines {START}-{END} ===\n\n\n")
    anchor = json.dumps(FIRST_HEADER)[1:-1].encode()
    assert a.count(anchor) == 1, "first window header must occur exactly once in the wire body"
    insert = json.dumps(section)[1:-1].encode()
    b = a.replace(anchor, insert + anchor, 1)
    doc_a, doc_b = json.loads(a), json.loads(b)
    user_a, user_b = doc_a["messages"][-1]["content"], doc_b["messages"][-1]["content"]
    position = user_a.index(FIRST_HEADER)
    assert user_b == user_a[:position] + section + user_a[position:], "B must be A plus exactly the section"
    assert {k: v for k, v in doc_a.items() if k != "messages"} == {k: v for k, v in doc_b.items() if k != "messages"}
    assert doc_a["messages"][:-1] == doc_b["messages"][:-1]
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=False)
    (out_dir / "input_A.json").write_bytes(a)
    (out_dir / "input_B.json").write_bytes(b)
    (out_dir / "added_section.txt").write_text(section, encoding="utf-8")
    manifest = {"input_A_sha256": sha(a), "input_B_sha256": sha(b), "added_section_sha256": sha(section.encode()),
                "added_source_span": f"{PATH} lines {START}-{END}", "added_source_sha256": sha(source.encode()),
                "base_engine_sha256": sha(Path(engine_path).read_bytes()), "added_chars": len(section),
                "settings": {k: v for k, v in doc_a.items() if k != "messages"}}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(json.dumps(manifest, indent=1))


def call(out: str, arm: str) -> None:
    out_dir = Path(out)
    raw_path = out_dir / f"response_{arm}.stream.jsonl"
    if raw_path.exists():
        raise SystemExit(f"{raw_path} exists: exactly one call per input")
    body = (out_dir / f"input_{arm}.json").read_bytes()
    request = urllib.request.Request(ENDPOINT, data=body, headers={"Content-Type": "application/json"})
    started = time.time()
    chunks, final = [], {}
    with urllib.request.urlopen(request, timeout=900) as response, open(raw_path, "wb") as raw:
        for line in response:
            raw.write(line)
            if not line.strip():
                continue
            event = json.loads(line)
            chunks.append((event.get("message") or {}).get("content", ""))
            if event.get("done"):
                final = event
    content = "".join(chunks)
    (out_dir / f"response_{arm}.txt").write_text(content, encoding="utf-8")
    summary = {"arm": arm, "wall_seconds": round(time.time() - started, 2), "content_sha256": sha(content.encode()),
               "done_reason": final.get("done_reason"), "prompt_eval_count": final.get("prompt_eval_count"),
               "eval_count": final.get("eval_count"), "model": final.get("model")}
    (out_dir / f"response_{arm}.summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary))


if __name__ == "__main__":
    if sys.argv[1] == "build":
        build(*sys.argv[2:5])
    else:
        call(*sys.argv[2:4])
