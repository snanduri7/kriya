"""Focused test (2026-08-03): does routing a Developer RETRY specifically
through a genuinely reasoning-capable model (qwen3.6:35b, reasoning_override=
True, real <think> output) produce a better fix than Kriya's current default
(qwen3-coder:30b, reasoning=false, the FIX ANALYSIS prompting workaround)?

Uses a REAL failure Kriya's own current mechanism actually got wrong live:
during a real M3 validation run, the Developer picked a nonexistent JMS
dependency (javax.jms:jms:jar:1.1), then on retry swapped to a DIFFERENT
nonexistent one (jakarta.jms:jakarta.jms-api:jar:2.0.1), then on a second
retry swapped BACK to the original wrong one - oscillating between two wrong
answers across 3 attempts, never converging (see
kriya-validation-ignite-qpid-person/run_m3_attempt1_failed_dilution_bug.log).

This is a good test case specifically because it's a "does the model actually
know/reason about the real Maven coordinate" problem, not a code-structure
problem - exactly the kind of thing where genuine deliberation (vs. a single
forward pass that just orders "analysis, then code") might plausibly help.

Ground truth check is OBJECTIVE, not vibes: does the chosen groupId:artifactId
:version actually resolve on Maven Central (a real HTTP HEAD request), not
just "does it look plausible."

Run: .venv/bin/python spikes/tool_call_developer/run_spike_reasoning_on_retry.py
"""
import re
import time

import httpx

OLLAMA_URL = "http://localhost:11434/v1/chat/completions"

REAL_ERROR = (
    "[ERROR] Failed to execute goal on project kriya-validation-ignite-qpid-person: "
    "Could not resolve dependencies for project com.example:kriya-validation-ignite-qpid-person:jar:1.0-SNAPSHOT\n"
    "[ERROR] dependency: javax.jms:jms:jar:1.1 (compile)\n"
    "[ERROR] \tCould not find artifact javax.jms:jms:jar:1.1 in central "
    "(https://repo.maven.apache.org/maven2), try downloading from http://java.sun.com/products/jms/docs.html"
)

POM_SNIPPET = """<dependencies>
    <dependency>
        <groupId>javax.jms</groupId>
        <artifactId>jms</artifactId>
        <version>1.1</version>
    </dependency>
    <dependency>
        <groupId>org.apache.qpid</groupId>
        <artifactId>qpid-jms-client</artifactId>
        <version>2.5.0</version>
    </dependency>
</dependencies>"""

# Same shape as DeveloperAgent._fill_missing_content's actual fix-analysis prompt.
SYSTEM_PROMPT = (
    "You are the Kriya Developer Agent.\n"
    "Your task is to write the complete, production-grade source code for the requested file path, "
    "and ONLY that one file. Return ONLY the raw file content for that single file. Do not include "
    "markdown code block wrappers, conversational explanation, or the content of any other file."
)

FILE_PROMPT = f"""=== Task ===
Extend a Maven project that uses the Apache Qpid JMS client (org.apache.qpid:qpid-jms-client)
to send/receive JMS messages over AMQP. The current pom.xml is below.

=== Current pom.xml ===
{POM_SNIPPET}

This is a RETRY: the previous attempt at this file failed the error described below.
Before writing any code, you MUST first write a line "FIX ANALYSIS:" followed by 1-3
sentences identifying the SPECIFIC cause of that error and exactly what you are changing
to address it. Only after that analysis, write the line "FILE CONTENT:" on its own line,
followed by the complete file content and nothing else after it.

=== Error ===
{REAL_ERROR}

Please generate the complete, correct pom.xml with a WORKING, real, resolvable JMS API
dependency that will actually be found on Maven Central.
"""

COORD_PATTERN = re.compile(
    r"<groupId>([\w.\-]+)</groupId>\s*<artifactId>([\w.\-]+)</artifactId>\s*<version>([\w.\-]+)</version>",
    re.DOTALL,
)


def _post(model: str, reasoning: bool) -> tuple[str, float]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": FILE_PROMPT},
        ],
    }
    start = time.monotonic()
    resp = httpx.post(OLLAMA_URL, json=payload, timeout=300)
    elapsed = time.monotonic() - start
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"] or ""
    if reasoning:
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    return content, elapsed


def _check_maven_central(group_id: str, artifact_id: str, version: str) -> str:
    group_path = group_id.replace(".", "/")
    url = f"https://repo.maven.apache.org/maven2/{group_path}/{artifact_id}/{version}/{artifact_id}-{version}.pom"
    try:
        resp = httpx.head(url, timeout=15, follow_redirects=True)
        return f"HTTP {resp.status_code} - {'EXISTS on Maven Central' if resp.status_code == 200 else 'does NOT exist'}"
    except Exception as e:
        return f"check failed: {e}"


def run(model: str, reasoning: bool):
    print(f"\n{'=' * 70}\nMODEL: {model} (reasoning={reasoning})\n{'=' * 70}")
    try:
        content, elapsed = _post(model, reasoning)
    except Exception as e:
        print(f"  REQUEST ERROR: {e}")
        return
    print(f"  elapsed: {elapsed:.2f}s")

    analysis_match = re.search(r"FIX ANALYSIS:(.*?)FILE CONTENT:", content, re.DOTALL | re.IGNORECASE)
    print(f"  complied with FIX ANALYSIS / FILE CONTENT format: {bool(analysis_match)}")
    if analysis_match:
        print(f"  analysis: {analysis_match.group(1).strip()[:300]}")

    jms_deps = [
        (g, a, v) for (g, a, v) in COORD_PATTERN.findall(content)
        if "jms" in a.lower() or "jms" in g.lower()
    ]
    if not jms_deps:
        print("  NO JMS-related dependency coordinate found in output at all")
        print(f"  raw content (first 400 chars): {content[:400]!r}")
        return
    for g, a, v in jms_deps:
        coord = f"{g}:{a}:{v}"
        print(f"  chosen JMS coordinate: {coord}")
        print(f"    -> {_check_maven_central(g, a, v)}")


def main():
    # Kriya's actual current default: no reasoning, the fix-analysis prompting workaround only.
    run("qwen3-coder:30b", reasoning=False)
    # The candidate: a genuinely reasoning-capable local model, real <think> output.
    run("qwen3.6:35b", reasoning=True)


if __name__ == "__main__":
    main()
