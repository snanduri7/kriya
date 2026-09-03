"""Deterministic, no-LLM pre-flight checks for known anti-patterns already
documented in skill rules (e.g. skills/ignite-java17/rules.txt's extensive
"never mix Ignite Method A and Method B" / "never leave Ignition.start()
unclosed" rules). Run once per attempt, after all files are written and known-
complete, before the expensive compile gate - catches a mistake the model
already had the rule for, without waiting through a full Maven build + JVM
boot + broker startup + a live hang/crash to discover it.

Found live, 2026-08-12 (ignite_qpid_protocol): the two Ignite checks below
reproduce real failures hit in the same session - a generated app that mixed
Ignition.start() with an XML defining IgniteSpringBean threw
"IgniteException: Ignite instance with this name has already been started"
at runtime, and a separate attempt left an Ignition.start()'d node with no
matching close(), hanging the JVM indefinitely after all application logic
had already finished and printed its correct result. Both are inherently
Java/Ignite-specific. A third check (BareVerificationMarkerCheck, added
2026-08-13) is deliberately language-generic instead - it scans every
written file regardless of extension, since the anti-pattern it catches
(Kriya's own runtime-verification marker text getting embedded unprinted)
can happen in any target language, not just Java.

A fourth check (TestContradictsVerificationMarkerCheck, added 2026-08-14)
catches a different, previously-undiscovered shape of the same underlying
theme - not a FALSE application defect this time, but a FALSE Quality Gate
failure: the model writes a test asserting a subprocess's stdout is exactly
empty, while the SAME entrypoint that subprocess invokes is (correctly)
required to print a "[VERIFICATION] PASS"/"FAIL" line per the verification
contract. Both facts can never be true together - the test is unsatisfiable
by construction, not a bug in the actual application. Found live,
2026-08-14 (a hand-run `wordcount.py` demo goal, not the eval harness): the
generated app worked correctly when run manually; the retry loop spent its
entire budget chasing a test whose own assertion contradicted a requirement
the same completion had already (correctly) satisfied elsewhere.

A fifth check (MismatchedFileTypeContentCheck, added 2026-08-19) catches a
different failure mode entirely: not a defect IN a file's own logic, but a
file getting the WRONG file's content altogether. Found live on a plain
static HTML/CSS/JS goal - the one class of project where NOTHING else in the
pipeline validates content by language at all, since PolymorphicValidator has
no compile/parse step for an "unknown" stack. A retry asked the Developer to
repair one file in response to an error that actually implicated a sibling;
instead of using the REPAIR-mode prompt's "NO CHANGE NEEDED" escape hatch,
the model wrote the sibling's own content under the wrong filename, and it
shipped as-is - a `.html` file with no HTML in it.

Best-effort by design, matching this project's own established philosophy for
this class of check (see kriya/workflow/failure_grounding.py's
extract_implicated_files() docstring): a false positive is low-cost since it's
just one more retry cycle, not a hard block.
"""
import json
import logging
import os
import re
import ast
import io
import tokenize
from dataclasses import dataclass
import xml.etree.ElementTree as ET
from typing import Dict, Iterable, Optional, Tuple

from kriya.workflow.edit_safety import _strip_java_comments_and_strings

logger = logging.getLogger(__name__)


class StaticCheck:
    """One deterministic anti-pattern check. `name` becomes part of the
    violation message so a human reading gate_outcomes/traces.db can tell
    which check fired."""

    name = "static_check"

    def check(self, files: Dict[str, str]) -> Optional[str]:
        raise NotImplementedError


class IgniteMethodMixingCheck(StaticCheck):
    name = "ignite_method_mixing"

    def check(self, files: Dict[str, str]) -> Optional[str]:
        direct_start_files = [f for f, c in files.items() if f.endswith(".java") and "Ignition.start(" in c]
        spring_bean_files = [f for f, c in files.items() if f.endswith(".xml") and "IgniteSpringBean" in c]

        if direct_start_files and spring_bean_files:
            return (
                f"{', '.join(sorted(direct_start_files))} calls Ignition.start(...) directly (Method A) "
                f"while {', '.join(sorted(spring_bean_files))} also defines an IgniteSpringBean (Method B). "
                "Mixing these two Ignite startup mechanisms in the same app throws "
                "'IgniteException: Ignite instance with this name has already been started' at "
                "runtime. Use exactly ONE: either Ignition.start(...) directly with a plain "
                "IgniteConfiguration bean in the XML (no IgniteSpringBean), or load the XML via "
                "ClassPathXmlApplicationContext and retrieve the already-started instance with "
                "context.getBean(...) - never call Ignition.start() at all under that approach."
            )
        return None


class IgniteDuplicateSpringContextCheck(StaticCheck):
    """Rejects loading the same IgniteSpringBean XML more than once.

    ``IgniteSpringBean`` starts during Spring context initialization. Creating
    a second context for the same resource therefore starts the same named node
    again even when the helper only wanted an unrelated bean from that XML.
    This is an ecosystem lifecycle invariant, not an application-name rule.
    """

    name = "ignite_duplicate_spring_context"
    _CONTEXT_LOAD_RE = re.compile(
        r"new\s+ClassPathXmlApplicationContext\s*\(\s*[\"']([^\"']+)[\"']\s*\)"
    )

    @staticmethod
    def _defines_ignite_spring_bean(content: str) -> bool:
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            # A malformed XML document is handled by the ordinary XML/build
            # gate. Raw substring fallback would turn comments or partial text
            # into false lifecycle evidence.
            return False
        return any(
            element.attrib.get("class", "").rsplit(".", 1)[-1] == "IgniteSpringBean"
            for element in root.iter()
        )

    def check(self, files: Dict[str, str]) -> Optional[str]:
        spring_resources = {
            filepath: content
            for filepath, content in files.items()
            if filepath.endswith(".xml") and self._defines_ignite_spring_bean(content)
        }
        if not spring_resources:
            return None

        loads: Dict[str, Dict[str, int]] = {}
        for filepath, content in sorted(files.items()):
            if not filepath.endswith(".java"):
                continue
            structural = _strip_java_comments_and_strings(content)
            for match in self._CONTEXT_LOAD_RE.finditer(content):
                # The structural mirror preserves offsets but blanks comments
                # and strings. The constructor token must still be present at
                # this raw match's offset; examples inside comments/string data
                # therefore cannot become false violations.
                if structural[match.start():match.start() + 3] != "new":
                    continue
                resource = match.group(1).lstrip("/")
                matching_xml = next(
                    (
                        xml_path for xml_path in spring_resources
                        if (
                            xml_path.replace("\\", "/").lstrip("/") == resource
                            or xml_path.replace("\\", "/").lstrip("/").endswith("/" + resource)
                            or os.path.basename(xml_path) == os.path.basename(resource)
                        )
                    ),
                    None,
                )
                if matching_xml:
                    per_file = loads.setdefault(matching_xml, {})
                    per_file[filepath] = per_file.get(filepath, 0) + 1

        for _xml_path, per_file_counts in sorted(loads.items()):
            for java_file, load_count in sorted(per_file_counts.items()):
                if load_count <= 1:
                    continue
                # Cross-file loads are not rejected without call-graph evidence:
                # a test and an entrypoint can legitimately initialize the same
                # resource in separate processes. Multiple loads in one source
                # file are the bounded, deterministic incident shape.
                return (
                    f"{java_file} constructs ClassPathXmlApplicationContext for the "
                    f"same Spring XML resource {load_count} times, and that resource "
                    "defines IgniteSpringBean. Each context load auto-starts the "
                    "Ignite node, so the second load fails with 'Ignite instance with this "
                    "name has already been started'. Construct the context exactly once, "
                    "keep it open for the application lifetime, and pass that same context "
                    "to every helper that needs any bean from the XML."
                )
        return None


class BareVerificationMarkerCheck(StaticCheck):
    """Catches Kriya's own Verification Contract instruction
    (retry_prompts.py's VERIFICATION_CONTRACT_HEADER, which asks the model to
    make its entrypoint PRINT an exact "[VERIFICATION] PASS"/"[VERIFICATION]
    FAIL: ..." verdict line) getting written into the source as bare,
    unquoted, un-printed text instead of an actual print/output call.

    Found live, 2026-08-13 (python_greeter, reproduced identically across two
    separate eval-harness runs): qwen3-coder:30b's first-draft generation
    wrote the literal, unquoted tokens `[VERIFICATION] PASS` as a standalone
    line - genuinely invalid Python (a syntax error), but nothing guarantees
    the same mistake is a compile-time error in every target language; a
    language where a bare identifier-like expression is legal would let this
    slip through as a silent no-op that never actually prints the verdict.
    This check is language-generic by design (scans every written file, not
    just one extension) since the marker text and the mistake shape are
    identical regardless of target language - unlike the Ignite checks
    above, which are inherently Java/XML-specific to the frameworks
    involved.

    Deliberately narrow: only flags the exact confirmed shape (the marker
    starting a stripped line completely bare, no leading quote or print-call
    prefix). A correctly quoted-but-never-printed marker (e.g. a bare
    "[VERIFICATION] PASS" string expression sitting on its own, syntactically
    valid in some languages but still never reaching real output) is a
    plausible sibling mistake but not yet a confirmed live incident - not
    covered here, matching this module's own established practice of one
    check per concretely-observed failure rather than speculative coverage."""

    name = "bare_verification_marker"

    _BARE_MARKER_RE = re.compile(r"^\[VERIFICATION\]\s+(PASS|FAIL)\b")

    def check(self, files: Dict[str, str]) -> Optional[str]:
        for filepath, content in sorted(files.items()):
            for line_no, line in enumerate(content.splitlines(), start=1):
                stripped = line.strip()
                if self._BARE_MARKER_RE.match(stripped):
                    return (
                        f"{filepath} line {line_no} contains the literal, unquoted text "
                        f"'{stripped}' sitting on its own. The verification contract's verdict "
                        "line must be the argument to an actual print/console-output call in "
                        "this file's language (e.g. Python's print(...), Java's "
                        "System.out.println(...), Ruby's puts) - not a bare line of source text. "
                        "As written, this either fails to compile, or compiles but never actually "
                        "prints anything, so the verdict will never reach the program's real "
                        "output. Wrap it in the correct print/output call for this file's "
                        "language."
                    )
        return None


class IgniteUnclosedResourceCheck(StaticCheck):
    name = "ignite_unclosed_resource"

    _TRY_WITH_RESOURCES_RE = re.compile(r"try\s*\([^)]*Ignition\.start\(")

    def check(self, files: Dict[str, str]) -> Optional[str]:
        for filepath, content in sorted(files.items()):
            if not filepath.endswith(".java"):
                continue
            if "Ignition.start(" not in content:
                continue
            if ".close(" in content:
                continue
            if self._TRY_WITH_RESOURCES_RE.search(content):
                continue
            return (
                f"{filepath} calls Ignition.start(...) but never calls .close() on the result, "
                "and doesn't use try-with-resources either. Background discovery/communication "
                "threads keep the JVM alive indefinitely after all application logic has already "
                "finished - the process hangs and gets force-killed after timing out, even though "
                "every expected output already printed correctly. Close the Ignite instance "
                "explicitly (e.g. in the same finally block that shuts down any embedded broker), "
                "or wrap the assignment in try-with-resources."
            )
        return None


class TestContradictsVerificationMarkerCheck(StaticCheck):
    """Catches a test file asserting a subprocess's stdout is EXACTLY empty
    when the specific script it invokes is itself required (by the goal's
    own verification contract) to print a "[VERIFICATION] PASS"/"FAIL" line
    on every run - the two facts are structurally incompatible, so the test
    can never pass regardless of how correct the invoked script actually is.

    Found live, 2026-08-14 (a hand-run wordcount.py demo goal): `wordcount.py`
    worked correctly when run manually - correct word counts, clean exit,
    ending with `print("[VERIFICATION] PASS")` per the standing convention.
    The model's OWN generated `test_empty_file` asserted
    `result.stdout.strip() == ""` for that same script - impossible once the
    verification line is always printed, regardless of input. The retry loop
    spent its entire budget (multiple targeted + full-set attempts, plus an
    escalation) chasing this before running out, never touching the real
    contradiction: two artifacts written in the same completion, one
    requiring output the other asserts can't exist. Distinct from
    BareVerificationMarkerCheck above (that catches the marker never
    reaching real output at all) - here the marker prints correctly, and a
    SEPARATE, sibling file's test is what's wrong.

    Deliberately narrow to the exact confirmed shape, matching this module's
    own established practice (see BareVerificationMarkerCheck's own
    docstring): Python only (`subprocess.run` is the specific idiom that
    produced the real incident - a Java/JUnit or Ruby/RSpec equivalent would
    need its own detection, not guessed at here), and only an EXACT-equality
    assertion against an empty string literal (`.stdout(.strip())? == ""` or
    `''`) - unambiguously wrong whenever it fires, since a script required to
    print the marker can never produce fully empty stdout. A looser
    "asserts some non-marker-inclusive literal" version was considered and
    rejected: distinguishing a genuinely wrong assertion from one that
    legitimately checks only a substring, or invokes a DIFFERENT script with
    no verification-contract obligation of its own, isn't reliable from text
    alone - the empty-string case is the one shape with no such ambiguity.

    Cross-references the SPECIFIC invoked script (extracted from the
    subprocess.run(...) call itself, not just "some file in this batch
    mentions the marker somewhere") against every already-written file
    sharing that basename, so a file that merely happens to also be in this
    batch but isn't the one actually being tested never triggers a false
    match."""

    name = "test_contradicts_verification_marker"

    _SUBPROCESS_INVOKE_RE = re.compile(
        r"subprocess\.run\(\s*\[\s*(?:sys\.executable|[\"']python3?[\"'])\s*,\s*[\"']([^\"']+)[\"']"
    )
    _STDOUT_EXACT_EMPTY_RE = re.compile(r"\.stdout(?:\.strip\(\))?\s*==\s*(?:\"\"|'')")

    def check(self, files: Dict[str, str]) -> Optional[str]:
        for filepath, content in sorted(files.items()):
            if not filepath.endswith(".py"):
                continue
            if not self._STDOUT_EXACT_EMPTY_RE.search(content):
                continue
            for m in self._SUBPROCESS_INVOKE_RE.finditer(content):
                invoked = m.group(1)
                invoked_contents = [c for f, c in files.items() if os.path.basename(f) == invoked]
                if any("[VERIFICATION]" in c for c in invoked_contents):
                    return (
                        f"{filepath} asserts a subprocess's stdout is exactly empty "
                        f"(`.stdout ... == \"\"`), but it invokes {invoked}, which is required to "
                        "print a '[VERIFICATION] PASS'/'[VERIFICATION] FAIL: ...' line on every run "
                        "per the goal's verification contract - stdout can never be exactly empty. "
                        f"This assertion can never pass regardless of whether {invoked} is correct. "
                        "Assert on the actual expected content instead (e.g. that the verification "
                        "marker is present, or the specific output besides it), not exact emptiness."
                    )
        return None


class MismatchedFileTypeContentCheck(StaticCheck):
    """Catches a file whose content doesn't match its own extension at all -
    e.g. a `.html` file containing plain JavaScript, with zero HTML markup.

    Found live, 2026-08-19 (a plain static HTML/CSS/JS calculator goal - no
    pom.xml/Gemfile/requirements.txt, so PolymorphicValidator classifies it
    as the "unknown" stack and never actually parses or compiles anything;
    see its own docstring for why that's a deliberate, honest degrade rather
    than a silent Python fallback - it just means NOTHING else in the
    pipeline validates file content by language for this stack). During a
    retry, the Developer agent was asked to repair `calculator/index.html`
    in response to an error that actually implicated a SIBLING file
    (`calculator/script.js`). The REPAIR-mode prompt (DeveloperAgent) gives
    the model an explicit escape hatch for exactly this situation -
    "NO CHANGE NEEDED: <reason>" when the reported error doesn't implicate
    the file being repaired - but the model didn't take it: it wrote
    script.js's own content (the whole Calculator class, verbatim) into
    index.html's FILE CONTENT: block instead. That got applied to the
    workspace as-is; the shipped "webpage" had no HTML in it at all.

    Deliberately narrow and conservative, matching this module's established
    practice (see BareVerificationMarkerCheck's own docstring): only fires
    on `.html`/`.htm` files with no actual tag-shaped pattern anywhere in
    their content (`<` immediately followed by a letter, `/`, or `!` - an
    opening tag, closing tag, or `<!DOCTYPE`/comment). A real HTML document
    - even a bare fragment - always has at least one such tag. Matching on a
    bare `<` alone was tried first and found live to under-fire: real
    JavaScript routinely contains a bare `<` as the less-than operator (e.g.
    `if (x < 0.000001)`, present in the exact incident's own script.js), so
    that naive version missed the very file it was built to catch. The
    tag-shaped pattern has no equivalent false-negative path in ordinary
    JS/CSS, and no realistic false-positive path for real HTML (even a bare
    fragment always has a tag). Not extended to `.css`/`.js`/etc. yet - the
    HTML case is the one with a confirmed live incident and an unambiguous
    signal; any other extension would need its own confirmed incident before
    guessing at a detection shape for it."""

    name = "mismatched_file_type_content"

    _HTML_TAG_RE = re.compile(r"<[a-zA-Z!/]")

    def check(self, files: Dict[str, str]) -> Optional[str]:
        for filepath, content in sorted(files.items()):
            if not filepath.lower().endswith((".html", ".htm")):
                continue
            if not self._HTML_TAG_RE.search(content):
                return (
                    f"{filepath} is an HTML file but contains no actual HTML tag anywhere "
                    "(no '<tagname', '</tagname', or '<!DOCTYPE'/comment pattern) - its content "
                    "is almost certainly a different file's content (e.g. a sibling .js/.css "
                    "file) written under this filename by mistake, not a real (even minimal) "
                    "HTML document. Regenerate this file with actual HTML markup."
                )
        return None


# Extensions where a literal backtick can NEVER be legitimate source syntax
# - deliberately excludes Ruby (a real command-execution operator,
# `` `ls` ``) and JS/TS (a real template-literal delimiter). Every other
# language here treats a bare backtick as a hard syntax error.
_BACKTICK_ILLEGAL_EXTENSIONS = {
    ".py", ".java", ".kt", ".kts", ".groovy", ".go", ".rs", ".cs",
    ".c", ".cc", ".cpp", ".h", ".hpp",
}


class MarkdownInlineCodeLeakCheck(StaticCheck):
    """Catches a literal backtick character - Markdown's inline-code
    delimiter, e.g. the backticks around `displayName()` in a model's own
    prose - ending up as literal file content in a language where a bare
    backtick is never valid source syntax at all. Not a wording pattern
    (see find_explanatory_prose_contamination in file_resolution.py for
    that, which matches a small set of anchored sentence-starter phrases) -
    a purely structural signal: this character simply cannot appear outside
    a comment/string in these languages' real grammar, regardless of what
    the leaked prose actually says.

    Found live, PRV-03 hardened (2026-08-27): a targeted repair's FULL FILE
    CONTENT response for Customer.java began with the model's own inline-
    code-formatted analysis text instead of real Java - javac failed at
    line 1 with "class, interface, enum, or record expected" plus two
    "illegal character: '`'" errors on that same line. The existing
    find_explanatory_prose_contamination check didn't fire: this
    incident's actual wording didn't match any of its anchored sentence-
    starter patterns, so a full compile-retry cycle was burned on content
    that could never have been valid source. This check doesn't try to
    guess the model's phrasing - it catches the structural impossibility
    instead, which generalizes across whatever prose wraps the backtick.

    Deliberately excludes Ruby and JS/TS - the two mainstream languages
    where this character is legitimate source syntax (see
    _BACKTICK_ILLEGAL_EXTENSIONS above)."""

    name = "markdown_inline_code_leak"

    # Python 3.12+'s PEP 701 tokenizer emits FSTRING_START/MIDDLE/END for an
    # f-string's own literal text instead of a single STRING token - without
    # these, an f-string's literal content (which can legitimately contain a
    # backtick-like substring) is never blanked and falsely trips this check
    # on valid Python source. getattr() with a None default keeps this
    # working on Python 3.10/3.11, where these constants don't exist.
    _BLANKABLE_TOKEN_TYPES = tuple(
        token_type for token_type in (
            tokenize.STRING, tokenize.COMMENT,
            getattr(tokenize, "FSTRING_START", None),
            getattr(tokenize, "FSTRING_MIDDLE", None),
            getattr(tokenize, "FSTRING_END", None),
        ) if token_type is not None
    )

    @staticmethod
    def _python_executable_regions(content: str) -> Optional[str]:
        """Return Python with strings/comments blanked, or None if invalid.

        Token positions are preserved closely enough for the textual leak
        diagnostic while valid docstrings, ordinary strings, and comments
        are excluded from consideration deterministically.
        """
        try:
            ast.parse(content)
            tokens = tokenize.generate_tokens(io.StringIO(content).readline)
            lines = content.splitlines(keepends=True)
            for token in tokens:
                if token.type not in MarkdownInlineCodeLeakCheck._BLANKABLE_TOKEN_TYPES:
                    continue
                (start_line, start_col), (end_line, end_col) = token.start, token.end
                for line_index in range(start_line - 1, end_line):
                    line = lines[line_index]
                    left = start_col if line_index == start_line - 1 else 0
                    right = end_col if line_index == end_line - 1 else len(line.rstrip("\r\n"))
                    lines[line_index] = line[:left] + (" " * max(0, right - left)) + line[right:]
            return "".join(lines)
        except (SyntaxError, tokenize.TokenError, IndentationError):
            return None

    def check(self, files: Dict[str, str]) -> Optional[str]:
        for filepath, content in sorted(files.items()):
            extension = os.path.splitext(filepath)[1].lower()
            if extension not in _BACKTICK_ILLEGAL_EXTENSIONS:
                continue
            scan_content = content or ""
            if extension == ".py":
                tokenized = self._python_executable_regions(scan_content)
                if tokenized is not None:
                    scan_content = tokenized
            for line_number, line in enumerate(scan_content.splitlines(), start=1):
                stripped = line.lstrip()
                if not stripped or stripped.startswith(("//", "#", "/*", "*", "--")):
                    continue
                if "`" in line:
                    return (
                        f"{filepath} line {line_number} contains a literal backtick character - "
                        "Markdown's inline-code delimiter, never valid source syntax in this "
                        "language - almost always a sign the model's own prose/analysis text (or "
                        "a Markdown-formatted explanation) leaked into the file's actual content "
                        "instead of real code: "
                        f"{stripped[:160]!r}. Regenerate this file's content as plain source, with "
                        "no inline-code formatting or explanatory text embedded in it."
                    )
        return None


def _extract_balanced_block(content: str, open_brace_index: int) -> Optional[str]:
    """content[open_brace_index] must be '{'. Returns the substring from
    that brace through its matching close brace (inclusive), tracking depth
    naively (no string/comment awareness - callers that need exact statement
    counts should re-run _strip_java_comments_and_strings on the result
    first, matching this module's existing convention). None if the braces
    never balance (truncated/malformed content)."""
    depth = 0
    for i in range(open_brace_index, len(content)):
        if content[i] == "{":
            depth += 1
        elif content[i] == "}":
            depth -= 1
            if depth == 0:
                return content[open_brace_index:i + 1]
    return None


# Test-file detection by filename convention only (cheap, matches this
# module's existing marker-file-existence philosophy) - real test-runner
# frameworks for each ecosystem already enforce this same naming, so it's
# not a guess: JUnit/Maven Surefire only picks up *Test.java/*Tests.java/
# *IT.java by default, pytest only collects test_*.py/*_test.py, RSpec only
# collects *_spec.rb.
_TEST_FILE_RE = re.compile(
    r"(^|/)(test_[^/]+\.py|[^/]+_test\.py|[^/]+_spec\.rb|[^/]+Test\.java|[^/]+Tests\.java|[^/]+IT\.java)$"
)


class VacuousTestAssertionCheck(StaticCheck):
    """Catches a generated/modified test whose only "check" is a
    constant-truth assertion - assertTrue(true)/assertFalse(false) (JUnit),
    assertTrue(True)/assertFalse(False) (Python unittest), or a bare
    `assert True` (pytest) - which passes unconditionally regardless of
    whatever production behavior the test method's name claims to verify.
    This is test-evasion, not prose contamination: Maven/pytest and Kriya's
    own compile+test Quality Gate both report PASSED with zero actual
    coverage of the intended behavior.

    Found live, PRV-03 legacy (2026-08-27): CustomerControllerTest.java's
    testDetailsWithMiddleName/testDetailsWithOnlyFirstNameAndLastName/
    testDetailsWithOnlyFirstName/testDetailsWithEmptyStrings each construct
    a mock CustomerService and a CustomerController, then never call the
    controller at all - just `assertTrue(true); // Placeholder - actual
    implementation would require controller refactor`. A production coding
    agent must not ship a test that passes independent of whether the
    feature it claims to verify exists.

    Deliberately scoped to test files only (_TEST_FILE_RE, by filename
    convention) so a legitimate assertTrue(true)-shaped expression appearing
    anywhere in ordinary application code never false-positives."""

    name = "vacuous_test_assertion"

    _VACUOUS_PATTERNS = (
        re.compile(r"\bassert(?:True)?\s*\(\s*true\s*\)", re.IGNORECASE),   # JUnit assertTrue(true)/assert(true)
        re.compile(r"\bassertFalse\s*\(\s*false\s*\)", re.IGNORECASE),      # JUnit assertFalse(false)
        re.compile(r"\bassertTrue\s*\(\s*True\s*\)"),                       # Python unittest
        re.compile(r"\bassertFalse\s*\(\s*False\s*\)"),                     # Python unittest
        re.compile(r"(?m)^\s*assert\s+True\s*(?:,.*)?$"),                   # bare pytest
    )

    def check(self, files: Dict[str, str]) -> Optional[str]:
        for filepath, content in sorted(files.items()):
            if not _TEST_FILE_RE.search(filepath):
                continue
            for pattern in self._VACUOUS_PATTERNS:
                m = pattern.search(content)
                if m:
                    return (
                        f"{filepath} contains a constant-truth assertion ({m.group(0).strip()!r}) "
                        "that passes unconditionally regardless of the actual production behavior "
                        "being tested. A generated test must fail when the intended production "
                        "behavior is absent or incorrect - replace this with a real assertion "
                        "against the actual method/component under test, or remove the test if it "
                        "cannot be written yet rather than faking a pass."
                    )
        return None


class TestMethodLacksVerificationCheck(StaticCheck):
    """Catches a @Test method whose body contains NO assertion/verification
    call at all - not even a vacuous constant-truth one (see
    VacuousTestAssertionCheck above for that narrower, stronger signal) - so
    it passes unconditionally regardless of the code under test. The same
    underlying test-evasion failure mode found live in PRV-03 legacy
    (2026-08-27): several test methods there construct a mock service and
    controller, comment that testing "would require a controller refactor",
    and (in the vacuous-assertion cases) fall back to assertTrue(true) - but
    the general shape (a @Test method that never calls any assertion at
    all) is broader than that one confirmed line and worth its own gate.

    A method carrying `@Test(expected = ...)` or containing
    `assertThrows(`/`fail(`/`verify(` counts as verifying (exception-based
    and Mockito-style checks legitimately don't need a bare assert* call).
    Java/JUnit-specific, matching this module's established scope - a
    pytest/RSpec equivalent would need its own detection shape, not guessed
    at here."""

    name = "test_method_lacks_verification"

    _JAVA_TEST_METHOD_RE = re.compile(
        r"@Test\b([^\n]*)\n\s*(?:public|protected|private)?\s*[\w<>\[\],\.\s]+\s+(\w+)\s*"
        r"\([^)]*\)\s*(?:throws[^{]+)?\{"
    )
    _VERIFIES_RE = re.compile(
        r"\bassert\w*\s*\(|\bAssertions\.|\bfail\s*\(|\bverify\s*\(|\bassertThrows\b|\bexpectThrows\b",
        re.IGNORECASE,
    )

    def check(self, files: Dict[str, str]) -> Optional[str]:
        for filepath, content in sorted(files.items()):
            if not filepath.endswith(".java") or not _TEST_FILE_RE.search(filepath):
                continue
            for m in self._JAVA_TEST_METHOD_RE.finditer(content):
                annotation_args, method_name = m.group(1), m.group(2)
                if "expected" in annotation_args:
                    continue
                body = _extract_balanced_block(content, m.end() - 1)
                if body is None:
                    continue
                if not self._VERIFIES_RE.search(_strip_java_comments_and_strings(body)):
                    return (
                        f"{filepath}'s {method_name}() is a @Test method with no assertion or "
                        "verification call anywhere in its body - it passes unconditionally "
                        "regardless of whether the intended production behavior exists or is "
                        "correct. A generated test must fail when the behavior it claims to test "
                        "is absent or incorrect; add a real assertion or remove the test."
                    )
        return None


class TestOverridesSubjectUnderTestCheck(StaticCheck):
    """Catches a test that creates an ANONYMOUS SUBCLASS of the class it
    claims to be testing and overrides the very method under test with a
    reimplementation of that method's own logic, instead of exercising the
    real production method. A test built this way can pass even when the
    real production method is completely broken, since the override never
    calls into it - the test only proves the test's OWN duplicate logic is
    self-consistent.

    Found live, PRV-03 hardened (2026-08-27): several tests in
    CustomerServiceTest.java override CustomerController.details(...) with
    a copy of the same response-building logic
    (`m.put("displayName", c.displayName())`, ...) instead of calling the
    real CustomerController the way that same file's own first test
    correctly does. Distinct from mocking a genuine COLLABORATOR (the same
    file legitimately overrides CustomerService.find(...) to return fixed
    test data - a normal, one-line test double) - this fires only on an
    override of a class the SAME FILE ALSO instantiates plainly elsewhere
    (proof, from the file's own content, that the class is being directly
    tested here, not merely used as a collaborator), and only when the
    override body is non-trivial (3+ statements) rather than a one-line
    delegate/stub - matching the real incident's shape (a multi-statement
    reconstruction of the production method), not penalizing every override
    indiscriminately. Deliberately NOT keyed off the test file's own name
    (e.g. assuming "FooTest.java" tests "Foo") - the real PRV-03 incident's
    file is misleadingly named CustomerServiceTest.java while its first,
    correct test actually instantiates CustomerController plainly; a
    filename-derived subject would have missed exactly the file that
    proves this check is needed.

    Java/JUnit-specific (the confirmed incident's own shape) - a Python/
    Ruby equivalent (monkey-patching or subclassing the subject under test
    in a mock) would need its own detection, not guessed at here."""

    name = "test_overrides_subject_under_test"

    _NEW_INSTANCE_RE = re.compile(r"new\s+(\w+)\s*\([^()]*\)\s*(\{|;)")
    _OVERRIDE_METHOD_RE = re.compile(
        r"@Override\s*(?:public|protected)?\s*[\w<>\[\],\.\s]+\s+(\w+)\s*\([^)]*\)\s*(?:throws[^{]+)?\{"
    )

    def check(self, files: Dict[str, str]) -> Optional[str]:
        for filepath, content in sorted(files.items()):
            if not filepath.endswith(".java") or not _TEST_FILE_RE.search(filepath):
                continue
            plainly_instantiated = set()
            anon_subclasses = []
            for m in self._NEW_INSTANCE_RE.finditer(content):
                class_name, delimiter = m.group(1), m.group(2)
                if delimiter == ";":
                    plainly_instantiated.add(class_name)
                else:
                    anon_subclasses.append((class_name, m.end() - 1))
            for class_name, brace_index in anon_subclasses:
                if class_name not in plainly_instantiated:
                    continue
                block = _extract_balanced_block(content, brace_index)
                if not block:
                    continue
                for override_match in self._OVERRIDE_METHOD_RE.finditer(block):
                    method_name = override_match.group(1)
                    body = _extract_balanced_block(block, override_match.end() - 1)
                    if not body:
                        continue
                    statement_count = _strip_java_comments_and_strings(body).count(";")
                    if statement_count >= 3:
                        return (
                            f"{filepath} creates an anonymous subclass of {class_name} - a class "
                            f"this same file also instantiates PLAINLY elsewhere, proving it's the "
                            f"real subject under test here, not just a collaborator - and "
                            f"overrides its own {method_name}(...) method with a "
                            f"{statement_count}-statement reimplementation instead of exercising "
                            f"the real {class_name}.{method_name}(...). This test can pass even if "
                            f"the real {class_name} is broken. Instantiate the real {class_name} "
                            "directly (as this file's own other test(s) do) and mock only genuine "
                            "collaborators, not the class under test itself."
                        )
        return None


# MA7.5 - real markers for each ecosystem PolymorphicValidator itself
# already recognizes (kriya/tools/validate.py::_detect_stack, java/ruby/
# python) plus npm/go - deliberately the SAME marker-file-existence
# approach, not content parsing, so this stays cheap and has the same
# false-positive profile as the compile-gate's own stack detection.
# Ecosystem NAME here is purely a label for the violation message - the
# check itself never branches on which two ecosystems are involved (no
# "Django vs Spring"/"Python vs Maven" pairwise logic - MA6 spec section
# 72's own two named examples are just instances of one general rule:
# an ALREADY-ESTABLISHED build ecosystem shouldn't get a competing one
# silently introduced).
_ECOSYSTEM_MARKERS: Dict[str, Tuple[str, ...]] = {
    "java (maven)": ("pom.xml",),
    "java (gradle)": ("build.gradle", "build.gradle.kts"),
    "ruby": ("Gemfile", "Rakefile"),
    "python": ("requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "Pipfile"),
    "npm": ("package.json",),
    "go": ("go.mod",),
}


def _ecosystem_for_marker(filepath: str) -> Optional[str]:
    basename = os.path.basename(filepath)
    for ecosystem, markers in _ECOSYSTEM_MARKERS.items():
        if basename in markers:
            return ecosystem
    return None


# MA7.5's own honest scope note (find_established_stack_drift's docstring,
# 2026-08-24): "a first-milestone goal-text-vs-generated-language mismatch
# ... intentionally out of scope". Closed 2026-08-25 (external review, P1) -
# _MARKER_ECOSYSTEM_FAMILY groups the marker-file ecosystems above (which
# distinguish maven/gradle - a real distinction for FILE drift) into the
# coarser LANGUAGE family a human names in a goal ("Java" doesn't imply
# maven vs gradle; the goal-text side can't know that distinction, only the
# broader family). _GOAL_FAMILY_KEYWORDS is deliberately narrow and
# high-precision-only, framework names and unambiguous language names ONLY -
# no bare, common-English-collidable words (e.g. "go" alone is excluded;
# "golang" is not, matching this session's own hard-won lesson from
# _EXPLICIT_TEST_REQUEST_RE's three real false-positive incidents - a
# missed catch here is only as bad as before this fix; a false positive
# would burn an entire retry budget on a goal that never asked for this
# check to fire at all, the exact failure mode already paid for once today).
_MARKER_ECOSYSTEM_FAMILY: Dict[str, str] = {
    "java (maven)": "java",
    "java (gradle)": "java",
    "ruby": "ruby",
    "python": "python",
    "npm": "node",
    "go": "go",
}

_GOAL_FAMILY_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "java": ("java", "spring", "springboot", "spring boot", "maven", "gradle"),
    "python": ("python", "django", "flask", "fastapi"),
    "ruby": ("ruby", "rails", "sinatra"),
    "node": ("node\\.js", "nodejs", "express\\.js", "npm"),
    "go": ("golang",),
}
_GOAL_FAMILY_PATTERNS: Dict[str, "re.Pattern"] = {
    family: re.compile(r"\b(?:" + "|".join(keywords) + r")\b", re.IGNORECASE)
    for family, keywords in _GOAL_FAMILY_KEYWORDS.items()
}


@dataclass(frozen=True)
class StackContract:
    languages: Tuple[str, ...]
    frameworks: Tuple[str, ...]
    substitution_policy: str = "FORBID"
    authority: str = "USER_GOAL"


def _goal_declared_family(goal: str) -> Optional[str]:
    """The single, unambiguous language family the goal text names - None
    if the goal names zero families (nothing to check against) OR two-plus
    DIFFERENT families (an intentionally mixed-stack goal, e.g. "a Python
    service called from a Java client" - ambiguous on purpose, not this
    check's business to referee)."""
    matched = {family for family, pattern in _GOAL_FAMILY_PATTERNS.items() if pattern.search(goal or "")}
    return next(iter(matched)) if len(matched) == 1 else None


def derive_stack_contract(goal: str) -> Optional[StackContract]:
    family = _goal_declared_family(goal)
    if family is None:
        return None
    lowered = (goal or "").lower()
    frameworks = tuple(name for name in ("django", "spring") if name in lowered)
    return StackContract(languages=(family,), frameworks=frameworks)


def log_stack_contract_boundary(
    boundary: str, contract: Optional[StackContract], violation: Optional[str],
) -> None:
    """Structured STACK_CONTRACT_BOUNDARY log line, shared by every call site
    that runs validate_stack_contract_artifacts (candidate/plan/terminal
    boundaries) - was duplicated verbatim at each site, risking drift
    between them on a future schema change."""
    logger.info(
        "STACK_CONTRACT_BOUNDARY %s",
        json.dumps({
            "boundary": boundary,
            "languages": list(getattr(contract, "languages", ())),
            "frameworks": list(getattr(contract, "frameworks", ())),
            "decision": "REJECT" if violation else "PASS",
        }, sort_keys=True),
    )


def validate_stack_contract_artifacts(
    contract: Optional[StackContract], artifact_paths: Iterable[str],
) -> Optional[str]:
    """Validate only artifacts inside the requested change boundary."""
    if contract is None or contract.substitution_policy != "FORBID":
        return None
    requested = contract.languages[0]
    for filepath in sorted(artifact_paths):
        ecosystem = _ecosystem_for_marker(filepath)
        family = _MARKER_ECOSYSTEM_FAMILY.get(ecosystem) if ecosystem else None
        normalized = filepath.replace("\\", "/").lower()
        if family is None and normalized.endswith((".java", ".kt", ".kts")):
            family = "java"
        elif family is None and normalized.endswith(".py"):
            family = "python"
        if family is not None and family != requested:
            return (
                f"STACK_CONTRACT_VIOLATION: {filepath} belongs to {family!r}, but the "
                f"authoritative USER_GOAL requests {requested!r}; substitution_policy=FORBID"
            )
    return None


def find_goal_stack_mismatch(goal: str, all_files_written: Iterable[str]) -> Optional[str]:
    """The first-milestone counterpart to find_established_stack_drift
    above: that check compares NEW writes against an ALREADY-ESTABLISHED
    marker, so it structurally cannot fire on a genuinely first-ever
    milestone/goal (nothing established yet to contradict). This compares
    the GOAL TEXT's own declared language family against whatever
    ecosystem marker THIS SAME attempt's writes establish - "goal said
    Django, generated architecture wrote pom.xml" is now catchable even
    with zero established history. A weaker, keyword-based signal than the
    established-marker check (goal text is natural language, not a real
    file on disk) - see _goal_declared_family's own docstring for why it's
    deliberately conservative (fires on unambiguous single-family goals
    only). Only ONE mismatch is ever reported (first found, sorted
    iteration), matching this module's own "first violation wins"
    convention."""
    contract = derive_stack_contract(goal)
    declared_family = contract.languages[0] if contract else None
    if declared_family is None:
        return None

    artifacts = tuple(all_files_written)
    contract_violation = validate_stack_contract_artifacts(
        contract, (path for path in artifacts if _ecosystem_for_marker(path) is None),
    )
    if contract_violation:
        return contract_violation

    for filepath in sorted(artifacts):
        ecosystem = _ecosystem_for_marker(filepath)
        if ecosystem is None:
            continue
        written_family = _MARKER_ECOSYSTEM_FAMILY[ecosystem]
        if written_family != declared_family:
            return (
                f"the goal explicitly names '{declared_family}', but {filepath} establishes "
                f"a '{written_family}' ({ecosystem}) project instead. Generated architecture "
                "must match the ecosystem the goal actually asked for - if this goal genuinely "
                "requires a different or additional ecosystem, say so explicitly rather than "
                "silently substituting one."
            )
    return None


def find_established_stack_drift(worktree_path: str, all_files_written: Iterable[str]) -> Optional[str]:
    """MA7.5 (MA6 spec section 72's "Django doesn't drift to Spring, Python
    doesn't invent Maven layout" regression category) - generic, marker-
    based, no per-framework-pair logic. Fires when this attempt's own
    writes (`all_files_written`) introduce a NEW build-ecosystem marker
    file (a fresh pom.xml, package.json, ...) into a worktree that ALREADY
    has a DIFFERENT ecosystem's marker established from BEFORE this
    attempt - the generated content has silently switched the project's
    real build identity, not merely added files within it.

    Deliberately does not look at goal text at all: for a workspace with
    NOTHING established yet (a brand-new first milestone), there is no
    established marker to contradict, so this check correctly never fires -
    matching every other deterministic check in this module (best-effort,
    real-evidence-only, never a guess). Catching a first-milestone
    goal-vs-generated-language mismatch would need goal-text analysis,
    which is a materially different, weaker signal (keyword-based, not
    file-existence-based) - intentionally out of scope here rather than
    forcing a fit; see this function's own caller for how that gap is
    tracked.

    Only ONE established/newly-written ecosystem pair is ever reported (the
    first mismatch found, deterministically via sorted iteration) - matching
    this module's "first violation wins" convention, not an exhaustive
    report of every marker present."""
    written = set(all_files_written)

    # "Established" = a real top-level marker file physically on disk in
    # the worktree that this attempt did NOT itself write - i.e. genuinely
    # pre-existing, from an earlier milestone/attempt or the real workspace
    # this worktree was synced from. Excluding `written` here is the whole
    # point: without it, this attempt's OWN new marker would immediately
    # count as "established" against itself the instant run_static_checks
    # reads it back (this check runs AFTER all files are written).
    established: Dict[str, str] = {}
    for entry in sorted(os.listdir(worktree_path)) if os.path.isdir(worktree_path) else []:
        if entry in written:
            continue
        full_path = os.path.join(worktree_path, entry)
        if not os.path.isfile(full_path):
            continue
        ecosystem = _ecosystem_for_marker(entry)
        if ecosystem:
            established[ecosystem] = entry

    if not established:
        return None

    for filepath in sorted(written):
        new_ecosystem = _ecosystem_for_marker(filepath)
        if new_ecosystem is None or new_ecosystem in established:
            continue
        existing_ecosystem, existing_marker = next(iter(established.items()))
        return (
            f"{filepath} introduces a new '{new_ecosystem}' build marker, but this "
            f"workspace already has an established '{existing_ecosystem}' project "
            f"({existing_marker} exists from before this attempt). Generated content "
            "must not silently switch the project's real build ecosystem - if this "
            "goal genuinely requires adding a second, different-ecosystem component, "
            "say so explicitly rather than introducing it as an apparent replacement."
        )
    return None


STATIC_CHECKS = [
    IgniteMethodMixingCheck(),
    IgniteDuplicateSpringContextCheck(),
    IgniteUnclosedResourceCheck(),
    BareVerificationMarkerCheck(),
    TestContradictsVerificationMarkerCheck(),
    MismatchedFileTypeContentCheck(),
    VacuousTestAssertionCheck(),
    TestMethodLacksVerificationCheck(),
    TestOverridesSubjectUnderTestCheck(),
    MarkdownInlineCodeLeakCheck(),
]


def run_static_checks(
    worktree_path: str, all_files_written: Iterable[str], overrides: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """Reads every known-written file back from the worktree (nothing keeps a
    Dict[str,str] of full content in memory this late in run_attempt() - it's
    read-and-discarded per-file during the earlier write loop) and runs every
    registered check against the combined set. Returns the first violation
    message found, or None. Best-effort: a file that can't be read is silently
    skipped rather than failing the whole check.

    overrides (optional): filepath -> content pairs used INSTEAD OF reading
    that file from disk - lets a caller check a proposed-but-not-yet-written
    candidate against the exact same registered checks a real write would
    run, without writing it first. Added 2026-08-17 for
    kriya/workflow/attempt.py's deterministic-validation-first override of
    the diagnosis-mismatch pre-flight check - see that call site's own
    comment for the full incident."""
    overrides = overrides or {}
    files: Dict[str, str] = {}
    for filepath in all_files_written:
        if filepath in overrides:
            files[filepath] = overrides[filepath]
            continue
        full_path = os.path.join(worktree_path, filepath)
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                files[filepath] = fh.read()
        except Exception:
            continue

    for check in STATIC_CHECKS:
        violation = check.check(files)
        if violation:
            return f"[{check.name}] {violation}"
    return None
