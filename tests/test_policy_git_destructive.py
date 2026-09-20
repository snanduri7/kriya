from kriya.policy.execution import ExecutionPolicy
from kriya.policy.model import ActionRequest, ActionType, PolicyDecision
from kriya.workflow.triage import ChangeKind, EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass


def _route(weight):
    return EngineeringRoute(
        kind=ChangeKind.TASK, impact=ImpactVector(),
        initial_risk_class=RiskClass.LOW, current_risk_class=RiskClass.LOW,
        max_observed_risk_class=RiskClass.LOW, execution_weight=weight,
    )


def test_git_read_is_unaffected_by_this_stage():
    """GIT_READ default-allows at MA4.2's own backstop - this stage governs
    GIT_WRITE only."""
    policy = ExecutionPolicy()
    result = policy.evaluate(ActionRequest(action_type=ActionType.GIT_READ, command=("git", "status")))
    assert result.decision == PolicyDecision.ALLOW
    assert result.reason_code == "DEFAULT_READ_ONLY_ALLOWED"


def test_force_push_variants_all_deny():
    policy = ExecutionPolicy()
    variants = [
        ("git", "push", "--force", "origin", "main"),
        ("git", "push", "-f", "origin", "main"),
        ("git", "push", "--force-with-lease", "origin", "main"),
        ("git", "push", "--force-with-lease=refs/heads/main:abc123", "origin"),
    ]
    for command in variants:
        result = policy.evaluate(ActionRequest(action_type=ActionType.GIT_WRITE, command=command))
        assert result.decision == PolicyDecision.DENY, command
        assert result.reason_code == "GIT_FORCE_PUSH_DENIED"


def test_force_push_denies_even_under_light_weight():
    policy = ExecutionPolicy()
    result = policy.evaluate(ActionRequest(
        action_type=ActionType.GIT_WRITE, command=("git", "push", "--force", "origin", "main"),
        engineering_route=_route(ExecutionWeight.LIGHT),
    ))
    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "GIT_FORCE_PUSH_DENIED"


def test_protected_ref_deletion_via_push_denies():
    policy = ExecutionPolicy()
    for ref in ("main", "master"):
        result = policy.evaluate(ActionRequest(
            action_type=ActionType.GIT_WRITE, command=("git", "push", "origin", "--delete", ref),
        ))
        assert result.decision == PolicyDecision.DENY, ref
        assert result.reason_code == "PROTECTED_REF_MUTATION_DENIED"


def test_non_protected_ref_deletion_via_push_follows_the_ordinary_push_rule():
    policy = ExecutionPolicy()
    result = policy.evaluate(ActionRequest(
        action_type=ActionType.GIT_WRITE, command=("git", "push", "origin", "--delete", "feature-x"),
        engineering_route=_route(ExecutionWeight.LIGHT),
    ))
    assert result.decision == PolicyDecision.ALLOW
    assert result.reason_code == "GIT_PUSH_ALLOWED_LIGHT"


def test_protected_branch_deletion_denies_regardless_of_flag_spelling():
    policy = ExecutionPolicy()
    for flag in ("-D", "-d", "--delete"):
        result = policy.evaluate(ActionRequest(
            action_type=ActionType.GIT_WRITE, command=("git", "branch", flag, "main"),
        ))
        assert result.decision == PolicyDecision.DENY, flag
        assert result.reason_code == "PROTECTED_REF_MUTATION_DENIED"


def test_non_protected_branch_deletion_requires_approval():
    policy = ExecutionPolicy()
    result = policy.evaluate(ActionRequest(
        action_type=ActionType.GIT_WRITE, command=("git", "branch", "-D", "feature-x"),
    ))
    assert result.decision == PolicyDecision.REQUIRE_APPROVAL
    assert result.reason_code == "GIT_WRITE_REQUIRES_APPROVAL"


def test_push_weight_table():
    policy = ExecutionPolicy()
    command = ("git", "push", "origin", "main")

    no_route = policy.evaluate(ActionRequest(action_type=ActionType.GIT_WRITE, command=command))
    assert no_route.decision == PolicyDecision.REQUIRE_APPROVAL
    assert no_route.reason_code == "GIT_PUSH_REQUIRES_APPROVAL"

    light = policy.evaluate(ActionRequest(
        action_type=ActionType.GIT_WRITE, command=command, engineering_route=_route(ExecutionWeight.LIGHT),
    ))
    assert light.decision == PolicyDecision.ALLOW
    assert light.reason_code == "GIT_PUSH_ALLOWED_LIGHT"

    for weight in (ExecutionWeight.STANDARD, ExecutionWeight.HEAVY):
        result = policy.evaluate(ActionRequest(
            action_type=ActionType.GIT_WRITE, command=command, engineering_route=_route(weight),
        ))
        assert result.decision == PolicyDecision.REQUIRE_APPROVAL, weight
        assert result.reason_code == "GIT_PUSH_REQUIRES_APPROVAL"


def test_git_config_mutation_always_denies():
    policy = ExecutionPolicy()
    for command in (
        ("git", "config", "user.name", "evil"),
        ("git", "config", "credential.helper", "store"),
        ("git", "config", "--global", "user.email", "evil@example.com"),
    ):
        result = policy.evaluate(ActionRequest(action_type=ActionType.GIT_WRITE, command=command))
        assert result.decision == PolicyDecision.DENY, command
        assert result.reason_code == "GIT_CONFIG_MUTATION_DENIED"
        assert result.requires_approval is False  # hard deny, no approval path


def test_git_remote_mutation_denies_for_mutating_verbs():
    policy = ExecutionPolicy()
    for verb in ("set-url", "remove", "rm", "add", "rename", "set-head"):
        result = policy.evaluate(ActionRequest(
            action_type=ActionType.GIT_WRITE, command=("git", "remote", verb, "origin", "http://evil.com"),
        ))
        assert result.decision == PolicyDecision.DENY, verb
        assert result.reason_code == "GIT_REMOTE_MUTATION_DENIED"


def test_git_remote_read_only_verb_is_not_denied_by_the_remote_mutation_rule():
    """'git remote -v'/'git remote show' aren't mutating verbs - falls
    through to the ordinary-write approval backstop, not the hard deny."""
    policy = ExecutionPolicy()
    result = policy.evaluate(ActionRequest(action_type=ActionType.GIT_WRITE, command=("git", "remote", "-v")))
    assert result.decision == PolicyDecision.REQUIRE_APPROVAL
    assert result.reason_code == "GIT_WRITE_REQUIRES_APPROVAL"


def test_ordinary_git_writes_require_approval_never_a_bare_allow():
    policy = ExecutionPolicy()
    for command in (
        ("git", "commit", "--allow-empty", "-m", "x"),
        ("git", "tag", "v1.0"),
        ("git", "merge", "feature-x"),
        ("git", "checkout", "-b", "new-branch"),
    ):
        result = policy.evaluate(ActionRequest(action_type=ActionType.GIT_WRITE, command=command))
        assert result.decision == PolicyDecision.REQUIRE_APPROVAL, command
        assert result.reason_code == "GIT_WRITE_REQUIRES_APPROVAL"


def test_command_without_a_leading_git_token_is_still_classified_correctly():
    """Real callers pass the full argv including 'git' as command[0], but
    the classification itself shouldn't depend on that literal token being
    present."""
    policy = ExecutionPolicy()
    result = policy.evaluate(ActionRequest(action_type=ActionType.GIT_WRITE, command=("push", "--force", "origin")))
    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "GIT_FORCE_PUSH_DENIED"


# --- POL-001-P3: the Kriya-internal bootstrap-commit recognizer ---
# The exact two commands kriya/workflow/worktree.py constructs. SEC-001-P1
# (2026-09-11) prepends "-c core.hooksPath=/dev/null" to both real
# commands (and to the real "git init" below) - these fixtures must match
# the real shape or these tests stop proving anything about the real path.

_ZERO_COMMIT_BOOTSTRAP = (
    "git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=Kriya", "-c", "user.email=kriya@local",
    "commit", "--allow-empty", "-m", "Kriya: initial commit (empty) to enable worktree isolation",
)
_GREENFIELD_BOOTSTRAP = (
    "git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=Kriya", "-c", "user.email=kriya@local",
    "commit", "--allow-empty", "-m", "Kriya: initial commit to enable isolation",
)


def _evaluate(command):
    return ExecutionPolicy().evaluate(ActionRequest(action_type=ActionType.GIT_WRITE, command=command))


def test_exact_zero_commit_bootstrap_is_allowed_with_dedicated_reason():
    result = _evaluate(_ZERO_COMMIT_BOOTSTRAP)
    assert result.decision == PolicyDecision.ALLOW
    assert result.reason_code == "KRIYA_INTERNAL_BOOTSTRAP_COMMIT_ALLOWED"


def test_exact_greenfield_bootstrap_is_allowed_with_dedicated_reason():
    result = _evaluate(_GREENFIELD_BOOTSTRAP)
    assert result.decision == PolicyDecision.ALLOW
    assert result.reason_code == "KRIYA_INTERNAL_BOOTSTRAP_COMMIT_ALLOWED"


def test_identity_free_allow_empty_commit_still_requires_approval():
    result = _evaluate(("git", "commit", "--allow-empty", "-m", "x"))
    assert result.decision == PolicyDecision.REQUIRE_APPROVAL
    assert result.reason_code == "GIT_WRITE_REQUIRES_APPROVAL"


def test_kriya_identity_without_allow_empty_is_not_exempt():
    result = _evaluate((
        "git", "-c", "user.name=Kriya", "-c", "user.email=kriya@local", "commit", "-m", "x",
    ))
    assert result.decision == PolicyDecision.REQUIRE_APPROVAL


def test_wrong_name_is_not_exempt():
    result = _evaluate((
        "git", "-c", "user.name=NotKriya", "-c", "user.email=kriya@local",
        "commit", "--allow-empty", "-m", "x",
    ))
    assert result.decision == PolicyDecision.REQUIRE_APPROVAL


def test_wrong_email_is_not_exempt():
    result = _evaluate((
        "git", "-c", "user.name=Kriya", "-c", "user.email=evil@local",
        "commit", "--allow-empty", "-m", "x",
    ))
    assert result.decision == PolicyDecision.REQUIRE_APPROVAL


def test_only_name_field_is_not_exempt():
    result = _evaluate(("git", "-c", "user.name=Kriya", "commit", "--allow-empty", "-m", "x"))
    assert result.decision == PolicyDecision.REQUIRE_APPROVAL


def test_only_email_field_is_not_exempt():
    result = _evaluate(("git", "-c", "user.email=kriya@local", "commit", "--allow-empty", "-m", "x"))
    assert result.decision == PolicyDecision.REQUIRE_APPROVAL


def test_amend_is_not_exempt():
    result = _evaluate((
        "git", "-c", "user.name=Kriya", "-c", "user.email=kriya@local",
        "commit", "--allow-empty", "--amend", "-m", "x",
    ))
    assert result.decision == PolicyDecision.REQUIRE_APPROVAL


def test_dash_a_is_not_exempt():
    result = _evaluate((
        "git", "-c", "user.name=Kriya", "-c", "user.email=kriya@local",
        "commit", "--allow-empty", "-a", "-m", "x",
    ))
    assert result.decision == PolicyDecision.REQUIRE_APPROVAL


def test_dash_dash_all_is_not_exempt():
    result = _evaluate((
        "git", "-c", "user.name=Kriya", "-c", "user.email=kriya@local",
        "commit", "--allow-empty", "--all", "-m", "x",
    ))
    assert result.decision == PolicyDecision.REQUIRE_APPROVAL


def test_pathspec_argument_is_not_exempt():
    result = _evaluate((
        "git", "-c", "user.name=Kriya", "-c", "user.email=kriya@local",
        "commit", "--allow-empty", "-m", "x", "somefile.txt",
    ))
    assert result.decision == PolicyDecision.REQUIRE_APPROVAL


def test_fixup_variant_is_not_exempt():
    result = _evaluate((
        "git", "-c", "user.name=Kriya", "-c", "user.email=kriya@local",
        "commit", "--allow-empty", "--fixup=HEAD", "-m", "x",
    ))
    assert result.decision == PolicyDecision.REQUIRE_APPROVAL


def test_reuse_message_flag_after_commit_is_not_exempt():
    """A second, post-subcommand -c (git commit -c <commit> reuses that
    commit's message/authorship) is a different flag than the pre-subcommand
    global -c this recognizer keys on, and must not be confused with it."""
    result = _evaluate((
        "git", "-c", "user.name=Kriya", "-c", "user.email=kriya@local",
        "commit", "--allow-empty", "-c", "HEAD", "-m", "x",
    ))
    assert result.decision == PolicyDecision.REQUIRE_APPROVAL


def test_other_git_write_subcommands_unaffected_by_the_bootstrap_recognizer():
    """The recognizer only ever fires for the exact commit shape - push/
    config/remote/branch-delete keep their own pre-existing verdicts."""
    assert _evaluate(("git", "push", "--force", "origin")).decision == PolicyDecision.DENY
    assert _evaluate(("git", "config", "user.name", "x")).decision == PolicyDecision.DENY
    assert _evaluate(("git", "remote", "add", "origin", "x")).decision == PolicyDecision.DENY
    assert _evaluate(("git", "branch", "-D", "main")).decision == PolicyDecision.DENY
    assert _evaluate(("git", "push", "origin", "main")).decision == PolicyDecision.REQUIRE_APPROVAL
    assert _evaluate(("git", "tag", "v1.0")).decision == PolicyDecision.REQUIRE_APPROVAL
    assert _evaluate(("git", "merge", "feature-x")).decision == PolicyDecision.REQUIRE_APPROVAL


# --- POL-001-P3: `git init`, _bootstrap_greenfield_repository's OTHER real
# GIT_WRITE request (found via a second look after an initial draft only
# covered the commit half of this same function - _check_git_destructive's
# catch-all applied to bare `git init` exactly as it did to an unrecognized
# commit, and was still reachable with no approval path until this). ---

def test_exact_greenfield_init_is_allowed_with_dedicated_reason():
    # SEC-001-P1 (2026-09-11): the real command now includes worktree.py's
    # hooks-disabled prefix - see the module comment above.
    result = _evaluate(("git", "-c", "core.hooksPath=/dev/null", "init"))
    assert result.decision == PolicyDecision.ALLOW
    assert result.reason_code == "KRIYA_INTERNAL_BOOTSTRAP_INIT_ALLOWED"


def test_bare_init_without_hooks_prefix_is_not_exempt():
    """The recognizer's positive allowlist now includes the hooks-disabled
    prefix as part of the exact required shape - a bare `git init` with no
    prefix at all (never constructed by any real Kriya caller) must not be
    silently treated as equivalent."""
    result = _evaluate(("git", "init"))
    assert result.decision == PolicyDecision.REQUIRE_APPROVAL


def test_init_with_bare_flag_is_not_exempt():
    result = _evaluate(("git", "init", "--bare"))
    assert result.decision == PolicyDecision.REQUIRE_APPROVAL


def test_init_with_template_flag_is_not_exempt():
    result = _evaluate(("git", "init", "--template=/tmp/evil"))
    assert result.decision == PolicyDecision.REQUIRE_APPROVAL


def test_init_with_target_directory_argument_is_not_exempt():
    result = _evaluate(("git", "init", "/some/other/path"))
    assert result.decision == PolicyDecision.REQUIRE_APPROVAL
