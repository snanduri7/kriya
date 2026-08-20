import pytest

from kriya.workflow.evidence import EvidenceRecord
from kriya.workflow.outbound_lookup import (
    OutboundLookupRequest, UnsafeLookupTerm, is_known_public_term,
    sanitize_public_technology_term,
)


def test_public_dependency_coordinates_and_versions_are_allowed():
    request = OutboundLookupRequest.from_extracted_terms(
        ["org.apache.ignite:ignite-core:2.18.0", "Apache Ignite 2.18.0"],
        origin="declared_dependency",
    )

    assert request.queries() == (
        "org.apache.ignite:ignite-core:2.18.0 example",
        "Apache Ignite 2.18.0 example",
    )


@pytest.mark.parametrize("unsafe", [
    "src/main/java/com/acme/SecretService.java",
    "CustomerTier.STANDARD: Decimal(\"0.00\"),",
    "java.lang.IllegalStateException\n\tat com.acme.Application.run(Application.java:42)",
    "<dependency><groupId>com.acme</groupId></dependency>",
])
def test_source_paths_code_and_diagnostics_are_rejected(unsafe):
    with pytest.raises(UnsafeLookupTerm):
        sanitize_public_technology_term(unsafe)


def test_canonical_local_evidence_cannot_be_used_as_lookup_terms():
    evidence = EvidenceRecord(
        kind="failure", source="compiler", attempt=1,
        payload={"source": "proprietary code"},
    )

    with pytest.raises(UnsafeLookupTerm):
        OutboundLookupRequest.from_extracted_terms(
            [evidence], origin="invalid",
        )


def test_unknown_term_requires_explicit_public_declaration_for_auto_approval():
    assert not is_known_public_term("internalwidgetlib")
    assert not is_known_public_term("internal apache payroll")
    assert not is_known_public_term("org.apache.private:company-secret:1.0")
    assert is_known_public_term("internalwidgetlib", ["internalwidgetlib"])
    assert is_known_public_term("Apache Ignite 2.18.0")
    assert is_known_public_term("org.apache.ignite:ignite-core:2.18.0")
    assert is_known_public_term("org.codehaus.mojo:exec-maven-plugin")
