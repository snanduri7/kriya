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


# `python3 -m py_compile` routes IndentationError/TabError through ONE line with no trailing newline (measured:
# python:3.12-slim and python:3.10-slim containers and the 3.14.6 host, identical text).
SORRY_INDENT = ("Sorry: IndentationError: expected an indented block after function definition on line 1 "
                "(indent.py, line 2)")
SORRY_TAB = "Sorry: TabError: inconsistent use of tabs and spaces in indentation (tab.py, line 3)"
SORRY_UNINDENT = ("Sorry: IndentationError: unindent does not match any outer indentation level "
                  "(unindent.py, line 4)")
# The same errors at import time print the full frame form (python:3.12-slim, `python3 imp.py`).
IMPORT_TIME_INDENT = (
    "Traceback (most recent call last):\n"
    '  File "/w/imp_i.py", line 1, in <module>\n'
    "    import indent\n"
    '  File "/w/indent.py", line 2\n'
    "    return 1\n"
    "    ^^^^^^\n"
    "IndentationError: expected an indented block after function definition on line 1\n"
)
IMPORT_TIME_TAB = (
    "Traceback (most recent call last):\n"
    '  File "/w/imp_t.py", line 1, in <module>\n'
    "    import tab\n"
    '  File "/w/tab.py", line 3\n'
    "    y=2\n"
    "TabError: inconsistent use of tabs and spaces in indentation\n"
)


def test_py_compile_indentation_and_tab_reports_ground_to_the_trailing_file_and_line():
    assert extract_error_source_locations(SORRY_INDENT) == [("indent.py", 2)]
    assert extract_error_source_locations(SORRY_TAB) == [("tab.py", 3)]
    assert extract_error_source_locations(SORRY_UNINDENT) == [("unindent.py", 4)]


def test_the_line_inside_an_indentation_message_is_not_the_location():
    # "... after function definition on line 1 (indent.py, line 2)": the error is at line 2, the BLOCK OPENER at 1.
    assert extract_error_source_locations(SORRY_INDENT) != [("indent.py", 1)]
    assert extract_error_source_locations(SORRY_INDENT + "\n") == [("indent.py", 2)]
    assert extract_error_source_locations(SORRY_INDENT + "\r\n") == [("indent.py", 2)]


def test_the_sorry_report_grounds_with_nothing_after_it_and_with_text_after_it():
    assert extract_error_source_locations(SORRY_TAB) == [("tab.py", 3)]                        # end of input
    assert extract_error_source_locations("noise\n" + SORRY_TAB + "\nmore\n") == [("tab.py", 3)]


def test_the_last_parenthesised_location_wins_when_the_message_quotes_another():
    text = "Sorry: IndentationError: see (other.py, line 9) for the opener (real.py, line 2)"
    assert extract_error_source_locations(text) == [("real.py", 2)]


def test_malformed_or_unrelated_sorry_lines_stay_ungrounded():
    malformed = [
        "Sorry: ValueError: boom (indent.py, line 2)",                       # not a SyntaxError family
        "Sorry: IndentationError: expected an indented block",               # no location
        "Sorry: IndentationError: x (indent.txt, line 2)",                   # not a .py file
        "Sorry: IndentationError: x (indent.py, line two)",                  # non-numeric line
        "Sorry: IndentationError: x (indent.py, line 2) and then more words",  # location does not end the report
        "IndentationError: x (indent.py, line 2)",                           # no py_compile 'Sorry:' prefix
        "sorry: indentationerror: x (indent.py, line 2)",                    # wrong case
    ]
    for text in malformed:
        assert extract_error_source_locations(text) == [], text


def test_the_import_time_indentation_and_tab_frames_ground_without_the_importer_frame():
    assert extract_error_source_locations(IMPORT_TIME_INDENT) == [("indent.py", 2)]
    assert extract_error_source_locations(IMPORT_TIME_TAB) == [("tab.py", 3)]


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


def test_real_py_compile_reports_are_grounded_for_every_syntax_error_kind(tmp_path):
    cases = {
        "nocolon.py": ("def f()\n    return 1\n", 1),
        "unclosed.py": ("x = (1,\n", 1),
        "indent.py": ("def f():\nreturn 1\n", 2),
        "tab.py": ("if True:\n\tx=1\n        y=2\n", 3),
        "unindent.py": ("class A:\n    def f(self):\n        pass\n      x = 1\n", 4),
    }
    for name, (source, line) in cases.items():
        (tmp_path / name).write_text(source)
        proc = subprocess.run(
            [sys.executable, "-m", "py_compile", name], cwd=tmp_path, capture_output=True, text=True, check=False
        )
        assert proc.returncode != 0, name
        assert extract_error_source_locations(proc.stderr) == [(name, line)], (name, proc.stderr)


def test_raw_py_compile_output_reaches_the_developer_as_an_anchored_source_window(tmp_path):
    (tmp_path / "nocolon.py").write_text("import os\n\ndef f()\n    return 1\n")
    text = NATIVE_NO_COLON.replace("line 1", "line 3")
    context = _build_error_source_context(str(tmp_path), text, known_files=["nocolon.py"])
    assert ">> 3: def f()" in context["nocolon.py"]
