"""The PRD-036 canary's REQ-3 oracle (scripts/prd036_canary_oracle/verify.sh): the candidate must equal the base outside the BODY of
``delete(Long driverId)`` (imports excepted), and delete's annotations and signature must be unchanged. Synthetic class - not the private fixture."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ORACLE = Path(__file__).parent.parent / "scripts" / "prd036_canary_oracle" / "verify.sh"
CANDIDATE = "src/main/java/com/myapp/service/driver/DefaultDriverService.java"
BASE = '''package com.example.service;

import java.util.List;
import org.springframework.transaction.annotation.Transactional;

public class DefaultDriverService implements DriverService
{
    private final Repo driverRepository;

    public DefaultDriverService(final Repo driverRepository)
    {
        this.driverRepository = driverRepository;
    }

    @Override
    @Transactional
    public Thing find(Long driverId) throws NotFound
    {
        return checked(driverId);
    }

    /**
     * Delete.
     */
    @Override
    @Transactional
    public void delete(Long driverId) throws NotFound
    {
        Thing thing = checked(driverId);
        thing.setDeleted(true);
    }

    @Override
    public List<Thing> all()
    {
        return driverRepository.findAll();
    }
}
'''


def _edit(old, new):
    return lambda text: text.replace(old, new, 1)


LEGAL = {
    "the base itself": lambda text: text,
    "the intended fix": _edit("thing.setDeleted(true);", "thing.setDeleted(true);\n        driverRepository.save(thing);"),
    "a fix that adds an import": lambda text: text.replace("import java.util.List;", "import java.util.List;\nimport java.util.Objects;", 1).replace(
        "thing.setDeleted(true);", "thing.setDeleted(true);\n        Objects.requireNonNull(driverRepository.save(thing));", 1),
    "a fix that reformats the body": _edit("Thing thing = checked(driverId);\n        thing.setDeleted(true);",
                                          "Thing thing = checked(driverId);\n\n        thing.setDeleted(true);\n        driverRepository.save(thing);\n"),
}
ILLEGAL = {
    "another method's body changed": _edit("return checked(driverId);", "return checked(driverId); // x"),
    "a statement added to another method": _edit("return driverRepository.findAll();", "int x = 0;\n        return driverRepository.findAll();"),
    "the @Transactional annotation removed": _edit("    @Override\n    @Transactional\n    public void delete", "    @Override\n    public void delete"),
    "the signature type changed": _edit("public void delete(Long driverId)", "public void delete(long driverId)"),
    "the parameter renamed": _edit("public void delete(Long driverId)", "public void delete(Long id)"),
    "the throws clause dropped": _edit("public void delete(Long driverId) throws NotFound", "public void delete(Long driverId)"),
    "a method added": lambda text: text.rstrip()[:-1] + "\n    private void helper() {}\n}\n",
    "a field added": _edit("private final Repo driverRepository;", "private final Repo driverRepository;\n    private int extra;"),
    "delete removed": lambda text: re.sub(r"    /\*\*\n     \* Delete\..*?\n    \}\n", "", text, count=1, flags=re.S),
    "the javadoc above delete edited": _edit("     * Delete.", "     * Delete it."),
}


def _run(tmp_path, transform, *, with_base=True, with_candidate=True):
    root = tmp_path / "candidate"
    (root / ".kriya/authority/oracle/base").mkdir(parents=True)
    shutil.copy(ORACLE, root / ".kriya/authority/oracle/verify.sh")
    if with_base:
        (root / ".kriya/authority/oracle/base/DefaultDriverService.java").write_text(BASE)
    target = root / CANDIDATE
    if with_candidate:
        target.parent.mkdir(parents=True)
        target.write_text(transform(BASE))
    done = subprocess.run(["sh", ".kriya/authority/oracle/verify.sh"], cwd=root, capture_output=True, text=True, check=False)
    return done.returncode, done.stderr + done.stdout


@pytest.mark.parametrize("name", sorted(LEGAL))
def test_a_candidate_that_changes_only_the_delete_body_or_imports_passes(tmp_path, name):
    code, output = _run(tmp_path, LEGAL[name])
    assert code == 0, (name, output)


@pytest.mark.parametrize("name", sorted(ILLEGAL))
def test_any_other_change_contradicts_req_3(tmp_path, name):
    code, output = _run(tmp_path, ILLEGAL[name])
    assert code == 1 and "REQ-3 contradicted" in output, (name, output)


def test_a_missing_candidate_file_contradicts_and_a_missing_asset_is_indeterminate(tmp_path):
    assert _run(tmp_path / "a", lambda text: text, with_candidate=False)[0] == 1
    assert _run(tmp_path / "b", lambda text: text, with_base=False)[0] == 2


def test_the_oracle_is_executable_and_declares_its_exit_protocol():
    text = ORACLE.read_text()
    assert ORACLE.stat().st_mode & 0o111 and "Exit 0 = PASS, 1 =" in text and "INDETERMINATE" in text
