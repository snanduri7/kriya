"""Read-only: extract the issue's three C# files with the engine under sys.argv[1]; print skips and calls edges."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from graphify.extract import extract  # noqa: E402

FILES = {
    "Settings.cs": "namespace Demo\n{\n    public class Settings\n    {\n        public T Get<T>(string key) { return default(T); }\n        public string GetRaw(string key) { return key; }\n    }\n}\n",
    "Reader.cs": "namespace Demo\n{\n    public class Reader : Settings\n    {\n        public int A() { return Get<int>(\"port\"); }\n        public int B() { return this.Get<int>(\"port\"); }\n        public string C() { return GetRaw(\"host\"); }\n        public string D() { return this.GetRaw(\"host\"); }\n    }\n}\n",
    "Local.cs": "namespace Demo\n{\n    public class Local\n    {\n        public T Make<T>() { return default(T); }\n        public int E() { return Make<int>(); }\n    }\n}\n",
}
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    for name, body in FILES.items():
        (root / name).write_text(body)
    old = os.getcwd()
    os.chdir(root)
    try:
        result = extract([Path(n) for n in FILES], cache_root=root / ".cache", root=root)
    finally:
        os.chdir(old)
labels = {n["id"]: n["label"] for n in result["nodes"]}
print("calls edges:", sorted((labels.get(e["source"]), labels.get(e["target"])) for e in result["edges"] if e["relation"] == "calls"))
