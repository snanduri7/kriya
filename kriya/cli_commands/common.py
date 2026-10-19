"""Helpers shared by ``kriya/cli.py`` and the command groups in ``kriya/cli_commands/`` (full modularization E14).

Moved verbatim from ``kriya/cli.py``: ``_user_error_text`` (an expected operator error as one message), used by the
moved ``metrics`` and ``authority`` groups and by the commands that stay in ``kriya/cli.py`` (which re-exports it, the
same object). This module imports nothing from ``kriya`` and never imports ``kriya.cli`` or ``kriya.repl`` (they form
an import cycle; a command module that reached either would join it).
"""

def _user_error_text(error: BaseException) -> str:
    """An expected operator error as one message: its typed reason code and
    remediation first when it carries them (e.g. SEC-009's
    TRUST_PATH_INSIDE_WORKSPACE)."""
    code = getattr(error, "reason_code", None)
    remediation = getattr(error, "remediation", None)
    text = f"[{code}] {error}" if isinstance(code, str) and code else str(error)
    return f"{text}\nRemediation: {remediation}" if isinstance(remediation, str) and remediation else text
