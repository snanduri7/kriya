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
