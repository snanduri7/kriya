"""MA4.16: AuthorizedFileWriter (kriya/policy/filesystem.py) - the first
REAL-enforcement code path in MA4. Covers containment (incl. symlink
escapes, on real filesystem state), the narrower enforcement-only
sensitive-path patterns (incl. the false-positive guard that's the whole
reason they're narrower than ExecutionPolicy's default set), and that a
denial genuinely blocks the write (nothing lands on disk)."""

import os
import tempfile

import pytest

from kriya.policy.errors import PolicyDeniedError
from kriya.policy.filesystem import (
    AuthorizedFileWriter,
    FilesystemScope,
    WriteScopeMode,
    is_within_scope,
    make_workspace_scope,
)
from kriya.policy.model import PolicyDecision
from kriya.workflow.edit_safety import StagedFileWrite, content_revision


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory() as d:
        yield d


# --- FilesystemScope / is_within_scope ---

def test_make_workspace_scope_canonicalizes_and_dedupes(workspace):
    scope = make_workspace_scope(workspace, extra_writable_roots=[workspace, workspace + "/"])
    assert len(scope.writable_roots) == 1


def test_target_inside_workspace_is_within_scope(workspace):
    scope = make_workspace_scope(workspace)
    assert is_within_scope(scope, os.path.join(workspace, "src", "main.py")) is True


def test_target_outside_workspace_is_not_within_scope(workspace):
    scope = make_workspace_scope(workspace)
    assert is_within_scope(scope, "/etc/passwd") is False


def test_sibling_directory_with_shared_prefix_is_not_within_scope():
    with tempfile.TemporaryDirectory() as parent:
        repo = os.path.join(parent, "repo")
        repo_evil = os.path.join(parent, "repo-evil")
        os.mkdir(repo)
        os.mkdir(repo_evil)
        scope = make_workspace_scope(repo)
        assert is_within_scope(scope, os.path.join(repo_evil, "x.py")) is False


def test_symlink_escape_is_not_within_scope():
    with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as outside:
        link_path = os.path.join(workspace, "escape")
        os.symlink(outside, link_path)
        scope = make_workspace_scope(workspace)
        assert is_within_scope(scope, os.path.join(link_path, "payload.py")) is False


def test_multiple_writable_roots_all_count():
    with tempfile.TemporaryDirectory() as root_a, tempfile.TemporaryDirectory() as root_b:
        scope = make_workspace_scope(root_a, extra_writable_roots=[root_b])
        assert is_within_scope(scope, os.path.join(root_a, "x.py")) is True
        assert is_within_scope(scope, os.path.join(root_b, "y.py")) is True


# --- AuthorizedFileWriter.authorize ---

def test_authorize_allows_a_path_inside_the_workspace(workspace):
    writer = AuthorizedFileWriter(workspace)
    result = writer.authorize(os.path.join(workspace, "app.py"))
    assert result.decision == PolicyDecision.ALLOW


def test_authorize_denies_a_path_outside_the_workspace(workspace):
    writer = AuthorizedFileWriter(workspace)
    result = writer.authorize("/etc/passwd")
    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "PATH_OUTSIDE_AUTHORIZED_WRITABLE_ROOTS"


def test_authorize_denies_a_symlink_escape(workspace):
    with tempfile.TemporaryDirectory() as outside:
        link_path = os.path.join(workspace, "escape")
        os.symlink(outside, link_path)
        writer = AuthorizedFileWriter(workspace)
        result = writer.authorize(os.path.join(link_path, "payload.py"))
        assert result.decision == PolicyDecision.DENY


def test_authorize_denies_a_real_credential_store_file(workspace):
    writer = AuthorizedFileWriter(workspace)
    for target in (
        os.path.join(workspace, "secrets.json"),
        os.path.join(workspace, "credentials.yaml"),
        os.path.join(workspace, "password.txt"),
        os.path.join(workspace, "id_rsa"),
        os.path.join(workspace, "server.pem"),
        os.path.join(workspace, ".env"),
    ):
        result = writer.authorize(target)
        assert result.decision == PolicyDecision.DENY, target
        assert result.reason_code == "SENSITIVE_PATH_DENIED"


def test_authorize_never_flags_a_legitimate_generated_file_with_a_sensitive_looking_name(workspace):
    """The exact false-positive risk narrower patterns exist to avoid -
    confirmed with the user before implementation."""
    writer = AuthorizedFileWriter(workspace)
    for target in (
        os.path.join(workspace, "password_validator.py"),
        os.path.join(workspace, "credentials_service.py"),
        os.path.join(workspace, "secrets_manager.py"),
        os.path.join(workspace, "src", "auth", "password_reset_handler.py"),
    ):
        result = writer.authorize(target)
        assert result.decision != PolicyDecision.DENY, target


# --- protected_relpaths: the goal-source-file guard (2026-08-25 live incident) ---

def test_authorize_denies_the_protected_goal_source_file(workspace):
    """Regression test for a real live bug, 2026-08-25 (ignite_qpid_protocol):
    a generated subtask targeted the literal goal file supplied via `kriya
    generate --file <path>`, and its content (Kriya's own JSON planning-
    artifact shape) silently overwrote the real goal text - nothing about the
    content itself was invalid, so no existing check could have caught it."""
    writer = AuthorizedFileWriter(workspace, protected_relpaths=["goal.md"])
    result = writer.authorize(os.path.join(workspace, "goal.md"))
    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "GOAL_SOURCE_FILE_PROTECTED"


def test_authorize_denies_a_protected_file_in_a_subdirectory(workspace):
    writer = AuthorizedFileWriter(workspace, protected_relpaths=[os.path.join("docs", "goal.md")])
    result = writer.authorize(os.path.join(workspace, "docs", "goal.md"))
    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "GOAL_SOURCE_FILE_PROTECTED"


def test_authorize_allows_every_other_file_when_a_path_is_protected(workspace):
    """The protection is scoped to exactly the one path - it must never
    become a blanket deny for the rest of a legitimate multi-file change."""
    writer = AuthorizedFileWriter(workspace, protected_relpaths=["goal.md"])
    result = writer.authorize(os.path.join(workspace, "app.py"))
    assert result.decision != PolicyDecision.DENY


def test_authorize_allows_goal_source_file_when_nothing_is_protected(workspace):
    """No protected_relpaths given (the default) - unchanged, ordinary
    behavior for every existing caller."""
    writer = AuthorizedFileWriter(workspace)
    result = writer.authorize(os.path.join(workspace, "goal.md"))
    assert result.decision != PolicyDecision.DENY


def test_authorize_denies_file_outside_validated_subtask_scope(workspace):
    writer = AuthorizedFileWriter(workspace, allowed_relpaths=["declared.py"])
    result = writer.authorize(os.path.join(workspace, "undeclared.py"))
    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "FILE_OUTSIDE_VALIDATED_SUBTASK_SCOPE"


def test_authorize_allows_file_inside_validated_subtask_scope(workspace):
    writer = AuthorizedFileWriter(workspace, allowed_relpaths=["src/declared.py"])
    result = writer.authorize(os.path.join(workspace, "src", "declared.py"))
    assert result.decision != PolicyDecision.DENY


# --- Path canonicalization (PRV-17, 2026-09-03): a live run planned
# "customers_project/" (trailing slash) as an allowed relpath, then the
# Developer's own generated target for the same directory came back as
# "customers_project" (no trailing slash, as any real file report would) -
# a raw string comparison treated the two as different targets and denied a
# write that was, in fact, authorized. normalize_workspace_relpath() (kriya/
# policy/filesystem.py) is the fix; these prove AuthorizedFileWriter's own
# comparison (already normpath-based before this fix) holds for every shape
# named in the incident, without accidentally loosening real containment. ---

def test_authorize_treats_trailing_slash_allowlist_entry_as_same_identity_as_bare_name(workspace):
    writer = AuthorizedFileWriter(
        workspace, allowed_relpaths=["customers_project/"], write_scope_mode=WriteScopeMode.ALLOWLIST,
    )
    result = writer.authorize(os.path.join(workspace, "customers_project"))
    assert result.decision != PolicyDecision.DENY


def test_authorize_trailing_slash_allowlist_entry_does_not_grant_directory_containment(workspace):
    """The trailing-slash normalization above is pure STRING identity, not a
    directory-prefix grant - "customers_project/" in the allowlist still only
    authorizes the literal "customers_project" path, never every file nested
    under it. Confirms the fix didn't widen ALLOWLIST semantics from
    exact-match to directory containment."""
    writer = AuthorizedFileWriter(
        workspace, allowed_relpaths=["customers_project/"], write_scope_mode=WriteScopeMode.ALLOWLIST,
    )
    result = writer.authorize(os.path.join(workspace, "customers_project", "manage.py"))
    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "FILE_OUTSIDE_VALIDATED_SUBTASK_SCOPE"


def test_authorize_dot_slash_prefixed_allowlist_entry_normalizes_safely(workspace):
    writer = AuthorizedFileWriter(
        workspace, allowed_relpaths=["./customers_project/manage.py"], write_scope_mode=WriteScopeMode.ALLOWLIST,
    )
    result = writer.authorize(os.path.join(workspace, "customers_project", "manage.py"))
    assert result.decision != PolicyDecision.DENY


def test_authorize_relative_traversal_target_cannot_collide_with_an_unrelated_allowlisted_file(workspace):
    """Normalization must never let a target spelled with a traversal
    segment ("a/../b.py") resolve to, and thereby collide with, a
    completely different allowlisted path ("a.py") living beside it."""
    writer = AuthorizedFileWriter(
        workspace, allowed_relpaths=["a.py"], write_scope_mode=WriteScopeMode.ALLOWLIST,
    )
    result = writer.authorize(os.path.join(workspace, "a", "..", "b.py"))
    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "FILE_OUTSIDE_VALIDATED_SUBTASK_SCOPE"


def test_commit_batch_raises_and_writes_nothing_when_the_goal_source_file_is_targeted(workspace):
    writer = AuthorizedFileWriter(workspace, protected_relpaths=["goal.md"])
    target = os.path.join(workspace, "goal.md")
    with open(target, "w") as f:
        f.write("# The real goal\n")
    with pytest.raises(PolicyDeniedError) as exc_info:
        writer.commit_file(
            target, '{"subtasks": [...]}', expected_revision=content_revision("# The real goal\n"),
        )
    assert exc_info.value.result.reason_code == "GOAL_SOURCE_FILE_PROTECTED"
    with open(target) as f:
        assert f.read() == "# The real goal\n"


# --- WriteScopeMode (PRV-05, 2026-08-28): allowed_relpaths=() used to
# ambiguously mean "no restriction" - the same falsy value every ordinary
# top-level `kriya generate` call ALSO passes to mean unrestricted writes -
# so a verification-only subtask's intended "write nothing" silently became
# "write anywhere". write_scope_mode makes the three real policies explicit
# and unambiguous; see WriteScopeMode's own docstring for the full incident.

def test_deny_all_rejects_any_write_regardless_of_allowed_relpaths(workspace):
    writer = AuthorizedFileWriter(workspace, write_scope_mode=WriteScopeMode.DENY_ALL)
    result = writer.authorize(os.path.join(workspace, "anything.py"))
    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "WRITE_SCOPE_DENY_ALL"


def test_deny_all_rejects_even_a_path_present_in_allowed_relpaths(workspace):
    """DENY_ALL is unconditional - it must not be defeatable by a caller
    that also (incorrectly) passes a non-empty allowed_relpaths."""
    writer = AuthorizedFileWriter(
        workspace, allowed_relpaths=["a.py"], write_scope_mode=WriteScopeMode.DENY_ALL,
    )
    result = writer.authorize(os.path.join(workspace, "a.py"))
    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "WRITE_SCOPE_DENY_ALL"


def test_allowlist_mode_allows_listed_file(workspace):
    writer = AuthorizedFileWriter(
        workspace, allowed_relpaths=["a.py"], write_scope_mode=WriteScopeMode.ALLOWLIST,
    )
    result = writer.authorize(os.path.join(workspace, "a.py"))
    assert result.decision != PolicyDecision.DENY


def test_allowlist_mode_rejects_unlisted_file(workspace):
    writer = AuthorizedFileWriter(
        workspace, allowed_relpaths=["a.py"], write_scope_mode=WriteScopeMode.ALLOWLIST,
    )
    result = writer.authorize(os.path.join(workspace, "b.py"))
    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "FILE_OUTSIDE_VALIDATED_SUBTASK_SCOPE"


def test_unrestricted_mode_preserves_existing_behavior(workspace):
    writer = AuthorizedFileWriter(workspace, write_scope_mode=WriteScopeMode.UNRESTRICTED)
    result = writer.authorize(os.path.join(workspace, "anything.py"))
    assert result.decision != PolicyDecision.DENY


def test_write_scope_mode_omitted_infers_unrestricted_from_empty_allowed_relpaths(workspace):
    """Backward compatibility: every call site written before write_scope_mode
    existed (every ordinary top-level `kriya generate` call) passes an empty
    allowed_relpaths meaning "no restriction" - must stay unchanged."""
    writer = AuthorizedFileWriter(workspace)
    result = writer.authorize(os.path.join(workspace, "anything.py"))
    assert result.decision != PolicyDecision.DENY


def test_write_scope_mode_omitted_infers_allowlist_from_nonempty_allowed_relpaths(workspace):
    """Backward compatibility: every existing bounded-subtask call site that
    already passes a real allowed_relpaths list keeps its exact prior
    ALLOWLIST behavior."""
    writer = AuthorizedFileWriter(workspace, allowed_relpaths=["a.py"])
    allowed = writer.authorize(os.path.join(workspace, "a.py"))
    denied = writer.authorize(os.path.join(workspace, "b.py"))
    assert allowed.decision != PolicyDecision.DENY
    assert denied.decision == PolicyDecision.DENY
    assert denied.reason_code == "FILE_OUTSIDE_VALIDATED_SUBTASK_SCOPE"


def test_deny_all_commit_file_raises_and_writes_nothing(workspace):
    writer = AuthorizedFileWriter(workspace, write_scope_mode=WriteScopeMode.DENY_ALL)
    target = os.path.join(workspace, "app.py")
    with pytest.raises(PolicyDeniedError) as exc_info:
        writer.commit_file(target, "print(1)", expected_revision=content_revision(""))
    assert exc_info.value.result.reason_code == "WRITE_SCOPE_DENY_ALL"
    assert not os.path.exists(target)


# --- AuthorizedFileWriter.commit_file / commit_batch: real enforcement ---

def test_commit_file_writes_when_authorized(workspace):
    writer = AuthorizedFileWriter(workspace)
    target = os.path.join(workspace, "app.py")
    writer.commit_file(target, "print(1)", expected_revision=content_revision(""))
    with open(target) as f:
        assert f.read() == "print(1)"


def test_commit_file_raises_and_writes_nothing_when_denied(workspace):
    writer = AuthorizedFileWriter(workspace)
    target = "/tmp/definitely-outside-the-authorized-root-kriya-test.py"
    if os.path.exists(target):
        os.remove(target)
    with pytest.raises(PolicyDeniedError):
        writer.commit_file(target, "malicious", expected_revision=content_revision(""))
    assert not os.path.exists(target)


def test_commit_batch_writes_all_when_every_target_authorized(workspace):
    writer = AuthorizedFileWriter(workspace)
    writes = [
        StagedFileWrite(
            target_path=os.path.join(workspace, "a.py"), content="a",
            base_path=os.path.join(workspace, "a.py"), expected_base_revision=content_revision(""),
        ),
        StagedFileWrite(
            target_path=os.path.join(workspace, "b.py"), content="b",
            base_path=os.path.join(workspace, "b.py"), expected_base_revision=content_revision(""),
        ),
    ]
    writer.commit_batch(writes)
    with open(os.path.join(workspace, "a.py")) as f:
        assert f.read() == "a"
    with open(os.path.join(workspace, "b.py")) as f:
        assert f.read() == "b"


def test_commit_batch_raises_and_writes_nothing_when_one_target_is_denied(workspace):
    """One denied target aborts the WHOLE batch before any write happens -
    checked up front, not interleaved with commit_revision_grounded_batch's
    own per-item write loop."""
    writer = AuthorizedFileWriter(workspace)
    good_target = os.path.join(workspace, "a.py")
    writes = [
        StagedFileWrite(
            target_path=good_target, content="a",
            base_path=good_target, expected_base_revision=content_revision(""),
        ),
        StagedFileWrite(
            target_path="/etc/passwd", content="malicious",
            base_path="/etc/passwd", expected_base_revision=content_revision(""),
        ),
    ]
    with pytest.raises(PolicyDeniedError):
        writer.commit_batch(writes)
    assert not os.path.exists(good_target)
