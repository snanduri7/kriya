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
    TestContradictsVerificationMarkerCheck,
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


# --- TestContradictsVerificationMarkerCheck: a test can't pass if it demands
# empty stdout from a script required to always print a verdict line ---

_WORDCOUNT_PY = (
    "import sys\n"
    "import os\n"
    "from collections import Counter\n\n"
    "def count_words(filename):\n"
    "    if not os.path.isfile(filename):\n"
    "        print(f\"Error: File '{filename}' not found.\")\n"
    "        sys.exit(1)\n"
    "    with open(filename, 'r', encoding='utf-8') as f:\n"
    "        content = f.read()\n"
    "    for word, count in Counter(content.split()).most_common(10):\n"
    "        print(f\"{word}: {count}\")\n\n"
    "if __name__ == '__main__':\n"
    "    count_words(sys.argv[1])\n"
    "    print(\"[VERIFICATION] PASS\")\n"
)

# Golden regression fixture: the exact real captured test_empty_file body
# from the live incident (2026-08-14) - a demo wordcount.py goal, run
# directly (not via the eval harness), where the generated app worked fine
# and this test was the entire reason Quality Gates never passed.
_WORDCOUNT_TEST_PY = (
    "import os\n"
    "import tempfile\n"
    "import subprocess\n"
    "import sys\n\n"
    "def test_empty_file():\n"
    "    \"\"\"Test wordcount with an empty file\"\"\"\n"
    "    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:\n"
    "        f.write(\"\")\n"
    "        temp_file = f.name\n"
    "    try:\n"
    "        result = subprocess.run([sys.executable, 'wordcount.py', temp_file], \n"
    "                              capture_output=True, text=True, cwd=os.getcwd())\n"
    "        assert result.returncode == 0\n"
    "        assert result.stdout.strip() == \"\"\n"
    "    finally:\n"
    "        os.unlink(temp_file)\n"
)


def test_test_contradicts_verification_marker_check_detects_real_incident():
    """Golden regression fixture: the exact real wordcount.py/test_wordcount.py
    pair from the live incident this check was built for - see this module's
    own docstring and TestContradictsVerificationMarkerCheck's docstring."""
    files = {"wordcount.py": _WORDCOUNT_PY, "test_wordcount.py": _WORDCOUNT_TEST_PY}
    violation = TestContradictsVerificationMarkerCheck().check(files)
    assert violation is not None
    assert "test_wordcount.py" in violation
    assert "wordcount.py" in violation


def test_test_contradicts_verification_marker_check_clean_when_test_expects_marker():
    fixed_test = _WORDCOUNT_TEST_PY.replace(
        'assert result.stdout.strip() == ""',
        'assert "[VERIFICATION] PASS" in result.stdout',
    )
    files = {"wordcount.py": _WORDCOUNT_PY, "test_wordcount.py": fixed_test}
    assert TestContradictsVerificationMarkerCheck().check(files) is None


def test_test_contradicts_verification_marker_check_clean_when_invoked_script_has_no_marker():
    # The empty-stdout assertion targets a script that never prints the
    # marker at all - a legitimate assertion, not a contradiction.
    plain_script = "import sys\nprint('', end='')\n"
    files = {"helper.py": plain_script, "test_helper.py": _WORDCOUNT_TEST_PY.replace("wordcount.py", "helper.py")}
    assert TestContradictsVerificationMarkerCheck().check(files) is None


def test_test_contradicts_verification_marker_check_clean_when_no_subprocess_call():
    files = {"test_x.py": "def test_thing():\n    assert 1 == 1\n"}
    assert TestContradictsVerificationMarkerCheck().check(files) is None


def test_test_contradicts_verification_marker_check_clean_when_invoked_script_not_written():
    # subprocess.run references a script name that isn't in this batch at
    # all (e.g. a system tool) - nothing to cross-reference, must not guess.
    files = {"test_x.py": "subprocess.run([sys.executable, 'other_tool.py'])\nassert result.stdout == \"\"\n"}
    assert TestContradictsVerificationMarkerCheck().check(files) is None


def test_test_contradicts_verification_marker_check_ignores_non_python_files():
    files = {
        "Test.java": "String out = run(\"wordcount.py\"); assertEquals(\"\", out);",
        "wordcount.py": _WORDCOUNT_PY,
    }
    assert TestContradictsVerificationMarkerCheck().check(files) is None


def test_run_static_checks_reports_test_contradicts_verification_marker_violation(tmp_path):
    (tmp_path / "wordcount.py").write_text(_WORDCOUNT_PY)
    (tmp_path / "test_wordcount.py").write_text(_WORDCOUNT_TEST_PY)
    violation = run_static_checks(str(tmp_path), ["wordcount.py", "test_wordcount.py"])
    assert violation is not None
    assert violation.startswith("[test_contradicts_verification_marker]")
