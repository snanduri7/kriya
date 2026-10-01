"""Code Intelligence R1, Stages 5-6: the structural store (BaselineIndex),
CandidateOverlay and deterministic localization."""
import hashlib
import textwrap

import pytest

from kriya.code_intel import locate as loc
from kriya.code_intel.parsing import parse_text
from kriya.code_intel.service import CodeIntelligenceService
from kriya.code_intel.store import StructuralStore, identifier_terms

PRICING = textwrap.dedent("""\
    package shop.pricing;

    import java.util.List;

    public class PriceCalculator {
        private final TaxTable taxTable;

        public PriceCalculator(TaxTable taxTable) {
            this.taxTable = taxTable;
        }

        public long total(List<Item> items) {
            long sum = 0;
            for (Item item : items) {
                sum += item.cents();
            }
            return sum + taxTable.tax(sum);
        }

        public long total(Item item) {
            return item.cents();
        }

        long discount(long cents, int percent) {
            if (percent < 0) {
                throw new IllegalArgumentException("percent must not be negative");
            }
            return cents * percent / 100;
        }
    }
    """)

TAX = textwrap.dedent("""\
    package shop.pricing;

    public class TaxTable {
        long tax(long cents) {
            return cents / 5;
        }
    }
    """)

CLIENT = textwrap.dedent('''\
    class RetryPolicy:
        def __init__(self, attempts):
            self.attempts = attempts

        async def backoff_delay(self, attempt):
            """Exponential backoff."""
            return 2 ** attempt


    def parse_header(value):
        name, _, rest = value.partition(":")
        return name.strip(), rest.strip()
    ''')

PRICING_PATH = "src/main/java/shop/pricing/PriceCalculator.java"
TAX_PATH = "src/main/java/shop/pricing/TaxTable.java"
CLIENT_PATH = "client/retry.py"


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "ws"
    for rel, text in ((PRICING_PATH, PRICING), (TAX_PATH, TAX), (CLIENT_PATH, CLIENT)):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


@pytest.fixture
def service(workspace, tmp_path):
    svc = CodeIntelligenceService(str(workspace), str(tmp_path / "index.db"))
    svc.refresh([PRICING_PATH, TAX_PATH, CLIENT_PATH])
    yield svc
    svc.close()


def _count(svc, table):
    return svc.store.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_identifier_terms_split_camel_and_snake():
    assert identifier_terms("abbreviateMiddle parse_header HTTPServer") == [
        "abbreviatemiddle", "abbreviate", "middle", "parse_header", "parse", "header", "httpserver", "http", "server"]


def test_refresh_binds_every_file_to_its_raw_digest_and_skips_unchanged(workspace, tmp_path):
    svc = CodeIntelligenceService(str(workspace), str(tmp_path / "i.db"))
    try:
        first = svc.refresh([PRICING_PATH, TAX_PATH, CLIENT_PATH])
        assert sorted(first.parsed) == sorted([PRICING_PATH, TAX_PATH, CLIENT_PATH]) and first.states == {}
        digest = hashlib.sha256((workspace / PRICING_PATH).read_bytes()).hexdigest()
        assert svc.store.file_digest(PRICING_PATH) == digest
        assert all(s.source_digest == digest for s in svc.store.by_path(PRICING_PATH))
        second = svc.refresh([PRICING_PATH, TAX_PATH, CLIENT_PATH])
        assert second.parsed == [] and second.unchanged == 3
        (workspace / TAX_PATH).unlink()
        third = svc.refresh([PRICING_PATH, TAX_PATH, CLIENT_PATH])
        assert third.removed == [TAX_PATH] and svc.store.by_path(TAX_PATH) == []
    finally:
        svc.close()


def test_rows_of_another_parser_identity_are_never_returned(service):
    service.store.conn.execute("UPDATE ci_files SET parser_digest = 'other' WHERE path = ?", (TAX_PATH,))
    service.store.conn.commit()
    assert service.find_symbol("shop.pricing.TaxTable") == []
    assert service.store.file_digest(TAX_PATH) is None
    # and the next refresh re-parses it under the current identity
    assert TAX_PATH in service.refresh([TAX_PATH]).parsed
    assert service.find_symbol("shop.pricing.TaxTable")


def test_find_symbol_by_qualified_key_suffix_and_simple_name(service):
    totals = service.find_symbol("shop.pricing.PriceCalculator.total")
    assert [s.parameter_types for s in totals] == [("Item",), ("List<Item>",)]
    assert [s.lookup_key for s in service.find_symbol("PriceCalculator.discount")] == [
        "shop.pricing.PriceCalculator.discount"]
    assert [s.lookup_key for s in service.find_symbol("backoff_delay")] == ["client.retry.RetryPolicy.backoff_delay"]


def test_get_member_returns_exact_current_bytes_bound_to_their_digest(service, workspace):
    discount = service.find_symbol("PriceCalculator.discount")[0]
    member = service.get_member(discount.symbol_id)
    assert member.index_current is True
    assert member.text.startswith("long discount(long cents, int percent) {") and member.text.endswith("}")
    assert member.source_digest == hashlib.sha256((workspace / PRICING_PATH).read_bytes()).hexdigest()


def test_a_stale_index_is_never_mutation_input(service, workspace):
    """The file changed after indexing: get_member re-reads and re-parses the
    current bytes (new span, new digest) instead of slicing stale offsets."""
    discount = service.find_symbol("PriceCalculator.discount")[0]
    path = workspace / PRICING_PATH
    path.write_text(path.read_text().replace("    private final TaxTable taxTable;\n",
                                             "    private final TaxTable taxTable;\n    // a\n    // b\n"))
    member = service.get_member(discount.symbol_id)
    assert member.index_current is False
    assert member.span.start_line == discount.declaration.start_line + 2
    assert member.text.startswith("long discount(") and member.source_digest == hashlib.sha256(
        path.read_bytes()).hexdigest()
    assert service.member_at(PRICING_PATH, discount.declaration.start_line + 3).name == "discount"
    # Line 24 is now the last line of total(Item); in the stale index it was
    # discount's first line - only the current bytes give the right member.
    assert service.member_at(PRICING_PATH, 24).parameter_types == ("Item",)
    path.write_text(PRICING.replace("    long discount", "    long rebate"))
    assert service.get_member(discount.symbol_id) is None


def test_overlay_shadows_baseline_hides_tombstones_and_discards_cleanly(service):
    rows = (_count(service, "ci_symbols"), _count(service, "ci_files"))
    changed = PRICING.replace("long discount(long cents, int percent)", "long markdown(long cents, int percent)")
    view = service.with_overlay({PRICING_PATH: changed.encode(), TAX_PATH: None,
                                 "src/main/java/shop/pricing/Coupon.java": b"package shop.pricing;\n"
                                 b"public class Coupon { long apply(long c) { return c; } }\n"})
    assert view.find_symbol("PriceCalculator.discount") == []
    assert [s.name for s in view.find_symbol("PriceCalculator.markdown")] == ["markdown"]
    assert view.find_symbol("shop.pricing.TaxTable") == []
    assert view.find_symbol("Coupon.apply")[0].path.endswith("Coupon.java")
    assert view.find_symbol("backoff_delay")  # unchanged file answers from the baseline
    markdown = view.find_symbol("PriceCalculator.markdown")[0]
    member = view.get_member(markdown.symbol_id)
    assert member.text.startswith("long markdown(") and member.index_current is False
    assert view.get_member(service.find_symbol("shop.pricing.TaxTable")[0].symbol_id) is None
    # the overlay never wrote anything, and discarding it restores the baseline
    assert (_count(service, "ci_symbols"), _count(service, "ci_files")) == rows
    baseline = view.without_overlay()
    assert baseline.find_symbol("PriceCalculator.discount") and not baseline.find_symbol("PriceCalculator.markdown")


def test_locate_compiler_error_line_names_the_containing_member(service):
    hits = service.locate(f"[ERROR] /abs/repo/{PRICING_PATH}:[26,23] error: cannot find symbol")
    assert hits[0].lookup_key == "shop.pricing.PriceCalculator.discount"
    assert dict(hits[0].channels)["file_line"] == loc.FILE_LINE and hits[0].exact


def test_locate_stack_frame_and_python_traceback(service):
    frame = service.locate("java.lang.ArithmeticException: / by zero\n"
                           "\tat shop.pricing.TaxTable.tax(TaxTable.java:5)\n\tat shop.pricing.PriceCalculator.total"
                           "(PriceCalculator.java:17)")
    keys = [h.lookup_key for h in frame[:2]]
    assert set(keys) == {"shop.pricing.TaxTable.tax", "shop.pricing.PriceCalculator.total"}
    trace = service.locate(f'Traceback (most recent call last):\n  File "{CLIENT_PATH}", line 7, in backoff_delay')
    assert trace[0].lookup_key == "client.retry.RetryPolicy.backoff_delay"


def test_exact_symbol_evidence_outranks_lexical_similarity(service):
    hits = service.locate("PriceCalculator.discount rejects a negative percent")
    assert hits[0].lookup_key == "shop.pricing.PriceCalculator.discount"
    assert "qualified_symbol" in dict(hits[0].channels)


def test_a_named_type_scopes_its_members_instead_of_winning(service):
    hits = service.locate("PriceCalculator should apply the tax table to the total")
    assert hits[0].kind == "method" and hits[0].lookup_key == "shop.pricing.PriceCalculator.total"
    assert "owner_named" in dict(hits[0].channels)


def test_string_literal_and_identifier_channels(service):
    hits = service.locate('error message "percent must not be negative" is confusing')
    assert hits[0].lookup_key == "shop.pricing.PriceCalculator.discount"
    assert "string_literal" in dict(hits[0].channels)
    assert service.locate("parse_header drops the value")[0].lookup_key == "client.retry.parse_header"


def test_locate_is_deterministic_and_every_hit_carries_provenance(service):
    query = "total price with tax for items"
    first, second = service.locate(query), service.locate(query)
    assert first == second and first
    assert all(hit.channels for hit in first)


def test_store_without_fts_still_answers_exact_channels(workspace, tmp_path, monkeypatch):
    original = StructuralStore._init_schema

    def no_fts(self):
        original(self)
        self.fts_available = False
    monkeypatch.setattr(StructuralStore, "_init_schema", no_fts)
    svc = CodeIntelligenceService(str(workspace), str(tmp_path / "nofts.db"))
    try:
        svc.refresh([PRICING_PATH])
        assert svc.store.search(["total"], 5) == []
        assert svc.locate(f"{PRICING_PATH}:26")[0].lookup_key == "shop.pricing.PriceCalculator.discount"
    finally:
        svc.close()


def test_member_at_uses_the_most_specific_declaration():
    structure = parse_text("A.java", "class A {\n  class B {\n    void f() {\n      int x;\n    }\n  }\n}\n")
    assert structure.symbol_at_line(4).lookup_key == "A.B.f"
    assert structure.symbol_at_line(2).lookup_key == "A.B"


# --- Stage 7: member-level packing (T0..T2) ---

def test_t0_carries_the_exact_member_bound_to_path_digest_span_and_id(service, workspace):
    target = service.find_symbol("PriceCalculator.discount")[0]
    package = service.build_context(target.symbol_id)
    rendered = package.render()
    digest = hashlib.sha256((workspace / PRICING_PATH).read_bytes()).hexdigest()
    assert package.member_text == service.get_member(target.symbol_id).text
    assert f"{PRICING_PATH}:24-29 sha256={digest} id={target.symbol_id}" in rendered
    # the enclosing header: package, only the imports the member uses, type, fields, constructors
    assert "package shop.pricing;" in package.header and "import java.util.List;" not in package.header
    assert "public class PriceCalculator" in package.header
    assert any("private final TaxTable taxTable" in line for line in package.header)
    assert any("public PriceCalculator(TaxTable taxTable)" in line for line in package.header)
    # stable material first, the volatile authoritative member last
    assert rendered.rstrip().endswith(package.member_text)


def test_t1_collaborators_and_t2_linked_tests(service, workspace):
    test_path = "src/test/java/shop/pricing/PriceCalculatorTest.java"
    (workspace / test_path).parent.mkdir(parents=True)
    (workspace / test_path).write_text("package shop.pricing;\nclass PriceCalculatorTest {\n"
                                       "  void testTotalWithTax() { }\n  void testDiscount() { }\n}\n")
    service.refresh([test_path])
    total = next(s for s in service.find_symbol("PriceCalculator.total") if s.parameter_types == ("List<Item>",))
    package = service.build_context(total.symbol_id)
    assert [key for key, _ in package.collaborators] == ["shop.pricing.TaxTable"]
    assert package.collaborators[0][1] == ["long tax(long cents)"]
    assert [sig for _, sig in package.tests] == ["void testTotalWithTax()"]
    assert "import java.util.List;" in package.header  # used by this member


def test_t0_is_never_dropped_for_a_budget_and_optional_tiers_yield_first(service):
    target = service.find_symbol("PriceCalculator.discount")[0]
    tiny = service.build_context(target.symbol_id, budget_tokens=5)
    assert tiny.over_budget and tiny.member_text.startswith("long discount(") and tiny.collaborators == []
    total = next(s for s in service.find_symbol("PriceCalculator.total") if s.parameter_types == ("List<Item>",))
    full = service.build_context(total.symbol_id)
    tight = service.build_context(total.symbol_id, budget_tokens=len(full.render()) // 4 - 5)
    assert tight.member_text == full.member_text and not tight.over_budget
    assert tight.dropped and len(tight.collaborators) < len(full.collaborators)


def test_t0_comes_from_the_candidate_overlay_when_one_is_active(service):
    changed = PRICING.replace("return cents * percent / 100;", "return cents * percent / 1000;")
    view = service.with_overlay({PRICING_PATH: changed.encode()})
    target = view.find_symbol("PriceCalculator.discount")[0]
    package = view.build_context(target.symbol_id)
    assert "/ 1000;" in package.member_text
    assert package.source_digest == hashlib.sha256(changed.encode()).hexdigest()
