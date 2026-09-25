"""PRD-012: every outbound-network client Kriya constructs is inventoried
with the trusted authority that governs it (ungoverned sites = 0).

A new network client anywhere in kriya/ or plugins/ fails this test until it
is added here WITH its governing authority - the same structural-inventory
discipline TOOL-001 used for tool execution. Subprocess-mediated network
(containers, package managers, git) is governed by ContainmentProfile network
authority and ShellTool/ExecutionPolicy; GitTool exposes no remote
subcommand (status/diff/log/branch/blame/commit only)."""
import ast
import os
from collections import Counter

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (file, client) -> (count, governing authority)
INVENTORY = {
    ("kriya/core/llm.py", "AsyncOpenAI"): (
        3, "autonomy.egress_policy: LLMClient.complete/complete_with_tools refuse a non-local "
           "endpoint under local_only (EgressViolationError); endpoints are SEC-009 config"),
    ("kriya/memory/vector.py", "httpx.AsyncClient"): (
        2, "autonomy.egress_policy: OllamaEmbeddingClient._enforce_egress before any request "
           "(egress_policy is a required constructor argument)"),
    ("kriya/tools/search.py", "httpx.AsyncClient"): (
        1, "autonomy.web_lookup_enabled + search.base_url + per-query approval of sanitized public terms"),
    ("kriya/tools/web.py", "httpx.AsyncClient"): (
        1, "fetch_url_text: public-address SSRF guard on every hop; callers are live lookup (above), "
           "an operator URL under a non-local_only policy, or the operator's own `kriya learn -u`"),
    ("kriya/tools/resolver.py", "httpx.Client"): (
        1, "allow_external_lookup = egress_policy != local_only AND web_lookup_enabled (every caller)"),
    ("kriya/tools/knowledge.py", "httpx.Client"): (
        7, "knowledge.offline_mode (production forces true); fixed platform REGISTRY_METADATA_HOSTS"),
    ("kriya/tools/service_runtime.py", "HTTPConnection"): (
        1, "probe of the managed service Kriya itself launched (loopback or inside its container)"),
    ("kriya/tools/service_runtime.py", "urlopen"): (
        1, "probe of the managed service Kriya itself launched (loopback or inside its container)"),
    ("kriya/production_doctor.py", "urlopen"): (
        1, "doctor probe of the configured llm/embedding endpoints (SEC-009 config)"),
    ("kriya/cli.py", "urlopen"): (
        1, "`kriya doctor` connectivity check of llm.base_url, refused when not local under local_only"),
}


def _network_clients():
    found = Counter()
    for root in ("kriya", "plugins"):
        for dirpath, _dirs, files in os.walk(os.path.join(REPO_ROOT, root)):
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(dirpath, name)
                rel = os.path.relpath(path, REPO_ROOT)
                for node in ast.walk(ast.parse(open(path, encoding="utf-8").read())):
                    if not isinstance(node, ast.Call):
                        continue
                    func = node.func
                    attr = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                    base = func.value.id if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) else None
                    if base == "httpx" and attr in ("Client", "AsyncClient"):
                        found[(rel, f"httpx.{attr}")] += 1
                    elif attr in ("AsyncOpenAI", "OpenAI", "urlopen", "HTTPConnection", "HTTPSConnection"):
                        found[(rel, attr)] += 1
                    elif base == "socket" and attr == "create_connection":
                        found[(rel, "socket.create_connection")] += 1
                    elif base in ("requests", "aiohttp", "urllib3"):
                        found[(rel, f"{base}.{attr}")] += 1
    return found


def test_every_network_client_is_inventoried_with_its_authority():
    found = _network_clients()
    expected = {key: count for key, (count, _authority) in INVENTORY.items()}
    unexpected = {key: count for key, count in found.items() if expected.get(key) != count}
    missing = {key: count for key, count in expected.items() if found.get(key) != count}
    assert not unexpected and not missing, (
        f"network clients changed - classify each with its governing authority in INVENTORY. "
        f"found-but-not-inventoried={unexpected}, inventoried-but-not-found={missing}"
    )
    assert all(authority.strip() for _count, authority in INVENTORY.values())


def test_the_inventory_scan_detects_a_new_client(tmp_path, monkeypatch):
    probe = tmp_path / "kriya" / "rogue.py"
    probe.parent.mkdir()
    probe.write_text("import httpx\nhttpx.Client()\n")
    monkeypatch.setattr(__import__(__name__), "REPO_ROOT", str(tmp_path))
    assert _network_clients()[("kriya/rogue.py", "httpx.Client")] == 1
