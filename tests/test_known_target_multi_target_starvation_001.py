"""KNOWN-TARGET-MULTI-TARGET-STARVATION-001: one Developer invocation with two
existing targets, the first carrying grounded members and the second none,
must give EVERY target its minimum authoritative editable source before any
enrichment, or refuse typed for capacity - never starve the second target.

The two live failures (CAGC-0 A/B 2026-10-03, spring-xml B r3 and invalidurl
B r3; replay: evidence/known-target-starvation/replay.py) are reproduced on
the real direct path with their measured budget shape:
  * allocation window 8491 (live: 32768 context, 16384 output and the
    qualified bytes-per-token ratio; here the default ratio, so a 27748
    context with the same output gives the same 8491);
  * the known-target limit at its 1000-token floor: retrieval matched other
    repository files whose graph context filled the 0.60 pool (graph
    context counts against that limit, never against T0's room), so the
    window-reserve rebuild leaves the limit at 0. Here retrieval is a
    deterministic stand-in naming real neighbour files of the workspace;
    the graph context itself is built by the real allocator;
  * the first target's grounded members as the run recorded them (supplied
    at the grounding seam, _resolve_known_target_member_hints).
The Developer is scripted at the transport; the gates are stubbed green."""
import asyncio
import json
import re
from unittest.mock import AsyncMock, patch

import pytest
from _edit_protocol_harness import PRODUCTION_CAPABILITIES, _git, make_config
from _protocol_responses import as_requested

from kriya.core import model_runtime
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.workflow import attempt
from kriya.workflow.context_budget import (
    REASON_BUDGET_EXHAUSTED,
    REASON_MINIMUM_AUTHORITY_UNFIT,
    build_known_target_context,
    estimate_tokens,
)
from kriya.workflow.graph_retrieval import GraphRetrievalResult
from kriya.workflow.state import GenerationState
from kriya.workflow.workflow import WorkflowEngine

JAVA = "src/main/java/org/example/clinic/ClinicServiceImpl.java"
XML = "src/main/resources/spring/tools-config.xml"
PY_EXC = "httpx/_exceptions.py"
PY_URLS = "httpx/_urls.py"

_JAVA_METHODS = ("findPetTypes", "findVets", "findPetById", "findOwnerById", "savePet", "saveOwner",
                 "findOwnerByLastName", "saveVisit", "findVisitsByPetId", "savePetType")
JAVA_SOURCE = (
    "package org.example.clinic;\n\nimport java.util.Collection;\n\n"
    "public class ClinicServiceImpl implements ClinicService {\n\n"
    "    private final Repository repository;\n\n"
    "    public ClinicServiceImpl(Repository repository) {\n        this.repository = repository;\n    }\n\n"
    + "".join(f"    @Override\n    public Collection<Object> {name}() {{\n"
              f"        return repository.{name}();\n    }}\n\n" for name in _JAVA_METHODS)
    + "}\n"
)
JAVA_MEMBERS = [f"ClinicServiceImpl.{name}" for name in _JAVA_METHODS[:5]]

# 2089 bytes, the measured tools-config.xml size (estimate 522 tokens).
_XML_HEAD = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<beans xmlns="http://www.springframework.org/schema/beans"\n'
    '       xmlns:cache="http://www.springframework.org/schema/cache">\n'
    '    <cache:annotation-driven/>\n'
    '    <bean id="cacheManager" class="org.springframework.cache.jcache.JCacheCacheManager">\n'
    '        <property name="cacheManager" ref="jCacheManager"/>\n'
    '    </bean>\n'
)
_XML_TAIL = "</beans>\n"
XML_SOURCE = _XML_HEAD + "".join(
    f'    <!-- tooling bean {k}: performance monitoring and cache statistics -->\n' for k in range(40)
)[:2089 - len(_XML_HEAD) - len(_XML_TAIL)].rsplit("\n", 1)[0] + "\n" + _XML_TAIL

PY_EXC_SOURCE = (
    "class HTTPError(Exception):\n    def __init__(self, message: str) -> None:\n"
    "        super().__init__(message)\n\n\n"
    "class RequestError(HTTPError):\n    def __init__(self, message: str, *, request=None) -> None:\n"
    "        super().__init__(message)\n        self._request = request\n\n\n"
    "class InvalidURL(Exception):\n    def __init__(self, message: str) -> None:\n"
    "        super().__init__(message)\n"
)
PY_EXC_MEMBERS = ["InvalidURL.__init__", "HTTPError.__init__", "RequestError.__init__"]


def _py_module(size: int) -> str:
    out, k = '"""URL handling."""\n\n\n', 0
    while len(out) < size:
        out += f"def component_{k}(url, index):\n    value = url.split('/')[index]\n    return value.strip()\n\n\n"
        k += 1
    return out


# _urls.py as measured (21808 bytes, ~5450 tokens): larger than T0's whole room.
PY_URLS_MEASURED = _py_module(21808)
# The same shape with a second module that does fit T0's room.
PY_URLS_FITTING = _py_module(9000)

# Retrieval's matched neighbours: many medium files, so the real allocator
# fills the 0.60 graph pool to within one degradation step (as the live
# petclinic neighbourhood did) and the known-target limit falls to its floor.
NEIGHBOURS = {
    f"src/main/java/org/example/clinic/Neighbour{k}.java": "package org.example.clinic;\n\n"
    f"public class Neighbour{k} {{\n" + "".join(
        f"    public int value{m}(int input) {{\n" + "".join(
            f"        int step{n} = input * {m} + {n} - {k};\n" for n in range(6)) +
        "        return step5;\n    }\n\n" for m in range(8)) + "}\n"
    for k in range(30)
}


def _workspace(tmp_path, files):
    workspace = tmp_path / "ws"
    for path, text in files.items():
        (workspace / path).parent.mkdir(parents=True, exist_ok=True)
        (workspace / path).write_text(text)
    _git(workspace, "init", "-q")
    _git(workspace, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(workspace, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    return workspace


def _run(tmp_path, files, hints, edits):
    """One real direct run over the two targets; returns (result, events,
    Developer requests). ``edits``: the scripted answer per file."""
    model_runtime.clear_model_runtime_cache()
    cfg = make_config(tmp_path, 27748, PRODUCTION_CAPABILITIES, max_tokens=16384)
    workspace = _workspace(tmp_path, {**files, **NEIGHBOURS})
    targets = list(files)
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "vector_index.db").write_bytes(b"")  # retrieval runs only beside an index

    async def retrieval(*args, **kwargs):
        del args, kwargs
        return GraphRetrievalResult(matched=True, matched_files=sorted(NEIGHBOURS),
                                    file_scores={path: 1.5 for path in NEIGHBOURS})
    events, developer = [], []
    real_record = GenerationState.record_event

    async def transport(llm, client, model, system_prompt, user_prompt, *args, **kwargs):
        del llm, client, model, args, kwargs
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        if "File List Planner" in first:
            content = json.dumps({"files": targets})
        elif "Developer Agent" in first:
            developer.append((system_prompt, user_prompt))
            named = re.search(r'path="([^"]+)"', system_prompt or "")
            path = named.group(1) if named else targets[-1]
            content = as_requested(edits[path], system_prompt, path)
        else:
            content = "Review: Approved"
        prompt_tokens = len(((system_prompt or "") + (user_prompt or "")).encode("utf-8")) * 2 // 7
        return {"content": content, "reasoning_chars": 0, "prompt_tokens": prompt_tokens, "completion_tokens": 5,
                "finish_reason": "stop", "provider_metadata": {}}

    def record_spy(state, event):
        events.append(event)
        return real_record(state, event)

    engine = WorkflowEngine(Kernel(config=cfg), LLMClient(cfg))
    with patch.object(LLMClient, "_request_once", new=transport), \
         patch.object(GenerationState, "record_event", new=record_spy), \
         patch.object(attempt, "_resolve_known_target_member_hints", new=lambda ctx, paths: dict(hints)), \
         patch("kriya.workflow.workflow.retrieve_graph_context", new=retrieval), \
         patch("kriya.tools.validate.PolymorphicValidator.run_compile_check",
               new=lambda *a, **k: {"success": True, "output": "ok"}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests",
               new=lambda *a, **k: {"success": True, "output": "ok"}):
        result = asyncio.run(engine.run_generation_workflow(
            goal="Cache the lookups the same way the existing cached lookups are cached.",
            workspace_path=str(workspace), predetermined_plan="Change both files.", predetermined_design="",
            predetermined_architect_files=targets,
            approval_callback=AsyncMock(return_value=True)))
    return result, events, developer


def _first(events, kind):
    return next(event.details for event in events if event.kind == kind)


def _measured_shape(events):
    """The live runs' budget shape, asserted so the reproducer cannot drift
    away from it silently."""
    transition = _first(events, "model.transition")
    assert transition["to"]["allocation_window"] == 8491
    assert estimate_tokens("".join(NEIGHBOURS.values())) > int(8491 * 0.60)


XML_EDIT = ("FIX ANALYSIS: register the cache.\nSEARCH:\n    <cache:annotation-driven/>\n"
            "REPLACE:\n    <cache:annotation-driven/>\n    <!-- pet types cached -->\n")
JAVA_EDIT = ("FIX ANALYSIS: cache the lookup.\nSEARCH:\n        return repository.findPetTypes();\n"
             "REPLACE:\n        return repository.findPetTypes(); // cached\n")
EXC_EDIT = ("FIX ANALYSIS: keep the request.\nSEARCH:\n        self._request = request\n"
            "REPLACE:\n        self._request = request  # kept\n")
URLS_EDIT = ("FIX ANALYSIS: strip.\nSEARCH:\n    value = url.split('/')[index]\n    return value.strip()\n\n\n"
             "def component_1(url, index):\n"
             "REPLACE:\n    value = url.split('/')[index]\n    return value.strip(' ')\n\n\n"
             "def component_1(url, index):\n")


def test_spring_shape_second_target_gets_its_whole_source_before_any_enrichment(tmp_path):
    """spring-xml B r3: ClinicServiceImpl (5 grounded members) + tools-config.xml
    (no member, 522 tokens). Before the fix: tools-config.xml budget_exhausted,
    no operation, CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE and no Developer request."""
    result, events, developer = _run(tmp_path, {JAVA: JAVA_SOURCE, XML: XML_SOURCE}, {JAVA: JAVA_MEMBERS},
                                     {JAVA: JAVA_EDIT, XML: XML_EDIT})
    _measured_shape(events)
    package = _first(events, "context.known_target_package")
    tiers = {(t["path"], t["tier"]) for t in package["tiers"]}
    assert (XML, "full") in tiers and (JAVA, "member_exact") in tiers
    assert not [o for o in package["omitted"] if o["path"] == XML]
    capability = {t["path"]: t["operations"] for t in _first(events, "context.edit_capability")["targets"]}
    assert capability[XML] and capability[JAVA]
    assert developer, "the Developer must be asked: every target has authoritative editable source"
    assert XML_SOURCE in developer[0][1]
    assert result.get("failure_category") != "context_edit_protocol_unsatisfiable"


def test_httpx_shape_as_measured_is_a_typed_capacity_refusal_not_starvation(tmp_path):
    """invalidurl B r3: _exceptions.py (3 grounded members) + _urls.py (no
    member, ~5450 tokens). The whole _urls.py exceeds even T0's whole room
    (0.60 x 8491 = 5094), so the minimum set cannot fit: the refusal stays,
    before any model call, and now names the capacity limit."""
    result, events, developer = _run(tmp_path, {PY_EXC: PY_EXC_SOURCE, PY_URLS: PY_URLS_MEASURED},
                                     {PY_EXC: PY_EXC_MEMBERS}, {PY_EXC: EXC_EDIT, PY_URLS: URLS_EDIT})
    _measured_shape(events)
    assert estimate_tokens(PY_URLS_MEASURED) > int(8491 * 0.60)
    package = _first(events, "context.known_target_package")
    reasons = {o["reason"] for o in package["omitted"] if o["path"] == PY_URLS}
    assert REASON_MINIMUM_AUTHORITY_UNFIT in reasons
    assert result.get("failure_category") == "context_edit_protocol_unsatisfiable"
    assert developer == []


def test_httpx_shape_with_a_fitting_second_module_authorizes_both(tmp_path):
    result, events, developer = _run(tmp_path, {PY_EXC: PY_EXC_SOURCE, PY_URLS: PY_URLS_FITTING},
                                     {PY_EXC: PY_EXC_MEMBERS}, {PY_EXC: EXC_EDIT, PY_URLS: URLS_EDIT})
    _measured_shape(events)
    package = _first(events, "context.known_target_package")
    assert (PY_URLS, "full") in {(t["path"], t["tier"]) for t in package["tiers"]}
    capability = {t["path"]: t["operations"] for t in _first(events, "context.edit_capability")["targets"]}
    assert capability[PY_URLS] and capability[PY_EXC]
    assert developer and result.get("failure_category") != "context_edit_protocol_unsatisfiable"


# --- the allocation itself ------------------------------------------------


def _files(tmp_path, files):
    for path, text in files.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(text)


@pytest.mark.parametrize("limit", [0, 1000, 1400])
def test_every_minimum_is_admitted_before_any_enrichment(tmp_path, limit):
    """Pass 1 then pass 2: whatever the shared limit, the second target's
    whole source is admitted against T0's room before the first target's
    package, signatures or further members take anything."""
    _files(tmp_path, {JAVA: JAVA_SOURCE, XML: XML_SOURCE})
    _, package = build_known_target_context([JAVA, XML], str(tmp_path), None, limit,
                                            member_hints={JAVA: JAVA_MEMBERS}, exact_member_budget=4000)
    shown = [(item.path, item.tier) for item in package.relevant_files]
    assert (XML, "full") in shown
    paths = [path for path, _tier in shown]
    assert paths == sorted(paths, key=[JAVA, XML].index)  # rendered grouped by target, in rank order


def test_first_target_enrichment_can_no_longer_take_the_seconds_minimum(tmp_path):
    """Room for every minimum but not for the first target's enrichment too:
    the minimum wins, the enrichment is what gives way."""
    _files(tmp_path, {JAVA: JAVA_SOURCE, XML: XML_SOURCE})
    xml_cost = estimate_tokens(XML_SOURCE)
    _, alone = build_known_target_context([JAVA], str(tmp_path), None, 10_000, member_hints={JAVA: JAVA_MEMBERS[:1]})
    member_cost = estimate_tokens(alone.relevant_files[0].content)
    limit = member_cost + xml_cost + 5  # both minima, and too little for the member package beside them
    _, package = build_known_target_context([JAVA, XML], str(tmp_path), None, limit,
                                            member_hints={JAVA: JAVA_MEMBERS[:1]})
    shown = [(item.path, item.tier) for item in package.relevant_files]
    assert (XML, "full") in shown and (JAVA, "member_exact") in shown
    assert sum(estimate_tokens(item.content) for item in package.relevant_files) <= limit


def test_a_minimum_that_fits_nowhere_is_omitted_typed_and_limits_hold(tmp_path):
    _files(tmp_path, {PY_EXC: PY_EXC_SOURCE, PY_URLS: PY_URLS_MEASURED})
    _, package = build_known_target_context([PY_EXC, PY_URLS], str(tmp_path), None, 0,
                                            member_hints={PY_EXC: PY_EXC_MEMBERS}, exact_member_budget=5094)
    assert [o["reason"] for o in package.omitted if o["path"] == PY_URLS] == [
        REASON_MINIMUM_AUTHORITY_UNFIT, REASON_BUDGET_EXHAUSTED]
    assert all(item.path == PY_EXC for item in package.relevant_files)
    assert sum(estimate_tokens(i.content) for i in package.relevant_files) <= 5094


def test_single_target_package_is_unchanged(tmp_path):
    """One hinted target: members, then its package/signatures, exactly as
    before (the passes only reorder work ACROSS targets)."""
    _files(tmp_path, {JAVA: JAVA_SOURCE})
    _, package = build_known_target_context([JAVA], str(tmp_path), None, 600, member_hints={JAVA: JAVA_MEMBERS},
                                            exact_member_budget=4000)
    tiers = [(item.member_id, item.tier) for item in package.relevant_files]
    assert tiers[:5] == [(member, "member_exact") for member in JAVA_MEMBERS]
    assert tiers[5:] == [(None, "signatures")]


def _big_member_class(name: str, methods: int, lines: int) -> str:
    return f"public class {name} {{\n\n" + "".join(
        f"    public int m{m}(int input) {{\n" + "".join(
            f"        int step{n} = input * {m} + {n};\n" for n in range(lines)) + "        return input;\n    }\n\n"
        for m in range(methods)) + "}\n"


@pytest.mark.parametrize("with_t0", [True, False])
def test_a_targets_further_members_are_enrichment_after_every_minimum(tmp_path, with_t0):
    """Only a target's FIRST grounded member is its minimum: its other
    members wait until every target holds its minimum. Sized so the second
    member WOULD fit if it went first, and would then leave no room for the
    XML's whole source."""
    big = "src/Big.java"
    _files(tmp_path, {big: _big_member_class("Big", 2, 70), XML: XML_SOURCE})
    _, alone = build_known_target_context([big], str(tmp_path), None, 0, member_hints={big: ["Big.m0"]},
                                          exact_member_budget=10_000)
    member_cost = estimate_tokens(alone.relevant_files[0].content)
    room = 2 * member_cost + estimate_tokens(XML_SOURCE) - 10
    limit, t0 = (0, room) if with_t0 else (room, None)
    _, package = build_known_target_context([big, XML], str(tmp_path), None, limit,
                                            member_hints={big: ["Big.m0", "Big.m1"]}, exact_member_budget=t0)
    shown = [(item.path, item.member_id, item.tier) for item in package.relevant_files]
    # Whatever room is left goes to enrichment (here Big's signatures); the
    # minima come first and the second member is the one that gives way.
    assert [entry for entry in shown if entry[2] != "signatures"] == [(big, "Big.m0", "member_exact"),
                                                                       (XML, None, "full")]
    assert [o.get("member_id") for o in package.omitted if o["reason"] == "body_elided"] == ["Big.m1"]


def test_rendering_follows_rank_even_when_a_higher_target_is_filled_in_pass_two(tmp_path):
    """The higher-ranked target's whole source fits nowhere, so it is shown
    as a bounded excerpt in pass 2; it is still rendered first."""
    first = "pkg/first.py"
    _files(tmp_path, {first: _py_module(12_000), XML: XML_SOURCE})
    _, package = build_known_target_context([first, XML], str(tmp_path), None, 1500, exact_member_budget=600,
                                            file_scores={first: 0.9, XML: 0.8})
    assert [(item.path, item.tier) for item in package.relevant_files] == [(first, "skeleton"), (XML, "full")]


def test_minima_share_t0s_room_and_never_exceed_it(tmp_path):
    """Two whole-file minima that each fit T0's room but not together: the
    second is a typed capacity omission, never admitted past the room."""
    first, second = "pkg/first.py", "pkg/second.py"
    _files(tmp_path, {first: _py_module(12_000), second: _py_module(12_000)})
    t0 = estimate_tokens(_py_module(12_000)) + 100
    _, package = build_known_target_context([first, second], str(tmp_path), None, 0, exact_member_budget=t0)
    assert [(item.path, item.tier) for item in package.relevant_files] == [(first, "full")]
    assert [o["reason"] for o in package.omitted if o["path"] == second] == [
        REASON_MINIMUM_AUTHORITY_UNFIT, REASON_BUDGET_EXHAUSTED]
