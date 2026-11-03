"""CERT-NODEID-GROUP-SUFFIX-001 sample (collected in a subprocess by tests/test_cert_nodeid_group_suffix.py): ids that contain ``@`` -- one of them ends exactly like an xdist group suffix -- on tests that carry an xdist_group marker."""
import pytest


@pytest.mark.xdist_group("grp")
@pytest.mark.parametrize("value", ["@Test-annotation", "plain", "ends@grp", "ends@other"])
def test_grouped(value):
    assert value


@pytest.mark.xdist_group(name="named")
@pytest.mark.xdist_group("alpha")
def test_two_groups():
    pass


def test_ungrouped_at_sign_param(request):
    assert "@" not in request.node.originalname
