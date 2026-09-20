"""A1-P1 (Java Repository-Aware Code Review, 2026-09-09): deterministic
member-inventory extraction. See kriya/analyzer/java_members.py's own
module docstring for the exact, tested scope boundary (what this scanner
does and deliberately does not support)."""
from kriya.analyzer.java_members import extract_java_members


def _one(source, kind="method"):
    members = extract_java_members(source)
    matches = [m for m in members if m.kind == kind]
    assert len(matches) == 1, f"expected exactly one {kind} in {members!r}"
    return matches[0]


# --- 1-4: constructor visibility ---

def test_public_constructor():
    m = _one("public class Foo { public Foo(int x) { int y = x; } }", "constructor")
    assert m.visibility == "public"
    assert m.name == "Foo"
    assert m.return_type is None
    assert m.parameter_types == ("int",)


def test_protected_constructor():
    m = _one("public class Foo { protected Foo(int x) { } }", "constructor")
    assert m.visibility == "protected"


def test_package_private_constructor():
    m = _one("public class Foo { Foo(int x) { } }", "constructor")
    assert m.visibility == "package-private"


def test_private_constructor():
    m = _one("public class Foo { private Foo() { } }", "constructor")
    assert m.visibility == "private"
    assert m.parameter_types == ()


# --- 5-8: method visibility ---

def test_public_method():
    m = _one("public class Foo { public String bar(int x) { return null; } }")
    assert m.visibility == "public" and m.return_type == "String"


def test_protected_method():
    m = _one("public class Foo { protected String bar(int x) { return null; } }")
    assert m.visibility == "protected"


def test_package_private_method():
    m = _one("public class Foo { String bar(int x) { return null; } }")
    assert m.visibility == "package-private"


def test_private_method():
    m = _one("public class Foo { private String bar(int x) { return null; } }")
    assert m.visibility == "private"


# --- 9-10: modifiers ---

def test_static_method():
    m = _one("public class Foo { public static int bar() { return 1; } }")
    assert m.is_static is True


def test_final_method():
    m = _one("public class Foo { public final int bar() { return 1; } }")
    assert m.is_final is True


# --- 11-12: annotations ---

def test_single_annotation():
    m = _one("public class Foo {\n    @Override\n    public String bar() { return null; }\n}")
    assert m.annotations == ("@Override",)


def test_multiple_annotations():
    m = _one(
        "public class Foo {\n    @Override\n    @Deprecated\n"
        "    public String bar() { return null; }\n}"
    )
    assert m.annotations == ("@Override", "@Deprecated")


# --- 13: throws ---

def test_throws_clause():
    m = _one("public class Foo { public void bar() throws java.io.IOException { } }")
    assert m.throws == ("java.io.IOException",)


# --- 14-17: type shapes ---

def test_generic_return_type():
    m = _one("public class Foo { public java.util.List<String> bar() { return null; } }")
    assert m.return_type == "java.util.List<String>"


def test_generic_parameters():
    """A generic parameter type's own embedded comma (Map<String, Integer>)
    must not be split as if it were two separate parameters - the exact
    case _normalize_java_type_list()'s naive comma-split (file_resolution.py)
    would get wrong; this module's own _split_top_level_commas() is
    bracket-depth-aware specifically to handle it."""
    m = _one("public class Foo { public void bar(java.util.Map<String, Integer> m) { } }")
    assert m.parameter_types == ("java.util.Map<String, Integer>",)


def test_array_parameter():
    m = _one("public class Foo { public void bar(String[] args) { } }")
    assert m.parameter_types == ("String[]",)


def test_varargs_parameter():
    m = _one("public class Foo { public void bar(String... args) { } }")
    assert m.parameter_types == ("String...",)


# --- 18-19: multiline declarations ---

def test_multiline_method_declaration():
    m = _one("public class Foo { public void bar(\n    int x,\n    int y\n) {\n    int z = x + y;\n} }")
    assert m.parameter_types == ("int", "int")
    assert m.start_line == 1
    assert m.end_line == 6


def test_multiline_constructor_declaration():
    m = _one("public class Foo { public Foo(\n    int x,\n    int y\n) {\n} }", "constructor")
    assert m.parameter_types == ("int", "int")


# --- 20: nested braces in body ---

def test_method_body_with_nested_braces_finds_real_closing_brace():
    source = (
        "public class Foo {\n"
        "    public void bar() {\n"
        "        if (true) {\n"
        "            for (int i = 0; i < 10; i++) {\n"
        "                int x = i;\n"
        "            }\n"
        "        }\n"
        "    }\n"
        "}"
    )
    m = _one(source)
    assert m.start_line == 2
    assert m.end_line == 8
    # No false-positive members from the nested if/for control-flow blocks.
    assert len(extract_java_members(source)) == 1


# --- 21-22: comment/string-literal safety ---

def test_comment_containing_method_looking_text_is_not_a_member():
    source = "public class Foo {\n    // public void fakeMethod() {}\n    public void bar() { }\n}"
    members = extract_java_members(source)
    assert len(members) == 1
    assert members[0].name == "bar"


def test_string_literal_containing_braces_is_not_a_member():
    source = 'public class Foo { public String bar() { return "public void fake() { }"; } }'
    members = extract_java_members(source)
    assert len(members) == 1
    assert members[0].name == "bar"


# --- 23-24: overloads ---

def test_overloaded_methods_each_get_own_record():
    members = extract_java_members(
        "public class Foo { public void bar() { } public void bar(int x) { } }"
    )
    assert [m.parameter_types for m in members] == [(), ("int",)]


def test_overloaded_constructors_each_get_own_record():
    members = extract_java_members(
        "public class Foo { public Foo() { } public Foo(int x) { } }"
    )
    assert [m.parameter_types for m in members] == [(), ("int",)]


# --- documented, explicit limitations (not silently mishandled) ---

def test_interface_method_has_no_body_and_is_detected():
    source = (
        "public interface DriverService {\n"
        "    DriverDO find(Long driverId) throws EntityNotFoundException;\n"
        "    void delete(Long driverId) throws EntityNotFoundException;\n"
        "}"
    )
    members = extract_java_members(source)
    assert [m.name for m in members] == ["find", "delete"]
    assert all(m.has_body is False for m in members)
    # Documented limitation: visibility is lexical, not semantic - an
    # interface member with no explicit modifier is implicitly public by
    # the JLS, but reported "package-private" here since no modifier
    # keyword is textually present.
    assert all(m.visibility == "package-private" for m in members)


def test_record_compact_canonical_constructor_is_not_detected():
    """Documented limitation: a record's COMPACT canonical constructor
    (no parameter list at all) is out of scope for this scanner, which
    requires a parenthesized parameter list for every constructor. An
    ordinary (explicit-parameter-list) canonical constructor IS detected
    normally - only the parameter-list-free compact form is missed."""
    source = (
        "public record Customer(long id, String name) {\n"
        "    public Customer {\n"
        "        if (id < 0) throw new IllegalArgumentException();\n"
        "    }\n"
        "    public String greet() { return \"hi \" + name; }\n"
        "}"
    )
    members = extract_java_members(source)
    assert [m.name for m in members] == ["greet"]


def test_second_top_level_type_members_not_scanned():
    """Documented, deliberate bound: only the first-declared top-level
    type's own direct members are scanned - a second top-level type in
    the same file is out of scope for this module's "one target file,
    one primary type" A1 usage."""
    source = (
        "public class Foo { public void bar() { } }\n"
        "class Helper { public void helperMethod() { } }"
    )
    members = extract_java_members(source)
    assert [m.name for m in members] == ["bar"]


# --- the frozen A1 ground-truth target ---

_DEFAULT_DRIVER_SERVICE_SOURCE = """package com.myapp.service.driver;

import com.myapp.dataaccessobject.DriverRepository;
import com.myapp.domainobject.CarDO;
import com.myapp.domainobject.DriverDO;
import com.myapp.domainvalue.GeoCoordinate;
import com.myapp.domainvalue.OnlineStatus;
import com.myapp.exception.ConstraintsViolationException;
import com.myapp.exception.EntityNotFoundException;
import java.util.List;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * Service to encapsulate the link between DAO and controller and to have business logic for some driver specific things.
 * <p/>
 */
@Service
public class DefaultDriverService implements DriverService
{

    private static org.slf4j.Logger LOG = LoggerFactory.getLogger(DefaultDriverService.class);

    private final DriverRepository driverRepository;


    public DefaultDriverService(final DriverRepository driverRepository)
    {
        this.driverRepository = driverRepository;
    }


    @Override
    @Transactional
    public DriverDO find(Long driverId) throws EntityNotFoundException
    {
        return findDriverChecked(driverId);
    }


    @Override
    @Transactional
    public DriverDO create(DriverDO driverDO) throws ConstraintsViolationException
    {
        DriverDO driver;
        try
        {
            driver = driverRepository.save(driverDO);
        }
        catch (DataIntegrityViolationException e)
        {
            LOG.warn("Some constraints are thrown due to driver creation", e);
            throw new ConstraintsViolationException(e.getMessage());
        }
        return driver;
    }


    @Override
    @Transactional
    public void delete(Long driverId) throws EntityNotFoundException
    {
        DriverDO driverDO = findDriverChecked(driverId);
        driverDO.setDeleted(true);
    }


    @Override
    @Transactional
    public void updateLocation(long driverId, double longitude, double latitude) throws EntityNotFoundException, ConstraintsViolationException {
        DriverDO driverDO = findDriverChecked(driverId);
        driverDO.setCoordinate(new GeoCoordinate(latitude, longitude));
        create(driverDO);
    }

    @Override
    @Transactional
    public void updateCar(long driverId, CarDO carDO) throws EntityNotFoundException, ConstraintsViolationException {
        DriverDO driverDO = findDriverChecked(driverId);
        driverDO.setCar(carDO);
        create(driverDO);
    }


    @Override
    public List<DriverDO> find(OnlineStatus onlineStatus)
    {
        return driverRepository.findByOnlineStatus(onlineStatus);
    }

    @Override
    public Iterable<DriverDO> findAll()
    {
        return driverRepository.findAll();
    }


    private DriverDO findDriverChecked(Long driverId) throws EntityNotFoundException
    {
        return driverRepository.findById(driverId)
            .orElseThrow(() -> new EntityNotFoundException("Could not find entity with id: " + driverId));
    }

}
"""


def test_frozen_a1_target_default_driver_service_inventory():
    """A1 ground-truth acceptance: exactly 9 members (1 constructor + 8
    methods), 100% recall of every previously-established expected
    member, zero invented members. A byte-identical copy of the real
    fixture source is embedded above (not read from the external
    kriya-live-validation checkout) so this test is self-contained and
    reproducible in CI without that external path existing."""
    members = extract_java_members(_DEFAULT_DRIVER_SERVICE_SOURCE)
    assert len(members) == 9

    by_name = {}
    for m in members:
        by_name.setdefault(m.name, []).append(m)

    assert len(by_name["DefaultDriverService"]) == 1
    ctor = by_name["DefaultDriverService"][0]
    assert ctor.kind == "constructor"
    assert ctor.visibility == "public"
    assert ctor.parameter_types == ("DriverRepository",)

    expected_methods = {
        "find": [("Long",), ("OnlineStatus",)],  # overloaded
        "create": [("DriverDO",)],
        "delete": [("Long",)],
        "updateLocation": [("long", "double", "double")],
        "updateCar": [("long", "CarDO")],
        "findAll": [()],
        "findDriverChecked": [("Long",)],
    }
    for name, expected_param_sets in expected_methods.items():
        assert name in by_name, f"expected member {name!r} not found - recall failure"
        actual_param_sets = sorted(m.parameter_types for m in by_name[name])
        assert actual_param_sets == sorted(expected_param_sets), (
            f"{name}: expected {expected_param_sets}, got {actual_param_sets}"
        )

    # Zero invented members: every found name is one of the 8 expected.
    expected_names = {"DefaultDriverService", "find", "create", "delete",
                       "updateLocation", "updateCar", "findAll", "findDriverChecked"}
    assert set(by_name.keys()) == expected_names

    assert by_name["findDriverChecked"][0].visibility == "private"
    assert by_name["find"][0].kind == "method"
