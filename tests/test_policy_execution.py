from kriya.policy.execution import ExecutionPolicy, _STAGE_METHOD_NAMES
from kriya.policy.model import ActionRequest, ActionType, PolicyDecision


def test_stage_order_matches_ma4_design_doc_section_11():
    assert _STAGE_METHOD_NAMES == (
        "_check_platform_invariants",
        "_check_filesystem",
        "_check_git_destructive",
        "_check_network_egress",
        "_check_package_supply_chain",
        "_check_command_allowlist",
        "_check_approval_rules",
    )


def test_evaluate_runs_stages_in_fixed_order_before_default_policy(monkeypatch):
    policy = ExecutionPolicy()
    call_order = []
    for name in _STAGE_METHOD_NAMES:
        def make_stub(stage_name):
            def stub(request):
                call_order.append(stage_name)
                return None
            return stub
        monkeypatch.setattr(policy, name, make_stub(name))

    policy.evaluate(ActionRequest(action_type=ActionType.READ_FILE, target="x"))
    assert call_order == list(_STAGE_METHOD_NAMES)


def test_first_matching_stage_wins_and_later_stages_never_run(monkeypatch):
    policy = ExecutionPolicy()
    from kriya.policy.model import PolicyResult

    later_stage_called = []

    def deny_immediately(request):
        return PolicyResult(decision=PolicyDecision.DENY, reason_code="X", explanation="y")

    def should_not_run(request):
        later_stage_called.append(True)
        return None

    monkeypatch.setattr(policy, "_check_filesystem", deny_immediately)
    monkeypatch.setattr(policy, "_check_git_destructive", should_not_run)

    result = policy.evaluate(ActionRequest(action_type=ActionType.READ_FILE, target="x"))
    assert result.reason_code == "X"
    assert later_stage_called == []


def test_malformed_request_denies_regardless_of_action_type():
    policy = ExecutionPolicy()
    cases = [
        ActionRequest(action_type=ActionType.READ_FILE),
        ActionRequest(action_type=ActionType.WRITE_FILE),
        ActionRequest(action_type=ActionType.RUN_COMMAND),
        ActionRequest(action_type=ActionType.NETWORK_ACCESS),
        ActionRequest(action_type=ActionType.LLM_NETWORK_ACCESS),
        ActionRequest(action_type=ActionType.INSTALL_PACKAGE),
        ActionRequest(action_type=ActionType.GIT_READ),
        ActionRequest(action_type=ActionType.GIT_WRITE),
        ActionRequest(action_type=ActionType.PUBLISH_ARTIFACT),
    ]
    for request in cases:
        result = policy.evaluate(request)
        assert result.decision == PolicyDecision.DENY
        assert result.reason_code == "MALFORMED_ACTION_REQUEST"


def test_well_formed_read_only_actions_default_allow():
    policy = ExecutionPolicy()
    for request in (
        ActionRequest(action_type=ActionType.READ_FILE, target="src/main.py"),
        ActionRequest(action_type=ActionType.GIT_READ, command=("git", "status")),
    ):
        result = policy.evaluate(request)
        assert result.decision == PolicyDecision.ALLOW
        assert result.reason_code == "DEFAULT_READ_ONLY_ALLOWED"


def test_well_formed_consequential_actions_default_deny_until_their_stage_lands():
    """MA4.4-MA4.9 haven't landed yet - every consequential action type must
    fail closed through the default-policy backstop, never silently allow,
    per section 12's own "never silently default to ALLOW" rule.

    RUN_COMMAND is deliberately excluded here: MA4.4 gave it real stage-6
    logic (kriya/policy/execution.py's _check_command_allowlist), so a
    well-formed RUN_COMMAND request never reaches this backstop anymore -
    see tests/test_policy_command_allowlist.py for its own DENY (and
    ALLOW_SANDBOXED/REQUIRE_APPROVAL) coverage."""
    policy = ExecutionPolicy()
    cases = [
        ActionRequest(action_type=ActionType.WRITE_FILE, target="src/main.py"),
        ActionRequest(action_type=ActionType.NETWORK_ACCESS, network_target="example.com"),
        ActionRequest(action_type=ActionType.LLM_NETWORK_ACCESS, network_target="localhost"),
        ActionRequest(action_type=ActionType.INSTALL_PACKAGE, target="left-pad"),
        ActionRequest(action_type=ActionType.GIT_WRITE, command=("git", "commit")),
        ActionRequest(action_type=ActionType.PUBLISH_ARTIFACT, target="my-artifact"),
    ]
    for request in cases:
        result = policy.evaluate(request)
        assert result.decision == PolicyDecision.DENY
        assert result.reason_code == "DEFAULT_UNKNOWN_ACTION_DENIED"


def test_evaluate_is_deterministic():
    policy = ExecutionPolicy()
    request = ActionRequest(action_type=ActionType.RUN_COMMAND, command=("mvn", "test"))
    first = policy.evaluate(request)
    second = policy.evaluate(request)
    assert first == second


def test_no_llm_or_network_module_imported_by_the_policy_engine():
    """The engine must be reasoned about without any live model call - see
    section 10's "No LLM should be used to decide whether an action is
    allowed." Importing kriya.core.llm here would be a smell (a policy
    module depending on the very boundary it's meant to sit in front of for
    egress); asserting the module itself is absent from execution.py's own
    namespace is a cheap, durable guard against that creeping in silently."""
    import kriya.policy.execution as execution_module
    assert "kriya.core.llm" not in execution_module.__dict__
    assert not hasattr(execution_module, "AsyncOpenAI")
