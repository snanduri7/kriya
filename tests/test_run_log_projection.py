"""RUN LOG PROJECTION (owner ruling 2026-11-07, the cockpit's "Kriya Log"): the REAL per-run application log, paged, cursor-based, bounded and redacted - never a path from a caller, never a phase guessed from text.

MEASURED: a desktop run already writes ``<log dir>/runs/<run id>/kriya.log`` (95 lines for a 1m20 run) through the logging owner's ``RedactingFormatter``; the line format is
``YYYY-MM-DD HH:MM:SS,mmm LEVEL logger: message`` with stage banners as continuation lines and a ``# Kriya run <id> workspace <path>`` header."""
import os

import pytest

from kriya.application import run_log
from kriya.application.run_log import MAX_LIMIT, NO_RUN_LOG, RUN_LOG_UNREADABLE, parse_cursor, read_run_log

RUN = "20261107T182659-f963bb85"
LOG = (
    "# Kriya run 20261107T182659-f963bb85 workspace /Users/x/proj\n"
    "2026-11-07 18:27:01,511 INFO kriya.core.kernel: Starting Kriya Kernel...\n"
    "2026-11-07 18:27:03,208 INFO kriya.core.llm: Sending completion request to local LLM [Model: qwen3-coder:30b, Stream: True]\n"
    "2026-11-07 18:27:03,300 INFO kriya.workflow.workflow: \n"
    "======================================================================\n"
    "                               PLANNING                               \n"
    "======================================================================\n"
    "2026-11-07 18:27:35,255 WARNING kriya.control.checkpoint: Checkpoint saved (stage=plan)\n"
    "2026-11-07 18:28:21,385 ERROR kriya.core.kernel: Stopping MCP servers...\n"
)


@pytest.fixture
def logs(tmp_path):
    directory = tmp_path / "logs"
    (directory / "runs" / RUN).mkdir(parents=True)
    path = directory / "runs" / RUN / "kriya.log"
    path.write_text(LOG, encoding="utf-8")
    return directory, path


def _read(directory, offset=0, limit=200):
    return read_run_log(str(directory), RUN, offset, limit)


def test_the_real_line_format_is_parsed_into_exactly_its_guaranteed_fields(logs):
    page = _read(logs[0])
    assert page["available"] is True and page["reason"] is None and page["at_end"] is True and page["reset"] is False
    assert [(r["time"], r["level"], r["logger"]) for r in page["records"]] == [
        ("2026-11-07 18:27:01,511", "INFO", "kriya.core.kernel"), ("2026-11-07 18:27:03,208", "INFO", "kriya.core.llm"),
        ("2026-11-07 18:27:03,300", "INFO", "kriya.workflow.workflow"), ("2026-11-07 18:27:35,255", "WARNING", "kriya.control.checkpoint"),
        ("2026-11-07 18:28:21,385", "ERROR", "kriya.core.kernel")]
    assert set(page["records"][0]) == {"offset", "time", "level", "logger", "message"}          # no phase, no component invented
    assert page["records"][0]["message"] == "Starting Kriya Kernel..."
    assert page["records"][1]["message"].endswith("Stream: True]")


def test_a_banner_belongs_to_the_record_before_it_and_the_header_line_is_not_a_record(logs):
    banner = _read(logs[0])["records"][2]["message"]
    assert "PLANNING" in banner and banner.count("=") >= 60
    assert not any("# Kriya run" in r["message"] for r in _read(logs[0])["records"])


def test_paging_by_cursor_returns_every_record_exactly_once_in_order(logs):
    seen, cursor, pages = [], None, 0
    while True:
        page = _read(logs[0], parse_cursor(cursor), 2)
        seen += [r["time"] for r in page["records"]]
        cursor, pages = page["next_cursor"], pages + 1
        if page["at_end"]:
            break
        assert pages < 10
    assert seen == [r["time"] for r in _read(logs[0])["records"]] and pages >= 2
    assert _read(logs[0], parse_cursor(cursor))["records"] == []                                 # nothing new: an empty page at the end


def test_a_line_still_being_written_is_held_back_until_it_is_complete(logs):
    directory, path = logs
    first = _read(directory)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("2026-11-07 18:29:00,001 INFO kriya.x: half a li")                          # no newline yet
    assert _read(directory, parse_cursor(first["next_cursor"]))["records"] == []
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("ne\n")
    later = _read(directory, parse_cursor(first["next_cursor"]))
    assert [r["message"] for r in later["records"]] == ["half a line"]


def test_an_offset_beyond_the_file_restarts_from_the_beginning_and_says_so(logs):
    page = _read(logs[0], 10_000_000)
    assert page["reset"] is True and page["records"][0]["message"] == "Starting Kriya Kernel..."


def test_a_missing_log_is_an_answer_not_an_empty_log(tmp_path):
    page = read_run_log(str(tmp_path), RUN)
    assert (page["available"], page["reason"], page["records"]) == (False, NO_RUN_LOG, [])


def test_messages_are_redacted_again_and_hidden_reasoning_is_never_shown(logs):
    directory, path = logs
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("2026-11-07 18:30:00,000 INFO kriya.core.llm: fetch https://alice:hunter2@example.com/x done\n")
        handle.write("2026-11-07 18:30:01,000 INFO kriya.core.llm: answer <think>the secret plan is X</think> ok\n")
        handle.write("2026-11-07 18:30:02,000 INFO kriya.core.llm: <think>never closed " + "z" * 50 + "\n")
    messages = [r["message"] for r in _read(directory)["records"]][-3:]
    assert "hunter2" not in messages[0] and "alice" not in messages[0]
    assert messages[1] == "answer [model reasoning hidden] ok" and "secret plan" not in messages[1]
    assert messages[2] == "[model reasoning hidden]"


def test_a_message_is_cut_to_a_bound_and_a_page_to_the_record_limit(logs):
    directory, path = logs
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("2026-11-07 18:31:00,000 INFO kriya.x: " + "m" * 5000 + "\n")
    assert len(_read(directory)["records"][-1]["message"]) == run_log.MAX_MESSAGE_CHARS
    assert len(_read(directory, 0, 10_000)["records"]) <= MAX_LIMIT


def test_a_log_that_is_a_link_or_leaves_the_runs_directory_is_refused_never_read(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("2026-11-07 18:27:01,511 INFO kriya.x: TOP SECRET\n")
    directory = tmp_path / "logs"
    (directory / "runs" / RUN).mkdir(parents=True)
    os.symlink(secret, directory / "runs" / RUN / "kriya.log")                                    # the log file itself is a link
    page = _read(directory)
    assert page["available"] is False and page["reason"] == RUN_LOG_UNREADABLE and "TOP SECRET" not in str(page)
    other = tmp_path / "logs2"
    (other / "runs").mkdir(parents=True)
    os.symlink(tmp_path / "elsewhere", other / "runs" / RUN)                                      # the run directory is a link out of runs/
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "kriya.log").write_text("2026-11-07 18:27:01,511 INFO kriya.x: TOP SECRET\n")
    assert _read(other)["reason"] == RUN_LOG_UNREADABLE


@pytest.mark.parametrize("cursor", ["", "o", "1", "o-1", "o1.5", "../x", "o" + "9" * 13, 7, None.__class__])
def test_only_a_cursor_this_projection_issued_is_accepted(cursor):
    with pytest.raises(ValueError):
        parse_cursor(cursor)
    assert parse_cursor(None) == 0 and parse_cursor("o0") == 0 and parse_cursor("o123") == 123


# ---- the typed KUP operation -----------------------------------------------------------------------------------------------------------------------------------------------------
def _operation(monkeypatch, tmp_path, request):
    from kriya.config import AppConfig
    from kriya.kup.cli_ops import run_kup_operation

    monkeypatch.setenv("KRIYA_LOG_DIR", str(tmp_path / "logs"))
    return run_kup_operation(AppConfig(), {"operation": "run.log", **request})


def test_the_operation_returns_a_typed_page_of_the_runs_own_log_and_nothing_else(monkeypatch, tmp_path, logs):
    envelope = _operation(monkeypatch, logs[0].parent, {"run_id": RUN, "limit": 3})
    assert envelope["operation"] == "run.log" and envelope.get("error") is None
    data = envelope["data"]
    assert data["available"] is True and len(data["records"]) == 3 and data["at_end"] is False and data["next_cursor"].startswith("o")
    follow = _operation(monkeypatch, logs[0].parent, {"run_id": RUN, "cursor": data["next_cursor"]})["data"]
    assert [r["time"] for r in data["records"] + follow["records"]] == [r["time"] for r in _read(logs[0])["records"]]


@pytest.mark.parametrize("request_", [{"run_id": "../../etc/passwd"}, {"run_id": "x/y"}, {"run_id": RUN, "cursor": "../x"}, {"run_id": RUN, "limit": 0}, {"run_id": RUN, "limit": 501},
                                      {"run_id": RUN, "limit": True}, {"run_id": RUN, "limit": "5"}, {}])
def test_the_operation_refuses_a_bad_request_typed_before_reading_anything(monkeypatch, tmp_path, request_):
    envelope = _operation(monkeypatch, tmp_path, request_)
    assert envelope["error"]["code"] == "INVALID_REQUEST"


def test_the_transport_grammar_and_the_use_cases_own_bounds_agree():
    from kriya.kup import policy

    assert policy.RUN_LOG_CURSOR_PATTERN == run_log.CURSOR_RE.pattern
    assert (policy.RUN_LOG_DEFAULT_LIMIT, policy.RUN_LOG_MAX_LIMIT) == (run_log.DEFAULT_LIMIT, run_log.MAX_LIMIT)


def test_the_operation_takes_no_path_and_says_when_the_run_has_no_log(monkeypatch, tmp_path):
    envelope = _operation(monkeypatch, tmp_path, {"run_id": RUN})
    assert envelope["data"]["available"] is False and envelope["data"]["reason"] == NO_RUN_LOG
    from kriya.kup import serve

    assert serve.ARGUMENTS["run.log"] == frozenset({"run_id", "cursor", "limit"})
