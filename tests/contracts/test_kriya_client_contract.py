"""The ``KriyaClient`` contract suite (full modularization, slice B4; TEST-MATRIX §2): every implementation runs this file.

``LocalKriyaClient`` is the only implementation now; a future ``RemoteKriyaClient`` registers in ``IMPLEMENTATIONS`` and must pass the
same checks: it exposes every port, each port method has exactly the contract's typed signature (parameter names, kinds and defaults;
every parameter and the return annotated; ``ctx: RequestContext`` first), every request context it builds carries its principal and the
requested workspace and never falls back to the process working directory, and its refusals use only the KUP error vocabulary.
Parameters that cannot cross a transport are a CLOSED list with removal slices (``LOCAL_ONLY_PARAMETERS``): an unlisted callable or
live-object parameter fails here.
"""
from __future__ import annotations

import dataclasses
import inspect
import os
from typing import Callable, Dict

import pytest

import kriya.application.compose as composition
from kriya.application import services
from kriya.client import KriyaClient, LocalKriyaClient, local_client
from kriya.client.contract import LOCAL_ONLY_PARAMETERS, PORTS
from kriya.context import PrincipalContext, RequestContext, SecurityDomain
from kriya.kup import policy

IMPLEMENTATIONS: Dict[str, Callable[[], KriyaClient]] = {"local": local_client}
# Parameter names whose values are plain data a transport can carry (identities, paths, bounded scalars, typed request values).
DATA_PARAMETERS = frozenset({"ctx", "workspace", "requested", "parent", "name", "launch_id", "review_id", "run_id", "goal", "asked", "skill_id",
                             "tool_id", "skills", "reviewed", "request", "cockpit_state_directory", "from_trace", "filters", "limit", "cursor",
                             "cursor_head", "after_seq", "max_page_bytes", "seq", "part", "record_kind", "content_class", "max_bytes",
                             "deadline_seconds", "max_endpoints",
                             # D2: a published trace snapshot named by id, or by its Snapshot value (id, directory, manifest), and the
                             # snapshot operations' bounded scalars
                             "snapshot_id", "snapshot", "verify", "keep"})


@pytest.fixture(params=sorted(IMPLEMENTATIONS))
def client(request) -> KriyaClient:
    return IMPLEMENTATIONS[request.param]()


def _public_methods(cls) -> Dict[str, Callable]:
    return {name: member for name, member in vars(cls).items() if callable(member) and not name.startswith("_")}


def test_the_client_satisfies_the_protocol_and_exposes_every_port(client):
    assert isinstance(client, KriyaClient)
    assert tuple(PORTS) == composition.SERVICE_NAMES, "the contract's ports are exactly the composed services"
    for port in PORTS:
        assert getattr(client, port) is getattr(client, port), f"{port}: one composed service per client, not one per access"


@pytest.mark.parametrize("port", sorted(PORTS))
def test_every_port_method_is_typed_and_the_implementation_has_exactly_its_signature(client, port):
    protocol, implementation = PORTS[port], getattr(client, port)
    methods = _public_methods(protocol)
    assert methods, port
    for name, declared in methods.items():
        contract = inspect.signature(declared)
        params = list(contract.parameters.values())
        assert params[0].name == "self" and params[1].name == "ctx" and "RequestContext" in str(params[1].annotation), f"{port}.{name}: ctx first"
        missing = [p.name for p in params[1:] if p.annotation is inspect.Parameter.empty]
        assert missing == [] and contract.return_annotation is not inspect.Signature.empty, f"{port}.{name}: untyped {missing}"
        actual = inspect.signature(getattr(type(implementation), name))
        shape = [(p.name, p.kind, p.default) for p in params]
        assert [(p.name, p.kind, p.default) for p in actual.parameters.values()] == shape, f"{port}.{name}: signature differs from the contract"
    extra = sorted(set(_public_methods(type(implementation))) - set(methods))
    assert extra == [], f"{port}: public methods outside the contract {extra}"


def test_the_local_only_parameters_are_a_closed_list_with_existing_removal_slices():
    from tests.architecture.test_import_direction import DONE_SLICES, SLICES

    found = set()
    for port, protocol in PORTS.items():
        for name, declared in _public_methods(protocol).items():
            for param in list(inspect.signature(declared).parameters)[1:]:
                if param not in DATA_PARAMETERS:
                    found.add((port, name, param))
    assert found == set(LOCAL_ONLY_PARAMETERS), "a parameter that cannot cross a transport must be listed (or removed) with its slice"
    assert all(slice_id in SLICES and slice_id not in DONE_SLICES for slice_id in LOCAL_ONLY_PARAMETERS.values())


def test_the_request_context_carries_the_principal_and_the_workspace_and_never_the_cwd(client, tmp_path):
    folder = str(tmp_path)
    ctx = client.context(workspace_root=folder, request_id="r-1")
    assert isinstance(ctx, RequestContext) and ctx.request_id == "r-1"
    assert ctx.principal == PrincipalContext.local() and ctx.principal.security_domain is SecurityDomain.LOCAL
    assert ctx.workspace is not None and os.path.samefile(ctx.workspace.root, folder)
    assert ctx.run is None and ctx.state_scope.kind == "user" and ("project", ctx.workspace.workspace_id) in {(s.kind, s.id) for s in ctx.config_scope}
    nowhere = client.context(workspace_root=None)
    assert nowhere.workspace is None and nowhere.request_id, "no workspace means none - never the process working directory"
    assert client.context(workspace_root=None).request_id != nowhere.request_id, "each request gets its own identity"


def test_a_local_client_for_another_principal_stamps_it_on_every_context(tmp_path):
    other = PrincipalContext(id="alice", display="Alice", security_domain=SecurityDomain.LOCAL)
    client = LocalKriyaClient(composition.compose(), principal=other)
    ctx = client.context(workspace_root=str(tmp_path))
    assert ctx.principal == other and dataclasses.replace(ctx, principal=PrincipalContext.local()) != ctx


def test_refusals_map_one_to_one_onto_the_kup_vocabulary():
    assert services.REFUSAL_CODES <= set(policy.ERROR_CODES)
    for code in services.REFUSAL_CODES:
        assert getattr(services, code) == getattr(policy, code) == code
    with pytest.raises(ValueError):
        services.ApplicationRefusal("NOT_A_KUP_CODE", "x")


def test_a_refusal_raised_through_the_client_is_the_wire_error(client, tmp_path):
    """The same typed refusal the adapter maps: a missing launch through ``runs.status`` and an invalid project name through ``projects.create``."""
    from kriya.config import AppConfig
    from kriya.kup.cli_ops import run_kup_operation

    ws = str(tmp_path)
    with pytest.raises(services.ApplicationRefusal) as caught:
        client.runs.status(client.context(workspace_root=ws), AppConfig(), ws, "20201010T101010-00000000")
    assert (caught.value.reason_code, caught.value.extra) == (policy.LAUNCH_NOT_FOUND, {"reason": "not_recorded"})
    wire = run_kup_operation(AppConfig(), {"operation": "launch.status", "workspace": ws, "launch_id": "20201010T101010-00000000"}, "r", client)
    assert wire["error"] == {"code": caught.value.reason_code, "message": caught.value.message, "database_state": None, **caught.value.extra}
    with pytest.raises(services.ApplicationRefusal) as named:
        client.projects.create(client.context(workspace_root=None), ws, ".hidden")
    assert named.value.reason_code == policy.INVALID_REQUEST and named.value.extra == {"reason": "invalid_name"}


def test_the_kup_adapter_reaches_the_application_only_through_the_client():
    from tests.architecture import module_map as mm

    table = mm.owner_table()
    reached = sorted({(e.source, e.target) for e in mm.scan()
                      if mm.logical(e.source, table) == "ADAPTER-KUP" and mm.logical(e.target, table) == "APPLICATION"})
    assert reached == [], "KUP translates transport only: the use cases are reached through kriya.client"
    via_client = {e.source for e in mm.scan() if mm.logical(e.source, table) == "ADAPTER-KUP" and mm.logical(e.target, table) == "CLIENT"}
    assert "kriya.kup.cli_ops" in via_client
