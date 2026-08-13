"""The eval harness's diverse goal set. See README.md for the question this
answers. Each entry is picked for a specific hypothesis about which
failure_category it's likely to probe (kriya/core/trace.py's new column) -
not just "another app" alongside the Ignite+Qpid golden use case that's
already produced six bugs and diminishing returns.

Kept intentionally small to start (coarse-first, per the architecture
initiative decision) - expected to grow once a first real batch shows which
categories are under-represented.
"""
from dataclasses import dataclass, field
from typing import List


@dataclass
class Goal:
    id: str
    text: str
    hypothesis: str
    # Extra kriya-generate CLI args beyond the shared `-y`, e.g. a non-default
    # --knowledge-policy for a goal specifically meant to probe that gate.
    extra_args: List[str] = field(default_factory=list)


GOALS: List[Goal] = [
    Goal(
        id="python_greeter",
        text=(
            "Create a Python script greet.py that defines a function "
            "greet(name: str) -> str returning f'Hello, {name}!' and, when "
            "run directly, prints greet('World')."
        ),
        hypothesis=(
            "Fast, single-file, stdlib-only baseline - should pass on attempt "
            "1. A regression here (not the goal's actual difficulty) points "
            "at something wrong in Kriya's own pipeline, not the model."
        ),
    ),
    Goal(
        id="ignite_qpid_person",
        text=(
            "In a Maven project targeting Java 17, using an embedded Apache "
            "Ignite 2.18 node and an embedded Apache Qpid Broker-J AMQP "
            "broker (both started in the same Spring XML-configured main "
            "method), define a Person class (name, email). On startup: "
            "start the Qpid broker, send one Person as a JMS message to a "
            "queue, synchronously consume that same message back via a "
            "MessageConsumer, store the received Person in the Ignite "
            "cache, then read it back from the cache and print its fields "
            "to stdout."
        ),
        hypothesis=(
            "Known-hard comparison point - the same app shape three days of "
            "manual validation already found six real bugs in "
            "(JAVA_HOME/jdtls, stack-detection, LSP visibility, runtime- "
            "verification file-scoping, process-group leak, anchored-edit "
            "parsing). Exact wording differs from any prior run (never "
            "persisted anywhere - see docs/kriya_production_readiness.md), "
            "but the shape is the same: multi-file Java/Maven/Spring, two "
            "libraries wired together, an explicit 'consume synchronously' "
            "step past ones ignored a model has skipped before."
        ),
    ),
    Goal(
        id="ignite_qpid_protocol",
        text=(
            "In a Maven project targeting Java 17, build this in three "
            "layers, IN ORDER - get each layer's logic right before adding "
            "the next, since each one depends on the previous being "
            "correct. Provide the complete Maven module structure "
            "including pom.xml - this is a real, buildable Maven project, "
            "not just the classes described below. Put all startup/"
            "orchestration logic (starting Ignite, starting Qpid, encode/"
            "send/receive/decode, cache store/read, verification, "
            "printing) in ONE entry-point class named ProtocolApp, with a "
            "public static void main(String[] args) method - do not split "
            "this across multiple orchestration classes, and do not name "
            "this class 'Main' (that name is reserved and must not be "
            "used).\n\n"
            "LAYER 1 - Protocol + ProtocolParser (no Ignite, no Qpid yet): "
            "define a Protocol class with fields protocolVersion (int, "
            "0-255), softwareVersion (int, 0-255), dataLength (int, "
            "0-16,777,215 - the body length only), time (long, treated as "
            "a 32-bit big-endian seconds-since-epoch value), and body "
            "(byte[]). Add equals(), hashCode(), and toString() to "
            "Protocol - equals() and hashCode() MUST compare/hash body by "
            "CONTENT using Arrays.equals(body, other.body) and "
            "Arrays.hashCode(body), never by reference. Add a "
            "ProtocolParser with byte[] encode(Protocol) implementing this "
            "exact wire format: a 9-byte big-endian header - "
            "protocolVersion (1 byte), softwareVersion (1 byte), "
            "dataLength (3 bytes), time (4 bytes) - followed by the raw "
            "body bytes; encode() must auto-calculate dataLength from "
            "body.length and overwrite the field, not trust whatever was "
            "already there. Add Protocol decode(byte[]) that parses the "
            "header, validates data.length == 9 + dataLength, extracts the "
            "body, and throws IllegalArgumentException on malformed "
            "input.\n\n"
            "LAYER 2 - add the embedded Ignite cache: start an embedded "
            "Apache Ignite 2.18 node via Spring XML configuration, in the "
            "same main method. Use EXACTLY ONE Ignite startup mechanism - "
            "either call Ignition.start(...) directly against a plain "
            "IgniteConfiguration bean (no IgniteSpringBean anywhere in the "
            "XML), or define an IgniteSpringBean in the XML and load it "
            "via ClassPathXmlApplicationContext, retrieving the "
            "already-started instance with context.getBean(...) - NEVER "
            "call Ignition.start() at all under that second approach. "
            "Mixing the two throws 'IgniteException: Ignite instance with "
            "this name has already been started' at runtime. Whichever "
            "mechanism you use, you MUST explicitly close it before the "
            "program exits - either ignite.close() or context.close(), in "
            "the same finally block that will also shut down the Qpid "
            "broker in Layer 3 below. An Ignite node that is started but "
            "never closed leaves background threads running that keep the "
            "JVM alive indefinitely, even after every expected line has "
            "already printed correctly - this is a real defect the "
            "process gets force-killed for, not a false alarm. Store a "
            "decoded Protocol (built via Layer 1's encode/decode round "
            "trip on a sample Protocol) in the Ignite cache using an "
            "explicitly-typed IgniteCache<Integer, Protocol> reference "
            "(never a raw or var-inferred cache handle), then read it back "
            "from the cache into a separate variable.\n\n"
            "LAYER 3 - add the embedded Qpid broker + JMS: start an "
            "embedded Apache Qpid Broker-J AMQP broker in the same main "
            "method (started/stopped alongside Ignite, both in the same "
            "try/finally lifecycle). Instead of encoding then immediately "
            "decoding the sample Protocol in-process, send the encoded "
            "bytes as a JMS BytesMessage to a queue, then synchronously "
            "consume that SAME message back via a real, blocking "
            "MessageConsumer.receive() call with an explicit timeout - the "
            "bytes must actually round-trip through the broker; do not "
            "reuse the original in-memory encoded byte array as if it "
            "were the received message - then decode the received bytes "
            "into the Protocol object that gets stored in the Ignite "
            "cache from Layer 2.\n\n"
            "Verification: after the full round trip (encode -> JMS send "
            "-> JMS receive -> decode -> Ignite cache store -> Ignite "
            "cache read), programmatically verify (an explicit if-check "
            "and throw, not the Java assert keyword, since JVM assertions "
            "are not enabled when this runs) that the ORIGINAL pre-send "
            "Protocol equals the one finally read back from the cache. "
            "Print a line in exactly this format: [RESULT] "
            "protocolVersion=X, softwareVersion=Y, dataLength=Z, time=T, "
            "bodyLength=B - then print [VERIFICATION] PASS if the equals "
            "check succeeded, or [VERIFICATION] FAIL: <reason> if it did "
            "not - do not hardcode either outcome, both must reflect the "
            "real computed comparison."
        ),
        hypothesis=(
            "Combines two independently-proven components - the Ignite+"
            "Qpid+Spring orchestration from ignite_qpid_person, and the "
            "hand-rolled binary protocol format from the closed "
            "kriya-protocol-parser-app effort - in a combination never "
            "tested together. Stresses a genuinely different JMS code path "
            "(BytesMessage + raw byte[] handling, not TextMessage+JSON like "
            "the Person goal) and a different Ignite cache value type. "
            "Rewritten 2026-08-12 into an explicit, ordered three-layer "
            "goal text (was one flat paragraph) after live runs of the "
            "flat version repeatedly hit two confirmed, distinct bugs in "
            "the SAME session despite skills/ignite-java17/rules.txt "
            "already documenting both in detail: mixing Ignite's two "
            "startup mechanisms ('already been started'), and leaving an "
            "Ignite node unclosed (hangs the JVM after everything else "
            "already printed correctly). Both are now stated directly in "
            "the goal text itself, not just the skill, as a second, more "
            "prominent reinforcement - the same knowledge, closer to point "
            "of use. A separate, already-validated experiment "
            "(../kriya-staged-protocol-ignite-qpid/goal.md, outside this "
            "repo) proved a fully STAGED three-call version of this same "
            "goal converges dramatically faster/more reliably than one "
            "shot (137s + 409s total vs. one one-shot run that took ~30 "
            "minutes and another that timed out at 2400s without ever "
            "succeeding) - this rewrite tests whether most of that benefit "
            "is reachable by asking the SAME model to sequence itself "
            "within one generate call, before concluding the harness needs "
            "real multi-stage execution support to get it. Wording fixed "
            "again same day, first live run of the layered rewrite: the "
            "layered text was so detailed about the three domain classes "
            "that it never asked for a pom.xml or a concrete entry-point "
            "file at all, and both independent Best-of-N candidates "
            "identically omitted both - a systematic Architect-planning "
            "gap, not per-sample randomness, so Best-of-N couldn't help "
            "(confirming its own limit: it only rescues probabilistic "
            "mistakes, not a goal-text omission every sample inherits "
            "alike). PolymorphicValidator's stack-detection then silently "
            "vacuous-passed the compile gate ('unknown' stack, no pom.xml "
            "to detect Java from), so the missing scaffolding only "
            "surfaced 3 steps later as a confusing 'Could not find or load "
            "main class Main' at runtime verification - Kriya's own "
            "inferred run command guessed the literal class name 'Main' "
            "from this goal's earlier 'one cohesive Main class' phrasing, "
            "which was meant as a style note, not a real file's name. Now "
            "explicit: 'provide the complete Maven module structure "
            "including pom.xml', and the entry-point class is named "
            "ProtocolApp (matching what every pre-rewrite run already "
            "converged on) with 'Main' explicitly reserved/disallowed."
        ),
    ),
    Goal(
        id="ruby_word_count",
        text=(
            "Create a Ruby project with a lib/word_count.rb (pure Ruby "
            "standard library, no external gems needed for the "
            "implementation itself) defining WordCount.count(text) that "
            "returns a Hash of word => occurrence count (case-insensitive, "
            "punctuation stripped), plus a spec/word_count_spec.rb with "
            "RSpec tests covering an empty string, repeated words, and mixed "
            "case, and a Gemfile that declares rspec as a dependency so the "
            "test suite can actually run."
        ),
        hypothesis=(
            "Zero coverage today outside unit tests - PolymorphicValidator "
            "supports Ruby (Gemfile/Rakefile/spec markers) but no live batch "
            "run has ever exercised that compile/test path end to end. "
            "Wording fixed 2026-08-07: the original text asked for a "
            "Gemfile with 'no external gems needed' while ALSO requiring "
            "RSpec tests - self-contradictory, since rspec is itself an "
            "external gem. Confirmed live: the model took the instruction "
            "literally, wrote an empty Gemfile, then flailed through "
            "several increasingly confused fix attempts (including pinning "
            "gem 'bundler', '~> 2.0' in its own Gemfile, which then "
            "collided with this machine's older system bundler) without "
            "ever reaching the actual fix - all downstream symptoms of the "
            "same original contradiction. Now explicit that 'no external "
            "gems' scopes to the library implementation only, and the "
            "Gemfile must declare rspec for the test suite."
        ),
    ),
    Goal(
        id="python_task_tracker",
        text=(
            "Build a small Python CLI task tracker across multiple modules: "
            "tasks/model.py (a Task dataclass: id, title, done), "
            "tasks/store.py (a TaskStore holding tasks in memory with add/"
            "complete/list_pending methods, plus load/save methods that "
            "persist the tasks to a JSON file so state survives between "
            "separate runs of the CLI), and cli.py (argparse-based commands: "
            "add <title>, done <id>, list - each invocation loads the "
            "TaskStore from the JSON file before acting and saves it back "
            "after) that wires them together. Include tests/test_store.py "
            "covering add, complete, and list_pending against a TaskStore "
            "instance directly (no file I/O needed in these tests)."
        ),
        hypothesis=(
            "Multi-file, stdlib-only, sized to plausibly cross a real diff-"
            "size threshold and stress multi-file response consistency - "
            "the documented bottleneck class (docs/kriya_production_"
            "readiness.md) distinct from single-file knowledge gaps. Note: "
            "under -y, on_approval always auto-approves (kriya/cli.py), so "
            "this cannot exercise the human_rejected category in an "
            "unattended harness run by design - that category is inherently "
            "interactive-only and will never appear in harness batch data. "
            "Wording fixed 2026-08-07: the original text asked for an "
            "'in-memory' TaskStore with argparse CLI commands, which is "
            "self-contradictory once run_verification exercises them as "
            "separate 'python cli.py <cmd>' shell invocations (confirmed "
            "live - each invocation is a fresh process, so a genuinely "
            "in-memory-only store can never show state added by an earlier "
            "invocation, regardless of how correct the generated code is). "
            "Now explicit that persistence goes through a JSON file between "
            "CLI invocations, matching how a real CLI tool would actually "
            "need to work, while the TaskStore/tests stay pure in-memory."
        ),
    ),
    Goal(
        id="django_healthcheck_gap",
        text=(
            "Using Django 5.2, add a minimal view at /healthz that returns "
            "a JSON response {\"status\": \"ok\"} for a GET request, wired "
            "into urls.py."
        ),
        hypothesis=(
            "Django 5.2 (released 2025, after the default 2023-12-01 "
            "knowledge cutoff) should trip KnowledgeGuard's stage-0 check. "
            "Requires real network access (KnowledgeGuard queries PyPI's "
            "release-date API) - if offline, this goal will silently not "
            "probe what it's meant to. Under the default --knowledge-policy "
            "warn plus -y, the CLI auto-confirms and re-runs after the "
            "initial gap detection, so traces.db will show TWO rows for "
            "this goal: a knowledge_gap row from the first (blocked) call, "
            "then a normal success/failure row from the auto-confirmed "
            "retry - both are correct, not a duplicate-logging bug."
        ),
    ),
]
