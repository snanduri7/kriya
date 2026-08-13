"""Tests for kriya/workflow/static_checks.py - the deterministic, no-LLM
pre-flight anti-pattern scanner. Fixtures reproduce the two real bugs found
live 2026-08-12 (ignite_qpid_protocol), plus the language-generic
bare-verification-marker bug found live 2026-08-13 (python_greeter), that
motivated this module and its checks.
"""
import os

from kriya.workflow.static_checks import (
    BareVerificationMarkerCheck,
    IgniteMethodMixingCheck,
    IgniteUnclosedResourceCheck,
    run_static_checks,
)

_METHOD_MIXING_JAVA = """
public class ProtocolApp {
    public static void main(String[] args) throws Exception {
        Ignite ignite = Ignition.start("ignite-config.xml");
    }
}
"""

_SPRING_BEAN_XML = """
<beans>
    <bean id="igniteNode" class="org.apache.ignite.IgniteSpringBean">
        <property name="configuration" ref="ignite.cfg"/>
    </bean>
</beans>
"""

_UNCLOSED_JAVA = """
public class ProtocolApp {
    public static void main(String[] args) throws Exception {
        Ignite ignite = Ignition.start();
        ignite.getOrCreateCache("x");
    }
}
"""

_TRY_WITH_RESOURCES_JAVA = """
public class ProtocolApp {
    public static void main(String[] args) throws Exception {
        try (Ignite ignite = Ignition.start()) {
            ignite.getOrCreateCache("x");
        }
    }
}
"""

_EXPLICIT_CLOSE_JAVA = """
public class ProtocolApp {
    public static void main(String[] args) throws Exception {
        Ignite ignite = Ignition.start();
        try {
            ignite.getOrCreateCache("x");
        } finally {
            ignite.close();
        }
    }
}
"""

_METHOD_B_XML_AND_JAVA = (
    """
public class ProtocolApp {
    public static void main(String[] args) throws Exception {
        try (ConfigurableApplicationContext context = new ClassPathXmlApplicationContext("ignite-config.xml")) {
            Ignite ignite = (Ignite) context.getBean("igniteNode");
        }
    }
}
""",
    _SPRING_BEAN_XML,
)


def test_ignite_method_mixing_check_detects_direct_start_plus_spring_bean():
    files = {"src/ProtocolApp.java": _METHOD_MIXING_JAVA, "ignite-config.xml": _SPRING_BEAN_XML}
    violation = IgniteMethodMixingCheck().check(files)
    assert violation is not None
    assert "ProtocolApp.java" in violation
    assert "ignite-config.xml" in violation


def test_ignite_method_mixing_check_clean_when_only_method_b_used():
    java, xml = _METHOD_B_XML_AND_JAVA
    files = {"src/ProtocolApp.java": java, "ignite-config.xml": xml}
    assert IgniteMethodMixingCheck().check(files) is None


def test_ignite_method_mixing_check_clean_when_no_xml_at_all():
    files = {"src/ProtocolApp.java": _METHOD_MIXING_JAVA}
    assert IgniteMethodMixingCheck().check(files) is None


def test_ignite_unclosed_resource_check_detects_missing_close():
    files = {"src/ProtocolApp.java": _UNCLOSED_JAVA}
    violation = IgniteUnclosedResourceCheck().check(files)
    assert violation is not None
    assert "ProtocolApp.java" in violation


def test_ignite_unclosed_resource_check_clean_with_try_with_resources():
    files = {"src/ProtocolApp.java": _TRY_WITH_RESOURCES_JAVA}
    assert IgniteUnclosedResourceCheck().check(files) is None


def test_ignite_unclosed_resource_check_clean_with_explicit_close():
    files = {"src/ProtocolApp.java": _EXPLICIT_CLOSE_JAVA}
    assert IgniteUnclosedResourceCheck().check(files) is None


def test_ignite_unclosed_resource_check_clean_when_ignition_start_never_called():
    files = {"src/ProtocolApp.java": "public class App { void run() {} }"}
    assert IgniteUnclosedResourceCheck().check(files) is None


def test_run_static_checks_reads_files_from_worktree_and_reports_first_violation(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "ProtocolApp.java").write_text(_METHOD_MIXING_JAVA)
    (tmp_path / "ignite-config.xml").write_text(_SPRING_BEAN_XML)

    violation = run_static_checks(str(tmp_path), ["src/ProtocolApp.java", "ignite-config.xml"])
    assert violation is not None
    assert violation.startswith("[ignite_method_mixing]")


def test_run_static_checks_returns_none_when_nothing_violates(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "ProtocolApp.java").write_text(_TRY_WITH_RESOURCES_JAVA)

    assert run_static_checks(str(tmp_path), ["src/ProtocolApp.java"]) is None


def test_run_static_checks_skips_unreadable_files_gracefully(tmp_path):
    # A path in all_files_written that no longer exists on disk must not crash.
    violation = run_static_checks(str(tmp_path), ["src/DoesNotExist.java"])
    assert violation is None


_BARE_MARKER_PYTHON = (
    "def greet(name: str) -> str:\n"
    "    return f\"Hello, {name}!\"\n\n"
    "[VERIFICATION] PASS\n"
    "print(greet('World'))\n"
)

_CORRECTLY_PRINTED_PYTHON = (
    "def greet(name: str) -> str:\n"
    "    return f\"Hello, {name}!\"\n\n"
    "print(greet('World'))\n"
    "print(\"[VERIFICATION] PASS\")\n"
)

_CORRECTLY_PRINTED_JAVA = (
    "public class App {\n"
    "    public static void main(String[] args) {\n"
    "        System.out.println(\"[VERIFICATION] PASS\");\n"
    "    }\n"
    "}\n"
)


def test_bare_verification_marker_check_detects_unquoted_standalone_marker():
    """Regression test for the real bug found live (2026-08-13,
    python_greeter, reproduced identically across two separate eval-harness
    runs): qwen3-coder:30b wrote Kriya's own verification-contract marker as
    bare, unquoted source text instead of a print() argument."""
    files = {"greet.py": _BARE_MARKER_PYTHON}
    violation = BareVerificationMarkerCheck().check(files)
    assert violation is not None
    assert "greet.py" in violation
    assert "line 4" in violation


def test_bare_verification_marker_check_detects_fail_variant():
    files = {"greet.py": "def f():\n    pass\n\n[VERIFICATION] FAIL: bad\n"}
    violation = BareVerificationMarkerCheck().check(files)
    assert violation is not None


def test_bare_verification_marker_check_clean_when_correctly_printed_python():
    assert BareVerificationMarkerCheck().check({"greet.py": _CORRECTLY_PRINTED_PYTHON}) is None


def test_bare_verification_marker_check_clean_when_correctly_printed_java():
    # Language-generic - the check doesn't special-case any one extension.
    assert BareVerificationMarkerCheck().check({"App.java": _CORRECTLY_PRINTED_JAVA}) is None


def test_bare_verification_marker_check_clean_when_marker_never_mentioned():
    assert BareVerificationMarkerCheck().check({"App.java": "public class App {}"}) is None


def test_run_static_checks_reports_bare_verification_marker_violation(tmp_path):
    (tmp_path / "greet.py").write_text(_BARE_MARKER_PYTHON)
    violation = run_static_checks(str(tmp_path), ["greet.py"])
    assert violation is not None
    assert violation.startswith("[bare_verification_marker]")
