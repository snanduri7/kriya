from unittest.mock import MagicMock, patch

from kriya.tools.resolver import enrich_java_compiler_errors, resolve_maven_class


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


def test_resolve_maven_class_blocks_external_lookup_before_client_creation():
    with patch("httpx.Client") as MockClient:
        result = resolve_maven_class(
            "org.example.PrivateClass", "fc", allow_external_lookup=False,
        )

    assert result is None
    MockClient.assert_not_called()


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


def test_enrich_does_not_query_bare_simple_class_names():
    """Regression test for a real bug found via two live golden-use-case runs
    (Ignite, Qpid): "cannot find symbol: class X" (a bare, unqualified class
    name with no package/FQCN context) used to be queried against Maven
    Central's simple-class-name search (c:"X"), which has no
    relevance/consensus filtering and just takes the single top hit.
    Confirmed live as actively harmful, not just unhelpful: a real "cannot
    find symbol: class IgniteCache" (whose actual cause was a wrong import
    path, not a missing dependency) matched an unrelated tiny library
    (cc.mashroom:mashroom-plugin) that got auto-accrued into a permanent
    skill polluting a later, unrelated goal; separately, in the same run,
    5/5 bare-class-name matches (SystemLauncher, ConnectionFactory,
    MessageProducer, MessageConsumer, TextMessage) were unrelated garbage
    (dataspacetck, ldk-sql-api, tracee-examples-jms-api, dapeng-message-api,
    dingtalk) while the package-based search correctly resolved javax.jms in
    the SAME compiler output - the bare-name pattern is categorically less
    reliable than the others and is now never queried at all."""
    compiler_output = (
        "[ERROR] cannot find symbol\n"
        "  symbol:   class ConnectionFactory\n"
        "  location: class com.example.QpidClientApp"
    )

    with patch("kriya.tools.resolver.resolve_maven_class") as mock_resolve:
        enriched = enrich_java_compiler_errors(compiler_output)
        mock_resolve.assert_not_called()

    assert enriched == compiler_output
    assert "KRIYA SUGGESTION" not in enriched


def test_enrich_still_queries_missing_package():
    """Patterns 1/2/4 (FQCN, Spring bean FQCN, missing package) are unchanged
    and still fire - only the bare-simple-class-name pattern was removed."""
    compiler_output = "[ERROR] package javax.jms does not exist"

    with patch("kriya.tools.resolver.resolve_maven_class") as mock_resolve:
        mock_resolve.return_value = {"groupId": "javax.jms", "artifactId": "jms", "version": "1.1"}
        enriched = enrich_java_compiler_errors(compiler_output)

    mock_resolve.assert_called_once_with(
        "javax.jms", "g", allow_external_lookup=True,
    )
    assert "<groupId>javax.jms</groupId>" in enriched
    assert "<artifactId>jms</artifactId>" in enriched


def test_enrich_compiler_errors_does_not_resolve_when_external_lookup_is_blocked():
    compiler_output = "[ERROR] package com.example.proprietary does not exist"

    with patch("kriya.tools.resolver.resolve_maven_class") as mock_resolve:
        enriched = enrich_java_compiler_errors(
            compiler_output, allow_external_lookup=False,
        )

    mock_resolve.assert_called_once_with(
        "com.example.proprietary", "g", allow_external_lookup=False,
    )
    assert enriched == compiler_output
