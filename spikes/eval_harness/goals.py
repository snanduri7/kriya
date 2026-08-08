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
            "In a Maven project targeting Java 17, using an embedded Apache "
            "Ignite 2.18 node and an embedded Apache Qpid Broker-J AMQP "
            "broker (both started in the same Spring XML-configured main "
            "method), define a Protocol class with fields protocolVersion "
            "(int), softwareVersion (int), dataLength (int), time (long), "
            "and body (byte[]), plus a ProtocolParser with "
            "encode(Protocol)->byte[] and decode(byte[])->Protocol "
            "implementing this exact wire format: a 9-byte header - "
            "protocolVersion (1 byte), softwareVersion (1 byte), dataLength "
            "(3 bytes, big-endian), time (4 bytes, big-endian) - followed "
            "by the raw body bytes. On startup: start the Qpid broker, "
            "create a sample Protocol (small representative field values, "
            "a body of a few dozen bytes), encode it, and send the encoded "
            "bytes as a JMS BytesMessage to a queue. Synchronously consume "
            "that same message back via a MessageConsumer, decode the "
            "received bytes into a Protocol object, store the decoded "
            "Protocol in the Ignite cache using an explicitly-typed "
            "IgniteCache<Integer, Protocol> reference (never a raw or "
            "var-inferred cache handle), then read it back from the cache "
            "and print all its field values (protocolVersion, "
            "softwareVersion, dataLength, time, body length) to stdout "
            "with a [RESULT] marker."
        ),
        hypothesis=(
            "Combines two independently-proven components - the Ignite+"
            "Qpid+Spring orchestration from ignite_qpid_person, and the "
            "hand-rolled binary protocol format from the closed "
            "kriya-protocol-parser-app effort - in a combination never "
            "tested together. Stresses a genuinely different JMS code path "
            "(BytesMessage + raw byte[] handling, not TextMessage+JSON like "
            "the Person goal) and a different Ignite cache value type - "
            "directly tests whether this session's raw-generics/"
            "incompatible-types fixes (the prompt scaffold, Layer 1) "
            "generalize beyond the one goal that surfaced them, not just "
            "happened to fix that one specific case."
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
