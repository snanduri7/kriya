import pytest

from kriya.workflow.evidence import EvidenceRecord
from kriya.workflow.outbound_lookup import (
    OutboundLookupRequest, UnsafeLookupTerm, sanitize_public_technology_term,
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
