"""POC: incrementally COMBINES the 4 previously-isolated tasks
(../01_wire_format_roundtrip, ../02_ignite_resource_lifecycle,
../03_jms_bytes_message_api, ../04_cache_generics_typing) into ONE growing
goal, mirroring the real ignite_qpid_protocol goal's own Layer 1/2/3
structure, to directly measure WHEN goal density starts degrading a
requirement that was individually 100% reliable in isolation.

Motivation: the 4 isolated POCs (2026-08-14) found every single requirement
tested - wire-format round trip, Ignite-close (given the explicit warning),
the JMS BytesMessage API, explicit cache typing (both with and without the
warning) - passes at or near 100% when it's the ONLY thing asked for. Yet the
exact same explicit wording, embedded in the real 3-layer goal, still fails
27-38% of the time per requirement across 11 live runs. Isolation vs. the full
goal are two extreme data points; this POC fills in the middle by combining
requirements ONE AT A TIME:

  Step 1 (goal1 only):          Protocol + ProtocolParser (wire-format round trip)
  Step 2 (goal1 + goal2):       + ProtocolApp: Ignite start/close + explicitly-typed cache
                                 (folded together, matching the real goal's own Layer 2
                                 paragraph, which states both requirements together)
  Step 3 (goal1 + goal2 + goal3): + route the encoded bytes through a JMS BytesMessage
                                 send before caching (matching Layer 3)

Every explicit requirement's WORDING is reused verbatim from the already-
validated isolated POCs (WIRE_FORMAT_SPEC/IGNITE_AND_CACHE_SPEC/JMS_SPEC below) -
nothing is rephrased - so a pass-rate drop at a later step can only be
attributed to density (more requirements competing in one generation), not to
a wording change.

Grading, every sub-requirement checked independently at every step (so a
later step can reveal an EARLIER requirement quietly degrading, not just
whether the newest one is hard):
  - roundtrip: Protocol.java + ProtocolParser.java are extracted and compiled/run
    against the same fixed Main.java driver ../01_wire_format_roundtrip uses -
    real execution, not a pattern match.
  - ignite_closed: ProtocolApp.java (step >= 2) statically checked with Kriya's
    own real IgniteUnclosedResourceCheck (kriya/workflow/static_checks.py).
  - cache_typed: ProtocolApp.java (step >= 2) statically checked for `var` vs.
    explicit IgniteCache<...> on the cache declaration - same regex as
    ../04_cache_generics_typing.
  - jms_api: ProtocolApp.java (step 3) statically checked for the correct
    createBytesMessage()+writeBytes() shape - same regex as
    ../03_jms_bytes_message_api.
ProtocolApp.java itself is never compiled (would need a real ignite-core
dependency on the classpath for no grading benefit over the static checks
above) - only Protocol.java/ProtocolParser.java/Main.java are, exactly as in
../01_wire_format_roundtrip.

Writes everything to disk per trial (prompt, raw LLM response, every extracted
file, the Maven project actually compiled/run, and a verdict.json) plus a
plain-text run.log capturing every printed line - nothing here depends on the
terminal not being closed, unlike the earlier POCs 02/03/04 which only ever
printed to stdout.

Run: .venv/bin/python spikes/protocol_bug_pocs/05_incremental_composition/run_poc.py [--steps 1 2 3] [--trials N]
Requires `mvn` on PATH (only Protocol.java/ProtocolParser.java/Main.java are ever compiled).
"""
import argparse
import asyncio
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from kriya.config import load_config  # noqa: E402
from kriya.core.llm import LLMClient  # noqa: E402
from kriya.workflow.static_checks import IgniteUnclosedResourceCheck  # noqa: E402

MODEL = "qwen3-coder:30b"
BASE_URL = "http://localhost:11434/v1"
TEMPERATURE = 0.2
MVN_TIMEOUT_SECONDS = 120

SYSTEM_PROMPT = (
    "You are an expert Java developer. Follow the requirements exactly and "
    "respond in exactly the format requested - no extra commentary."
)

# --- Requirement text blocks, reused verbatim from the already-validated
# isolated POCs (01/02/03/04) - see this file's own module docstring for why
# nothing here is rephrased. ---

WIRE_FORMAT_SPEC = """Class `Protocol` (package com.example):
- Fields: protocolVersion (int, 0-255), softwareVersion (int, 0-255), dataLength (int, 0 to 16,777,215 - the body length only), time (long, treated as a 32-bit big-endian seconds-since-epoch value), body (byte[]).
- A constructor with this exact signature: `Protocol(int protocolVersion, int softwareVersion, long time, byte[] body)` - dataLength is derived from body.length, not passed in.
- Public getters: getProtocolVersion(), getSoftwareVersion(), getDataLength(), getTime(), getBody().
- equals(), hashCode(), and toString(). equals() and hashCode() MUST compare/hash body by CONTENT using Arrays.equals(body, other.body) and Arrays.hashCode(body), never by reference.

Class `ProtocolParser` (package com.example):
- `static byte[] encode(Protocol protocol)` implementing this EXACT wire format: a header of protocolVersion (1 byte) + softwareVersion (1 byte) + dataLength (3 bytes, big-endian) + time (4 bytes, big-endian), followed immediately by the raw body bytes. encode() must auto-calculate dataLength from body.length and use that computed value - never trust or read whatever value might already be stored in the Protocol object's own dataLength field.
- `static Protocol decode(byte[] data)` that parses the header, validates data.length == header_size + dataLength, extracts the body, and throws IllegalArgumentException on malformed input."""

IGNITE_AND_CACHE_SPEC = """Also write a `ProtocolApp` class (package com.example) with a `public static void main(String[] args) throws Exception` method that does the following:
1. Starts an embedded Apache Ignite node by calling `Ignition.start("ignite-config.xml")` (a config file that already exists in the working directory - you do not need to create it).
2. Builds a sample `Protocol` (any values you like), encodes it with `ProtocolParser.encode(...)`, then decodes it back with `ProtocolParser.decode(...)`.
3. Stores the decoded `Protocol` in an Ignite cache named "protocolCache", keyed by an int. The cache reference MUST be declared using an explicitly-typed `IgniteCache<Integer, Protocol>` reference - never a raw or `var`-inferred cache handle.
4. Reads the entry back from the cache into a separate variable and prints it.

IMPORTANT: An Ignite node that is started but never explicitly closed leaves background discovery/communication threads running that keep the JVM alive indefinitely, even after all the code above has already run successfully - this is a real defect, not a false alarm. You MUST ensure the Ignite instance is explicitly closed before the program exits, no matter which control-flow path is taken (including if an exception occurs)."""

JMS_SPEC = """Before storing the decoded Protocol in the cache (step 3 above), route the encoded bytes through JMS first: assume `javax.jms.Session session` and `javax.jms.MessageProducer producer` are already available as local variables in `main` (already connected to a broker - you do not need to create or configure them). Send the encoded bytes as a JMS BytesMessage using the given producer - create the BytesMessage from the session, put the bytes into it, and send it. Then proceed to decode and cache the Protocol as already described."""

_RESPONSE_FORMAT = """
Respond with ONLY the Java file(s) described above. For each file, put its exact relative path on its own line starting with '### ', immediately followed by a fenced code block containing that file's complete content. Do not include any other files, explanation, or commentary outside the code blocks."""

STEP_EXPECTED_FILES = {
    1: ["Protocol.java", "ProtocolParser.java"],
    2: ["Protocol.java", "ProtocolParser.java", "ProtocolApp.java"],
    3: ["Protocol.java", "ProtocolParser.java", "ProtocolApp.java"],
}


def build_prompt(step: int) -> str:
    parts = [
        "Write Java classes for a small binary protocol library. This is one isolated layer of a larger system, but for this task you only need what's described below - no Maven setup (that is already provided separately).\n",
        WIRE_FORMAT_SPEC,
    ]
    if step >= 2:
        parts.append(IGNITE_AND_CACHE_SPEC)
    if step >= 3:
        parts.append(JMS_SPEC)
    parts.append(_RESPONSE_FORMAT)
    return "\n\n".join(parts)


# --- Same fixed test harness as ../01_wire_format_roundtrip - not generated,
# never varies between steps, only ever compiles/runs Protocol.java +
# ProtocolParser.java + this file, regardless of what else got generated. ---

POM_XML = """<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.example</groupId>
  <artifactId>incremental-composition-poc</artifactId>
  <version>1.0-SNAPSHOT</version>
  <properties>
    <maven.compiler.source>17</maven.compiler.source>
    <maven.compiler.target>17</maven.compiler.target>
    <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
  </properties>
  <build>
    <plugins>
      <plugin>
        <groupId>org.codehaus.mojo</groupId>
        <artifactId>exec-maven-plugin</artifactId>
        <version>3.1.0</version>
        <configuration>
          <mainClass>com.example.Main</mainClass>
        </configuration>
      </plugin>
    </plugins>
  </build>
</project>
"""

MAIN_JAVA = """package com.example;

import java.util.Arrays;
import java.nio.charset.StandardCharsets;

public class Main {
    public static void main(String[] args) throws Exception {
        byte[] body = "Hello World".getBytes(StandardCharsets.UTF_8);
        Protocol original = new Protocol(1, 2, 1734000000L, body);

        byte[] encoded = ProtocolParser.encode(original);
        Protocol decoded = ProtocolParser.decode(encoded);

        boolean versionOk = decoded.getProtocolVersion() == 1;
        boolean swVersionOk = decoded.getSoftwareVersion() == 2;
        boolean dataLengthOk = decoded.getDataLength() == body.length;
        boolean timeOk = decoded.getTime() == 1734000000L;
        boolean bodyOk = decoded.getBody() != null && Arrays.equals(decoded.getBody(), body);

        System.out.println("[RESULT] protocolVersion=" + decoded.getProtocolVersion()
            + ", softwareVersion=" + decoded.getSoftwareVersion()
            + ", dataLength=" + decoded.getDataLength()
            + ", time=" + decoded.getTime()
            + ", bodyLength=" + (decoded.getBody() == null ? -1 : decoded.getBody().length));

        if (versionOk && swVersionOk && dataLengthOk && timeOk && bodyOk) {
            System.out.println("[VERIFICATION] PASS");
        } else {
            System.out.println("[VERIFICATION] FAIL: versionOk=" + versionOk
                + " swVersionOk=" + swVersionOk + " dataLengthOk=" + dataLengthOk
                + " (expected " + body.length + ", got " + decoded.getDataLength() + ")"
                + " timeOk=" + timeOk + " bodyOk=" + bodyOk);
        }
    }
}
"""

FILE_BLOCK_RE = re.compile(r"^###\s*(\S+)\s*\n```(?:java)?\n(.*?)```", re.MULTILINE | re.DOTALL)
CACHE_VAR_RE = re.compile(r"\bvar\s+\w*[Cc]ache\w*\s*=")
CACHE_TYPED_RE = re.compile(r"IgniteCache\s*<[^>]+>\s+\w*[Cc]ache\w*\s*=")


def extract_files(response: str) -> dict:
    return {m.group(1).strip(): m.group(2) for m in FILE_BLOCK_RE.finditer(response)}


def grade_cache_typing(app_code: str) -> str:
    if CACHE_VAR_RE.search(app_code):
        return "FAIL (used var for the cache handle)"
    if CACHE_TYPED_RE.search(app_code):
        return "PASS"
    if "getOrCreateCache(" not in app_code and "cache(" not in app_code:
        return "MISSING_CALL (no cache retrieval found at all)"
    return "UNKNOWN (couldn't find a recognizable typed/untyped cache declaration)"


def grade_jms_api(app_code: str) -> str:
    if re.search(r"createBytesMessage\s*\(\s*[^)\s]", app_code):
        return "FAIL (createBytesMessage called with an argument - it takes none)"
    if not re.search(r"createBytesMessage\s*\(\s*\)", app_code):
        return "FAIL (no correct-shaped createBytesMessage() call found)"
    if ".writeBytes(" not in app_code:
        return "FAIL (createBytesMessage() called correctly, but never wrote the bytes via writeBytes())"
    return "PASS"


class RunLogger:
    """Writes every printed line to a plain-text log on disk too - the earlier
    POCs (02/03/04) only ever printed to stdout, which is why a completed
    run's results couldn't be re-inspected after the fact. Explicit fix for
    that, per direct instruction."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("")

    def log(self, msg: str = "") -> None:
        print(msg)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(msg + "\n")


async def run_trial(llm: LLMClient, step: int, trial_dir: Path, logger: RunLogger) -> dict:
    prompt = build_prompt(step)
    (trial_dir / "prompt.txt").write_text(prompt)

    start = time.monotonic()
    try:
        response = await llm.complete(
            SYSTEM_PROMPT, prompt,
            model_override=MODEL, base_url_override=BASE_URL, api_key_override="local-key",
            temperature_override=TEMPERATURE,
        )
    except Exception as e:
        verdict = {"outcome": "LLM_ERROR", "detail": str(e)[:300], "elapsed": time.monotonic() - start}
        (trial_dir / "verdict.json").write_text(json.dumps(verdict, indent=2))
        return verdict
    elapsed = time.monotonic() - start
    (trial_dir / "response.txt").write_text(response)

    # Raw extraction preserves whatever path the model actually gave (e.g. a
    # package-qualified "com/example/Protocol.java" instead of a flat
    # "Protocol.java") - dumped as-is for inspection, parent dirs created as
    # needed. All DOWNSTREAM lookups (missing-file check, compile inputs, the
    # ProtocolApp.java static checks) go through files_by_basename instead,
    # so a model that qualifies its own path doesn't get misgraded as having
    # skipped a file it actually wrote.
    files = extract_files(response)
    extracted_dir = trial_dir / "extracted"
    extracted_dir.mkdir(exist_ok=True)
    for fname, content in files.items():
        dest = extracted_dir / fname
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)
    files_by_basename = {Path(k).name: v for k, v in files.items()}

    expected = STEP_EXPECTED_FILES[step]
    missing = [f for f in expected if f not in files_by_basename]
    verdict: dict = {"elapsed": round(elapsed, 1)}
    if missing:
        verdict["outcome"] = "MISSING_FILES"
        verdict["detail"] = f"missing: {missing}, got: {list(files.keys())}"
        (trial_dir / "verdict.json").write_text(json.dumps(verdict, indent=2))
        return verdict

    # roundtrip: real compile + execute, same harness as ../01_wire_format_roundtrip.
    maven_dir = trial_dir / "maven_project"
    src_dir = maven_dir / "src" / "main" / "java" / "com" / "example"
    src_dir.mkdir(parents=True, exist_ok=True)
    (maven_dir / "pom.xml").write_text(POM_XML)
    (src_dir / "Main.java").write_text(MAIN_JAVA)
    (src_dir / "Protocol.java").write_text(files_by_basename["Protocol.java"])
    (src_dir / "ProtocolParser.java").write_text(files_by_basename["ProtocolParser.java"])
    try:
        result = subprocess.run(
            ["mvn", "-q", "-DskipTests", "compile", "exec:java"],
            cwd=maven_dir, capture_output=True, text=True, timeout=MVN_TIMEOUT_SECONDS,
        )
        output = result.stdout + result.stderr
        if "[VERIFICATION] PASS" in output:
            verdict["roundtrip"] = "PASS"
        elif "[VERIFICATION] FAIL" in output:
            verdict["roundtrip"] = "ROUNDTRIP_FAIL"
        elif result.returncode != 0:
            verdict["roundtrip"] = "COMPILE_OR_RUNTIME_ERROR"
        else:
            verdict["roundtrip"] = "UNKNOWN"
    except subprocess.TimeoutExpired:
        verdict["roundtrip"] = "MVN_TIMEOUT"

    if step >= 2:
        app_code = files_by_basename["ProtocolApp.java"]
        ignite_violation = IgniteUnclosedResourceCheck().check({"ProtocolApp.java": app_code})
        verdict["ignite_closed"] = "FAIL (unclosed)" if ignite_violation else "PASS"
        verdict["cache_typed"] = grade_cache_typing(app_code)
    if step >= 3:
        verdict["jms_api"] = grade_jms_api(app_code)

    verdict["outcome"] = "GRADED"
    (trial_dir / "verdict.json").write_text(json.dumps(verdict, indent=2))
    return verdict


async def run_step(llm: LLMClient, step: int, trials: int, step_dir: Path, logger: RunLogger) -> list:
    logger.log(f"\n{'=' * 78}\nStep {step}: goal" + "+goal".join(str(g) for g in range(1, step + 1)) + f" ({', '.join(STEP_EXPECTED_FILES[step])})\n{'=' * 78}")
    results = []
    for i in range(trials):
        trial_dir = step_dir / f"trial_{i}"
        trial_dir.mkdir(parents=True, exist_ok=True)
        r = await run_trial(llm, step, trial_dir, logger)
        results.append(r)
        parts = [f"[{i + 1}/{trials}]", r.get("outcome", "?"), f"{r.get('elapsed', 0):.1f}s"]
        for key in ("roundtrip", "ignite_closed", "cache_typed", "jms_api"):
            if key in r:
                parts.append(f"{key}={r[key]}")
        if "detail" in r:
            parts.append(r["detail"][:150])
        logger.log("  " + "  ".join(str(p) for p in parts))
    (step_dir / "summary.json").write_text(json.dumps(results, indent=2))
    return results


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--steps", type=int, nargs="+", default=[1, 2, 3], choices=[1, 2, 3])
    args = parser.parse_args()

    if shutil.which("mvn") is None:
        print("ERROR: 'mvn' not found on PATH - this POC compiles and runs real Java code.")
        sys.exit(1)

    config = load_config()
    llm = LLMClient(config)

    batch_id = time.strftime("%Y%m%d-%H%M%S")
    run_dir = Path(__file__).resolve().parent / "runs" / batch_id
    logger = RunLogger(run_dir / "run.log")
    logger.log(f"Incremental composition POC - batch {batch_id}")
    logger.log(f"Output/logs under: {run_dir}")

    all_results = {}
    for step in args.steps:
        step_dir = run_dir / f"step{step}"
        all_results[step] = await run_step(llm, step, args.trials, step_dir, logger)

    logger.log(f"\n{'=' * 78}\nSummary\n{'=' * 78}")
    for step, results in all_results.items():
        keys = [k for k in ("roundtrip", "ignite_closed", "cache_typed", "jms_api") if any(k in r for r in results)]
        for key in keys:
            outcomes = [r.get(key, "N/A") for r in results]
            pass_count = sum(1 for o in outcomes if o == "PASS")
            logger.log(f"  step{step}.{key:<14}: {pass_count}/{len(outcomes)} PASS   {outcomes}")

    logger.log(f"\nFull per-trial detail (prompt/response/extracted files/verdict.json) under: {run_dir}")


if __name__ == "__main__":
    asyncio.run(main())
