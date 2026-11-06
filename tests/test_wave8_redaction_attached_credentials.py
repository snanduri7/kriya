"""WAVE8-CURL-ATTACHED-CREDENTIAL-REDACTION-001 (NEW-1): the ONE redaction owner hides the password of the ATTACHED forms of the
``user:password`` flags, in free text and in an argv list, and the runtime-command review refuses what it hides.

Measured defect (2026-11-06, redact_text before the fix): ``curl --user=u:PW``, ``--proxy-user=u:PW``, ``-uu:PW`` printed the password
unchanged (the separate forms ``-u u:PW`` / ``--user u:PW`` were already hidden), and ``commands_carry_secret`` was False for them, so the
review would have offered the raw password. The matrix below is red before / green after; the mutation tests restore the defect by
disabling each new rule and must fail the matrix.
"""
import re

import pytest

from kriya.foundation import redaction
from kriya.foundation.redaction import REDACTED, redact_text, redact_value
from kriya.policy.approval import commands_carry_secret

PW = "S3cr3tPW-wave8"

ATTACHED_TEXTS = [
    f"curl --user=user:{PW} https://h/x",
    f"curl --proxy-user=user:{PW} https://h/x",
    f"curl -uuser:{PW} https://h/x",
    f"curl -Uuser:{PW} https://h/x",
    f"curl -u:{PW} https://h/x",
    f"curl --user='user:{PW}' https://h/x",
    f'curl --user="user:{PW}" https://h/x',
    f"curl -u'user:{PW}' https://h/x",
    f'curl -u"user:{PW}" https://h/x',
    f"curl -sSuuser:{PW} https://h/x",
    f"curl -sSu user:{PW} https://h/x",
    f"curl -fsSLu user:{PW} https://h/x",
    f"curl -sSLU user:{PW} https://h/x",
    f"curl --oauth2-bearer={PW} https://h/x",
    f"bash -c 'curl --user=u:{PW} h'",
    f'["curl", "--user=u:{PW}", "h"]',
]
ATTACHED_ARGVS = [
    ["curl", f"--user=u:{PW}", "h"],
    ["curl", f"--proxy-user=u:{PW}", "h"],
    ["curl", f"-uu:{PW}", "h"],
    ["curl", f"-Uu:{PW}", "h"],
    ["curl", f"-sSuu:{PW}", "h"],
    ["curl", "-sSu", f"u:{PW}", "h"],
    ["curl", f"--oauth2-bearer={PW}", "h"],
]
# already hidden before the fix: they must stay hidden (regression of the existing contract)
SEPARATE_TEXTS = [f"curl -u user:{PW} h", f"curl --user user:{PW} h", f"curl --proxy-user user:{PW} h", f"wget --password={PW} h"]


@pytest.mark.parametrize("text", ATTACHED_TEXTS + SEPARATE_TEXTS)
def test_no_password_survives_redact_text(text):
    out = redact_text(text)
    assert PW not in out and REDACTED in out


@pytest.mark.parametrize("argv", ATTACHED_ARGVS)
def test_no_password_survives_an_argv_list_and_the_review_refuses_it(argv):
    assert PW not in str(redact_value(argv))
    assert commands_carry_secret([argv]) is True       # the runtime-command review is not offered raw bytes


def test_the_user_part_and_the_rest_of_the_command_stay_readable():
    assert redact_text(f"curl --user=alice:{PW} --max-time 5 https://h/x") == f"curl --user=alice:{REDACTED} --max-time 5 https://h/x"
    assert redact_text(f"curl -ualice:{PW} h") == f"curl -ualice:{REDACTED} h"
    assert redact_text(f"curl -u'alice:{PW}' h") == f"curl -u'alice:{REDACTED}' h"
    assert redact_value(["curl", f"--user=alice:{PW}"]) == ["curl", f"--user=alice:{REDACTED}"]


@pytest.mark.parametrize("text", ["curl -unique h", "curl --user=alice h", "curl -u alice h", "ls -uh", "tar -xvuf a.tar", "sort -u file",
                                  "curl --user-agent=x h", "curl -usomething h", "git push -u origin main"])
def test_text_without_a_password_is_untouched(text):
    assert redact_text(text) == text


@pytest.mark.parametrize("argv", [["curl", "--user=alice", "h"], ["sort", "-u", "f"], ["git", "push", "-u", "origin", "main"], ["curl", "-unique", "h"]])
def test_argv_without_a_password_is_untouched_and_reviewable(argv):
    assert redact_value(argv) == argv and commands_carry_secret([argv]) is False


def test_a_gate_outcome_with_an_attached_credential_argv_is_hidden_in_its_stored_json():
    """The channel the trace row and the evidence envelope use: a JSON document written as ``json.dumps`` writes it."""
    import json

    stored = json.dumps([{"type": "tests", "command": ["curl", f"--user=u:{PW}", "h"], "output": f"+ curl -uu:{PW} h"}])
    assert PW not in redact_text(stored) and PW not in json.dumps(redact_value(json.loads(stored)))


# --- mutations: restoring the defect (each new rule disabled) must bring the leak back -----------------------------------------
NEVER = re.compile(r"(?!)")


@pytest.mark.parametrize("rule, leaked", [("_ATTACHED_USER_LONG", f"curl --user=user:{PW} h"), ("_ATTACHED_USER_SHORT", f"curl -uuser:{PW} h")])
def test_mutation_disabling_an_attached_rule_leaks(monkeypatch, rule, leaked):
    assert PW not in redact_text(leaked)
    monkeypatch.setattr(redaction, rule, NEVER)
    assert PW in redact_text(leaked)


def test_mutation_without_the_bundle_rule_the_separate_value_after_a_bundle_leaks(monkeypatch):
    assert PW not in redact_text(f"curl -sSu user:{PW} h")
    monkeypatch.setattr(redaction, "_is_user_flag", lambda flag, name: name.lower() in redaction._USER_FLAGS)
    assert PW in redact_text(f"curl -sSu user:{PW} h") and PW in str(redact_value(["curl", "-sSu", f"u:{PW}"]))


def test_mutation_without_bearer_as_a_secret_word_the_attached_oauth_token_leaks(monkeypatch):
    assert PW not in redact_text(f"curl --oauth2-bearer={PW} h")
    monkeypatch.setattr(redaction, "_SECRET_LAST_WORDS", redaction._SECRET_LAST_WORDS - {"bearer"})
    assert PW in redact_text(f"curl --oauth2-bearer={PW} h")
