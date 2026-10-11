"""BACKEND-READINESS-V2, PLAN-TARGET-GROUNDING-001 (a): Planner localization reproducers.

Mechanism measured on the frozen jsoup run (handover/BACKEND-READINESS-V2/01, case T2): the goal says
``Element.absUrl(...)`` and shows ``Jsoup.parse(...)`` in its example. ``absUrl`` is declared by Element's supertype
Node, not by Element; ``Jsoup.parse`` has fifteen overloads. Every overload of a qualified match was credited in full
and there was no credit for the inherited declaration, so the fifteen overloads filled the whole 12-slot candidate map
and the declaration the fix belongs in was never shown to the Planner. The fixtures below are small synthetic
repositories with that shape; nothing here is keyed to a task, repository or file name.
"""
import asyncio
import textwrap
from types import SimpleNamespace

from kriya.code_intel.service import CodeIntelligenceService
from kriya.workflow.graph_retrieval import LOCALIZATION_LIMIT, localization_candidates

NODE = textwrap.dedent("""\
    package lib.nodes;

    public abstract class Node extends Base {
        public String attr(String key) {
            return "";
        }

        public String absUrl(String key) {
            return Strings.resolve(key);
        }

        public String deep() {
            return "node";
        }
    }
    """)
ELEMENT = textwrap.dedent("""\
    package lib.nodes;

    public class Element extends Node {
        public String text() {
            return "";
        }

        public String attr(String key) {
            return "own";
        }
    }
    """)
BASE = textwrap.dedent("""\
    package lib.nodes;

    public abstract class Base {
        public String deep() {
            return "base";
        }

        public String root() {
            return "base";
        }
    }
    """)
_PARAMETER_TYPES = ["String", "int", "long", "double", "char", "byte", "short", "float", "boolean", "Object", "Integer",
                    "Long", "Double", "Character", "StringBuilder"]
PARSE_OVERLOADS = "\n".join(f"    public static Document parse({t} a) {{ return null; }}" for t in _PARAMETER_TYPES)
ENTRY = "package lib;\n\npublic final class Entry {\n" + PARSE_OVERLOADS + "\n}\n"
STRINGS = "package lib;\n\npublic final class Strings {\n    public static String resolve(String key) { return key; }\n}\n"
PATHS = {"src/main/java/lib/nodes/Base.java": BASE, "src/main/java/lib/nodes/Node.java": NODE, "src/main/java/lib/nodes/Element.java": ELEMENT,
         "src/main/java/lib/Entry.java": ENTRY, "src/main/java/lib/Strings.java": STRINGS}
GOAL = ("Element.absUrl(...) resolves relative links incorrectly.\n\n"
        "    Document doc = Entry.parse(\"<a href='?page=2'>Next</a>\");\n"
        "    doc.selectFirst(\"a\").absUrl(\"href\")\n")


def _service(tmp_path, paths=PATHS):
    root = tmp_path / "ws"
    for rel, text in paths.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    service = CodeIntelligenceService(str(root), str(tmp_path / "memory" / "dependency_graph.db"))
    service.refresh(list(paths))
    return root, service


def test_t2_a_goal_stated_owner_member_resolves_to_the_supertype_that_declares_it(tmp_path):
    """Element does not declare absUrl; its supertype Node does. Structural inheritance (``extends`` as written) names the
    declaration, and the hit says it was found that way."""
    _root, service = _service(tmp_path)
    try:
        hits = {h.lookup_key: h for h in service.locate(GOAL, limit=60)}
        absurl = hits["lib.nodes.Node.absUrl"]
        assert "inherited_member" in dict(absurl.channels)
        assert absurl.path == "src/main/java/lib/nodes/Node.java"
    finally:
        service.close()


def test_t2_inherited_member_negative_controls(tmp_path):
    """No credit when the owner itself declares the member, when no supertype declares it, or when the chain cycles."""
    cyc = {"A.java": "public class A extends B {\n    public void a() {}\n}\n",
           "B.java": "public class B extends A {\n    public void b() {}\n}\n", **PATHS}
    _root, service = _service(tmp_path, cyc)
    try:
        # declared by the owner itself: Node.attr is Node's own, nothing is inherited
        assert service.inherited_members("Node", "attr") == []
        # an override: Element declares attr itself although its supertype Node does too - nothing is inherited
        assert service.inherited_members("Element", "attr") == []
        # the NEAREST declaring level wins: Node and Base both declare deep(); Element inherits Node's
        assert [s.lookup_key for s in service.inherited_members("Element", "deep")] == ["lib.nodes.Node.deep"]
        # a member only a distant supertype declares is still found (two hops)
        assert [s.lookup_key for s in service.inherited_members("Element", "root")] == ["lib.nodes.Base.root"]
        assert service.inherited_members("Element", "text") == []  # Element's own member
        assert service.inherited_members("Element", "vanish") == []  # no supertype declares it
        assert service.inherited_members("Nope", "absUrl") == []  # unknown owner
        assert [s.lookup_key for s in service.inherited_members("Element", "absUrl")] == ["lib.nodes.Node.absUrl"]
        calls = []
        real = service._by_name
        service._by_name = lambda name: (calls.append(name), real(name))[1]
        assert service.inherited_members("A", "zzz") == []  # A <-> B cycle terminates ...
        assert calls == ["B", "A"], calls  # ... after exactly one lookup per supertype edge: the owner is never revisited
        service._by_name = real
        assert [s.lookup_key for s in service.inherited_members("A", "b")] == ["B.b"]  # one supertype hop
        hits = service.locate("Element.text() and Node.attr(...) are described", limit=60)
        assert all("inherited_member" not in dict(h.channels) for h in hits)
    finally:
        service.close()


def test_t2_the_planner_map_shows_the_declaring_supertype_and_folds_overloads(tmp_path):
    """The measured end state: fifteen overloads of one name must not crowd the declaration out of the Planner's candidate
    map. One entry per qualified name, best first, with the overload count; the inherited declaration is in the top three."""
    root, service = _service(tmp_path)
    service.close()
    cfg = SimpleNamespace(paths=SimpleNamespace(memory=str(tmp_path / "memory")))
    candidates = asyncio.run(localization_candidates(cfg, str(root), GOAL))
    keys = [c.lookup_key for c in candidates]
    assert len(keys) <= LOCALIZATION_LIMIT and len(keys) == len(set(keys))  # one entry per qualified name
    assert "lib.nodes.Node.absUrl" in keys[:3], keys
    parse = [c for c in candidates if c.lookup_key == "lib.Entry.parse"]
    assert len(parse) == 1 and parse[0].overloads == 15
    from kriya.workflow.graph_retrieval import render_localization_candidates

    rendered = render_localization_candidates(candidates)
    assert "lib.nodes.Node.absUrl" in rendered and "(+14 overloads)" in rendered
