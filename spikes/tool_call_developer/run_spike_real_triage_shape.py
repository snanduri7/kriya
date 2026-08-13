"""Re-test (2026-08-13), fidelity follow-on to run_spike_small_arg_triage.py: that
spike's simplified prompt got 5/5 CORRECT json_mode results from gpt-oss:20b,
which does NOT match the real live failure (empty content, `json.loads` ->
"Expecting value: line 1 column 1 (char 0)") seen earlier this session from
kriya/workflow/attribution.py's actual `triage` tier. This script closes that
gap by calling Kriya's REAL LLMClient.complete() - not a hand-rolled httpx
approximation - with the REAL _TRIAGE_SYSTEM_PROMPT, REAL skeletonize_code()
output for multiple candidate files, and the REAL `max_tokens_override=300`
_tier_triage() actually passes.

Leading hypothesis, found by reading complete()'s own code (kriya/core/llm.py):
    base_max_tokens = max_tokens_override if max_tokens_override is not None else self.max_tokens
    max_tokens = max(base_max_tokens, 12288) if is_reasoning else base_max_tokens
The eval harness's llm_chain fallback entry (spikes/eval_harness/run_harness.py's
_write_config) sets "reasoning": False for gpt-oss:20b (comment: "Explicit, fast,
non-reasoning fallback"). Since gpt-oss silently emits its real output through
Ollama's separate "reasoning" API field regardless of what Kriya's config
*believes* about it, `is_reasoning=False` means the 12288-token floor never
applies - the triage call gets exactly 300 tokens to cover whatever gpt-oss
writes into "reasoning" AND its actual JSON "content", combined. If gpt-oss
spends most/all of that budget on the reasoning channel first (observed in the
prior spike: 1006-2292 chars of reasoning text on non-timeout trials, ~250-575
tokens), content could come back empty simply because the budget ran out before
the model ever got to it - not because of any prompt-format incompatibility.

Three conditions against gpt-oss:20b, 3 trials each:
  (a) REAL shape as actually configured live: reasoning_override=False,
      max_tokens_override=300 - expected to reproduce the empty-content failure
      if the hypothesis is right.
  (b) reasoning_override=True, same max_tokens_override=300 - tests whether
      just fixing gpt-oss's config classification (triggering the 12288 floor)
      is sufficient, with no other code change.
  (c) reasoning_override=False, max_tokens_override=2000 - tests whether the
      fix is really just "more budget", independent of the reasoning flag (and
      its <think>-stripping side effect, which doesn't apply to gpt-oss's
      output shape anyway).

Run: .venv/bin/python spikes/tool_call_developer/run_spike_real_triage_shape.py
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kriya.config import load_config  # noqa: E402
from kriya.core.llm import LLMClient  # noqa: E402
from kriya.workflow.attribution import _TRIAGE_SYSTEM_PROMPT  # noqa: E402
from kriya.workflow.context_budget import skeletonize_code  # noqa: E402

MODEL = "gpt-oss:20b"
BASE_URL = "http://localhost:11434/v1"
TRIALS_PER_CONDITION = 3

# Real-shaped multi-file project, same domain as the actual ignite_qpid_protocol
# goal this whole investigation traces back to - enough real code that
# skeletonize_code() produces a genuine multi-file signature skeleton, not a
# toy one-liner.
FILES = {
    "src/main/java/com/example/App.java": """package com.example;

import org.apache.ignite.Ignite;
import org.apache.ignite.Ignition;
import org.apache.ignite.IgniteCache;

public class App {
    public static void main(String[] args) throws Exception {
        try (Ignite ignite = Ignition.start("ignite-config.xml")) {
            CacheConfig cacheConfig = new CacheConfig(ignite);
            var cache = cacheConfig.getProtocolCache();
            Protocol p = new Protocol("client-1", "hello");
            cache.put(p.getId(), p);
            Protocol roundTripped = cache.get(p.getId());
            System.out.println("[RESULT] " + roundTripped);
            if (!roundTripped.equals(p)) {
                System.out.println("[VERIFICATION] FAIL: Data mismatch after round-trip");
            } else {
                System.out.println("[VERIFICATION] PASS");
            }
        }
    }
}
""",
    "src/main/java/com/example/CacheConfig.java": """package com.example;

import org.apache.ignite.Ignite;
import org.apache.ignite.IgniteCache;
import org.apache.ignite.configuration.CacheConfiguration;

public class CacheConfig {
    private final Ignite ignite;

    public CacheConfig(Ignite ignite) {
        this.ignite = ignite;
    }

    public IgniteCache<Object, Object> getProtocolCache() {
        var cache = ignite.getOrCreateCache("protocolCache");
        return cache;
    }
}
""",
    "src/main/java/com/example/Protocol.java": """package com.example;

import java.io.Serializable;
import java.util.Objects;

public class Protocol implements Serializable {
    private final String id;
    private final String payload;

    public Protocol(String id, String payload) {
        this.id = id;
        this.payload = payload;
    }

    public String getId() { return id; }
    public String getPayload() { return payload; }

    @Override
    public boolean equals(Object o) {
        if (!(o instanceof Protocol)) return false;
        Protocol other = (Protocol) o;
        return Objects.equals(id, other.id) && Objects.equals(payload, other.payload);
    }

    @Override
    public String toString() {
        return "Protocol{id='" + id + "', payload='" + payload + "'}";
    }
}
""",
    "src/main/java/com/example/BrokerServer.java": """package com.example;

import org.apache.qpid.server.SystemLauncher;
import java.util.HashMap;
import java.util.Map;

public class BrokerServer {
    private final SystemLauncher launcher = new SystemLauncher();

    public void start() throws Exception {
        Map<String, Object> attrs = new HashMap<>();
        attrs.put("type", "Memory");
        attrs.put("initialConfigurationLocation", "broker-config.json");
        launcher.startup(attrs);
    }

    public void stop() {
        launcher.shutdown();
    }
}
""",
    "pom.xml": """<project>
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.example</groupId>
  <artifactId>ignite-qpid-protocol</artifactId>
  <version>1.0-SNAPSHOT</version>
  <dependencies>
    <dependency>
      <groupId>org.apache.ignite</groupId>
      <artifactId>ignite-core</artifactId>
      <version>2.18.0</version>
    </dependency>
    <dependency>
      <groupId>org.apache.qpid</groupId>
      <artifactId>qpid-broker-core</artifactId>
      <version>9.1.0</version>
    </dependency>
  </dependencies>
</project>
""",
}

# Real-shaped compiler output - same error class as the actual recurring
# ignite_qpid_protocol bug (untyped var cache declaration, use sites reported
# elsewhere), truncated the same way _tier_triage() truncates (raw_text[:4000]).
FAILURE_TEXT = """[INFO] Scanning for projects...
[INFO] ------------------------------------------------------------------
[INFO] Building ignite-qpid-protocol 1.0-SNAPSHOT
[INFO] ------------------------------------------------------------------
[INFO] --- maven-compiler-plugin:3.11.0:compile (default-compile) @ ignite-qpid-protocol ---
[INFO] Compiling 4 source files to /workspace/target/classes
[ERROR] /workspace/src/main/java/com/example/App.java:[15,32] incompatible types: java.lang.Object cannot be converted to com.example.Protocol
[ERROR] /workspace/src/main/java/com/example/App.java:[17,46] incompatible types: java.lang.Object cannot be converted to com.example.Protocol
[INFO] 2 errors
[INFO] -------------------------------------------------------------
[INFO] BUILD FAILURE
[INFO] -------------------------------------------------------------
[ERROR] Failed to execute goal org.apache.maven.plugins:maven-compiler-plugin:3.11.0:compile (default-compile) on project ignite-qpid-protocol: Compilation failure
[ERROR] -> [Help 1]
"""


def build_user_prompt() -> str:
    skeleton_sections = []
    for filepath, content in FILES.items():
        try:
            skeleton = skeletonize_code(content, filepath, "signatures")
        except Exception:
            skeleton = content[:400]
        skeleton_sections.append(f"--- {filepath} ---\n{skeleton}")
    return (
        f"=== Failure ===\n{FAILURE_TEXT[:4000]}\n\n"
        f"=== Candidate files ===\n" + "\n\n".join(skeleton_sections)
    )


async def run_trial(llm: LLMClient, reasoning_override: bool, max_tokens_override: int) -> dict:
    user_prompt = build_user_prompt()
    start = time.monotonic()
    try:
        response = await llm.complete(
            _TRIAGE_SYSTEM_PROMPT, user_prompt,
            json_mode=True,
            model_override=MODEL,
            base_url_override=BASE_URL,
            api_key_override="local-key",
            max_tokens_override=max_tokens_override,
            reasoning_override=reasoning_override,
        )
    except Exception as e:
        return {"outcome": f"complete() RAISED: {e}", "elapsed": time.monotonic() - start, "grade": "N/A"}
    elapsed = time.monotonic() - start

    if not response.strip():
        return {"outcome": "EMPTY response", "elapsed": elapsed, "grade": "matches the real live failure shape"}
    try:
        parsed = json.loads(response)
        files = parsed.get("files", [])
        correct = "src/main/java/com/example/CacheConfig.java" in files and \
                  "src/main/java/com/example/App.java" not in files
        grade = "CORRECT (CacheConfig.java)" if correct else f"files={files}"
        return {"outcome": "parsed OK", "elapsed": elapsed, "grade": grade}
    except json.JSONDecodeError as e:
        return {"outcome": f"UNPARSEABLE: {e}", "elapsed": elapsed, "grade": f"raw: {response[:300]!r}"}


async def main():
    config = load_config()
    llm = LLMClient(config)

    conditions = [
        ("(a) REAL live shape: reasoning=False, max_tokens=300", False, 300),
        ("(b) reasoning=True (triggers 12288 floor), max_tokens=300", True, 300),
        ("(c) reasoning=False, max_tokens=2000 (budget only, no flag change)", False, 2000),
    ]

    for label, reasoning_override, max_tokens_override in conditions:
        print(f"\n{'=' * 78}\n{label}\n{'=' * 78}")
        for i in range(TRIALS_PER_CONDITION):
            r = await run_trial(llm, reasoning_override, max_tokens_override)
            print(f"  [{i+1}] {r['outcome']:<30} {r['elapsed']:.1f}s   grade: {r['grade']}")


if __name__ == "__main__":
    asyncio.run(main())
