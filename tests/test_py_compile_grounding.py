"""P-A (S7 close #2, 2026-10-25): Python syntax-error source grounding.

Under mandatory containment the Python compile gate returns the raw
`python3 -m py_compile` report, not the host branch's normalized
'Syntax error in X.py line N: ...'. `extract_error_source_locations` must ground
BOTH representations, and must not read an ordinary traceback frame as a
syntax-error location.

The native fixtures are real CPython output (measured on 3.14.6 in the rebuild
venv); `test_a_real_py_compile_report_is_grounded` re-measures it on whatever
interpreter runs the suite so the fixtures cannot drift from the real shape.
"""
import subprocess
import sys

from kriya.workflow.failure_grounding import (
    _build_error_source_context,
    extract_error_source_locations,
)

NATIVE_NO_COLON = (
    '  File "nocolon.py", line 1\n'
    "    def f()\n"
    "           ^\n"
    "SyntaxError: expected ':'\n"
)
NATIVE_UNCLOSED = (
    '  File "unclosed.py", line 1\n'
    "    def f(\n"
    "         ^\n"
    "SyntaxError: '(' was never closed\n"
)
# Final frame of an import-time traceback: the importer frame ends ', in <module>'.
IMPORT_TIME = (
    "Traceback (most recent call last):\n"
    '  File "/w/main.py", line 1, in <module>\n'
    "    import unclosed\n"
    '  File "/w/unclosed.py", line 1\n'
    "    def f(\n"
    "         ^\n"
    "SyntaxError: '(' was never closed\n"
)
RUNTIME = (
    "Traceback (most recent call last):\n"
    '  File "/w/rt.py", line 3, in <module>\n'
    "    g()\n"
    "    ~^^\n"
    '  File "/w/rt.py", line 2, in g\n'
    "    return undefined_name\n"
    "           ^^^^^^^^^^^^^^\n"
    "NameError: name 'undefined_name' is not defined\n"
)


def test_the_normalized_host_representation_still_grounds():
    assert extract_error_source_locations(
        "Syntax error in greet.py line 6: [VERIFICATION] PASS (invalid syntax)"
    ) == [("greet.py", 6)]


def test_raw_py_compile_output_grounds_the_right_file_and_line():
    assert extract_error_source_locations(NATIVE_NO_COLON) == [("nocolon.py", 1)]
    assert extract_error_source_locations(NATIVE_UNCLOSED) == [("unclosed.py", 1)]


def test_a_native_report_with_an_absolute_path_and_crlf_grounds_the_basename():
    text = NATIVE_NO_COLON.replace('"nocolon.py"', '"/var/work/src/calc.py"').replace("\n", "\r\n")
    assert extract_error_source_locations(text) == [("calc.py", 1)]


def test_indentation_and_tab_error_classes_in_the_native_traceback_shape_ground():
    # SyntaxError subclasses print in the identical File/line shape on interpreters
    # that route them through the traceback formatter.
    for exc in ("IndentationError: expected an indented block", "TabError: inconsistent use of tabs"):
        text = '  File "m.py", line 7\n    x = 1\n    ^\n' + exc + "\n"
        assert extract_error_source_locations(text) == [("m.py", 7)]


def test_the_native_and_normalized_shapes_and_javac_ground_together_in_order():
    text = (
        "Syntax error in greet.py line 6: boom (invalid syntax)\n"
        + NATIVE_NO_COLON
        + "[ERROR] .../App.java:[10,5] incompatible types\n"
    )
    assert extract_error_source_locations(text) == [("greet.py", 6), ("nocolon.py", 1), ("App.java", 10)]


def test_only_the_syntax_error_frame_of_an_import_time_traceback_is_grounded():
    # The importer frame (', in <module>') is NOT a syntax-error location.
    assert extract_error_source_locations(IMPORT_TIME) == [("unclosed.py", 1)]


def test_an_ordinary_runtime_traceback_is_never_attributed_as_a_syntax_error():
    assert extract_error_source_locations(RUNTIME) == []


def test_a_runtime_frame_whose_exception_is_a_user_raised_syntax_error_is_not_grounded():
    text = (
        "Traceback (most recent call last):\n"
        '  File "/w/p.py", line 4, in parse\n'
        '    raise SyntaxError("bad token")\n'
        "SyntaxError: bad token\n"
    )
    assert extract_error_source_locations(text) == []


def test_malformed_native_output_stays_ungrounded():
    malformed = [
        '  File "x.py", line abc\n    foo\nSyntaxError: x\n',               # non-numeric line
        '  File "x.py", line 3\n    foo\nValueError: x\n',                  # not a SyntaxError family
        '  File "x.py", line 3\n    foo\nsome free text\nSyntaxError: x\n',  # a non-indented line breaks the frame
        '  File "x.py", line 3\n',                                           # frame with no exception line
        '  File "x.txt", line 3\n    foo\nSyntaxError: x\n',                # not a .py file
        '  File "<string>", line 3\n    foo\nSyntaxError: x\n',             # no file to attribute
        '  File "x.py", line 3\n    a\n    b\n    c\n    d\n    e\nSyntaxError: x\n',  # > 4 context lines
        "SyntaxError: expected ':'\n",                                       # exception line alone
    ]
    for text in malformed:
        assert extract_error_source_locations(text) == [], text


def test_a_real_py_compile_report_is_grounded(tmp_path):
    src = tmp_path / "invalid.py"
    src.write_text("def f()\n    return 1\n")
    proc = subprocess.run(
        [sys.executable, "-m", "py_compile", src.name], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert proc.returncode != 0 and "SyntaxError" in proc.stderr
    assert extract_error_source_locations(proc.stderr) == [("invalid.py", 1)]


def test_raw_py_compile_output_reaches_the_developer_as_an_anchored_source_window(tmp_path):
    (tmp_path / "nocolon.py").write_text("import os\n\ndef f()\n    return 1\n")
    text = NATIVE_NO_COLON.replace("line 1", "line 3")
    context = _build_error_source_context(str(tmp_path), text, known_files=["nocolon.py"])
    assert ">> 3: def f()" in context["nocolon.py"]
