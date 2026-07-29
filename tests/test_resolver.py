import pytest
from unittest.mock import patch, MagicMock
from kriya.tools.resolver import resolve_maven_class, enrich_java_compiler_errors


def _mock_httpx_response(docs: list) -> MagicMock:
    """Helper to build a mock httpx response."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"response": {"docs": docs}}
    return mock_resp


def test_resolve_maven_class_found():
    """Should return groupId, artifactId, version when Maven Central returns a match."""
    with patch("httpx.Client") as MockClient:
        mock_client = MockClient.return_value.__enter__.return_value
        mock_client.get.return_value = _mock_httpx_response([{
            "g": "org.apache.qpid",
            "a": "qpid-jms-client",
            "latestVersion": "1.9.0"
        }])
        result = resolve_maven_class("org.apache.qpid.jms.JmsConnectionFactory", "fc")

    assert result is not None
    assert result["groupId"] == "org.apache.qpid"
    assert result["artifactId"] == "qpid-jms-client"
    assert result["version"] == "1.9.0"


def test_resolve_maven_class_not_found():
    """Should return None when Maven Central returns no results."""
    with patch("httpx.Client") as MockClient:
        mock_client = MockClient.return_value.__enter__.return_value
        mock_client.get.return_value = _mock_httpx_response([])
        result = resolve_maven_class("com.totally.unknown.ClassFoo", "fc")

    assert result is None


def test_resolve_maven_class_http_error():
    """Should return None when the HTTP call raises an exception."""
    with patch("httpx.Client") as MockClient:
        mock_client = MockClient.return_value.__enter__.return_value
        mock_client.get.side_effect = Exception("Connection timeout")
        result = resolve_maven_class("org.apache.qpid.jms.JmsConnectionFactory", "fc")

    assert result is None


def test_enrich_class_not_found():
    """Should detect ClassNotFoundException and append a KRIYA SUGGESTION."""
    compiler_output = (
        "Error occurred during JMS/Ignite operations: Connection refused\n"
        "ClassNotFoundException: org.apache.qpid.jms.JmsConnectionFactory"
    )

    with patch("kriya.tools.resolver.resolve_maven_class") as mock_resolve:
        mock_resolve.return_value = {
            "groupId": "org.apache.qpid",
            "artifactId": "qpid-jms-client",
            "version": "1.9.0"
        }
        enriched = enrich_java_compiler_errors(compiler_output)

    assert "KRIYA PLATFORM DEPENDENCY SUGGESTIONS" in enriched
    assert "KRIYA SUGGESTION" in enriched
    assert "qpid-jms-client" in enriched
    assert "org.apache.qpid" in enriched


def test_enrich_cannot_find_class_in_bean():
    """Should detect Spring 'Cannot find class' error and append a KRIYA SUGGESTION."""
    compiler_output = (
        "CannotLoadBeanClassException: Cannot find class "
        "[org.apache.qpid.jms.JmsConnectionFactory] for bean 'qpidConnectionFactory'"
    )

    with patch("kriya.tools.resolver.resolve_maven_class") as mock_resolve:
        mock_resolve.return_value = {
            "groupId": "org.apache.qpid",
            "artifactId": "qpid-jms-client",
            "version": "1.9.0"
        }
        enriched = enrich_java_compiler_errors(compiler_output)

    assert "KRIYA SUGGESTION" in enriched
    assert "qpid-jms-client" in enriched


def test_enrich_no_missing_classes():
    """Should return original output unchanged when no known error patterns match."""
    compiler_output = "BUILD SUCCESS\n[INFO] No compilation errors found."
    with patch("kriya.tools.resolver.resolve_maven_class") as mock_resolve:
        enriched = enrich_java_compiler_errors(compiler_output)
        mock_resolve.assert_not_called()

    assert enriched == compiler_output
    assert "KRIYA SUGGESTION" not in enriched


def test_enrich_deduplicates_suggestions():
    """Should deduplicate suggestions for the same groupId:artifactId even if it appears multiple times."""
    compiler_output = (
        "ClassNotFoundException: org.apache.qpid.jms.JmsConnectionFactory\n"
        "ClassNotFoundException: org.apache.qpid.jms.JmsSession"
    )

    with patch("kriya.tools.resolver.resolve_maven_class") as mock_resolve:
        mock_resolve.return_value = {
            "groupId": "org.apache.qpid",
            "artifactId": "qpid-jms-client",
            "version": "1.9.0"
        }
        enriched = enrich_java_compiler_errors(compiler_output)

    # Even though two classes were missing, both map to the same artifact,
    # so the suggestion should appear exactly once
    assert enriched.count("qpid-jms-client") == 1


def test_enrich_empty_output():
    """Should gracefully handle empty output."""
    result = enrich_java_compiler_errors("")
    assert result == ""


def test_enrich_none_output():
    """Should gracefully handle None output."""
    result = enrich_java_compiler_errors(None)
    assert result is None
