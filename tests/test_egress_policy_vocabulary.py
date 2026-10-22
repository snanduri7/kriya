"""``autonomy.egress_policy`` is a closed vocabulary: exactly ``local_only`` or ``unrestricted``.

MEASURED at f6194c4 (reconciliation, EGRESS-POLICY-TYPO-FAILS-OPEN-NEW): ``AutonomyConfig(egress_policy=v)`` accepted ``local-only``,
``LOCAL_ONLY`` and ``nonsense`` unchanged, and every consumer compares against the literal ``"local_only"`` - so a mistyped
restriction was silently read as unrestricted. The field is validated like its siblings (``shell_network``,
``contract_change_escalation``): exact, case-sensitive, a ValueError naming the allowed values."""
import pytest
from pydantic import ValidationError

from kriya.config.config import AppConfig, AutonomyConfig


@pytest.mark.parametrize("value", ["local_only", "unrestricted"])
def test_every_member_of_the_vocabulary_is_accepted(value):
    assert AutonomyConfig(egress_policy=value).egress_policy == value
    assert AppConfig.model_validate({"autonomy": {"egress_policy": value}}).autonomy.egress_policy == value


def test_the_default_is_the_restrictive_value():
    assert AutonomyConfig().egress_policy == "local_only"


@pytest.mark.parametrize("value", [
    "local-only", "LOCAL_ONLY", "Local_Only", " local_only", "local_only ", "localonly", "local", "nonsense", "", "open", "none",
    "UNRESTRICTED", "Unrestricted",
])
def test_anything_outside_the_vocabulary_is_refused_not_read_as_unrestricted(value):
    with pytest.raises(ValidationError, match="autonomy.egress_policy must be 'local_only' or 'unrestricted'"):
        AutonomyConfig(egress_policy=value)
    with pytest.raises(ValidationError, match="autonomy.egress_policy must be 'local_only' or 'unrestricted'"):
        AppConfig.model_validate({"autonomy": {"egress_policy": value}})          # the shape a kriya.yaml section reaches the model in


@pytest.mark.parametrize("value", [None, 0, False, ["local_only"], {"local_only": True}])
def test_a_non_string_is_refused_too(value):
    with pytest.raises(ValidationError):
        AutonomyConfig(egress_policy=value)


# The same closed vocabulary on every way a value can reach the section after construction (the `autonomy.mode` pattern): an
# assignment (`cfg.autonomy.egress_policy = ...`), a model_copy update, and an instance built without validation handed to AppConfig
# (revalidate_instances="always"). A raw `AutonomyConfig.model_construct(...)` used WITHOUT AppConfig is not guarded - not a production path.
@pytest.mark.parametrize("value", ["local-only", "LOCAL_ONLY", "nonsense", "", "open"])
def test_an_assignment_a_model_copy_and_a_model_constructed_section_are_refused_too(value):
    section = AutonomyConfig()
    with pytest.raises(ValueError, match="autonomy.egress_policy must be 'local_only' or 'unrestricted'"):
        section.egress_policy = value
    with pytest.raises(ValueError, match="autonomy.egress_policy must be 'local_only' or 'unrestricted'"):
        section.model_copy(update={"egress_policy": value})
    with pytest.raises(ValidationError):
        AppConfig(autonomy=AutonomyConfig.model_construct(egress_policy=value))
    config = AppConfig()
    with pytest.raises(ValueError, match="autonomy.egress_policy must be"):
        config.autonomy.egress_policy = value
    assert section.egress_policy == "local_only" and config.autonomy.egress_policy == "local_only"       # nothing was half-applied


@pytest.mark.parametrize("value", ["local_only", "unrestricted"])
def test_a_valid_assignment_and_model_copy_still_work(value):
    section = AutonomyConfig()
    section.egress_policy = value
    assert section.egress_policy == value
    assert AutonomyConfig().model_copy(update={"egress_policy": value}).egress_policy == value
