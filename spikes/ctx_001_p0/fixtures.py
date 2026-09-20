"""CTX-001 P0 deterministic fixture generators.

All fixtures are generated programmatically (not hand-authored one-off files)
so bands are reproducible and their exact ground truth is known independently
of any LLM. Nothing here imports kriya/ - these are plain file-tree builders.
"""
import os
import random
import textwrap

SEED = 20260917


# ---------------------------------------------------------------------------
# CORE fixture: a small, realistic cross-module relationship (S1 relevant
# core, and S3's caller -> interface -> implementation -> test chain).
#
# Ground truth for a goal like "apply a 7% regional surcharge inside
# InvoiceCalculator's tax computation":
#   MUST_CHANGE:   core/billing/invoice_impl.py (StandardInvoiceCalculator.calculate_total)
#   MUST_PRESERVE: core/billing/invoice_interface.py (InvoiceCalculator ABC - signature)
#                  core/billing/caller.py (CheckoutService - must not need edits)
#   RELEVANT_MEMBERS: InvoiceCalculator.calculate_total (interface),
#                      StandardInvoiceCalculator.calculate_total (impl, the true target)
#   REQUIRED_RELATIONSHIPS: caller.py imports+calls invoice_impl.StandardInvoiceCalculator,
#                            invoice_impl.py imports+implements invoice_interface.InvoiceCalculator,
#                            test_invoice.py imports+calls caller.CheckoutService
# ---------------------------------------------------------------------------

CORE_FILES = {
    "core/billing/__init__.py": "",
    "core/billing/invoice_interface.py": textwrap.dedent('''\
        """Billing interface contract."""
        from abc import ABC, abstractmethod
        from typing import List


        class LineItem:
            def __init__(self, sku: str, unit_price: float, quantity: int) -> None:
                self.sku = sku
                self.unit_price = unit_price
                self.quantity = quantity


        class InvoiceCalculator(ABC):
            """Contract every concrete invoice calculator must satisfy."""

            @abstractmethod
            def calculate_total(self, items: List[LineItem]) -> float:
                """Return the total charge for the given line items."""
                raise NotImplementedError
    '''),
    "core/billing/invoice_impl.py": textwrap.dedent('''\
        """Concrete invoice calculator - the relevant member for CTX-001 fixtures."""
        from typing import List

        from core.billing.invoice_interface import InvoiceCalculator, LineItem

        BASE_TAX_RATE = 0.05


        class StandardInvoiceCalculator(InvoiceCalculator):
            """Standard flat-tax invoice calculator."""

            def calculate_total(self, items: List[LineItem]) -> float:
                """RELEVANT_MEMBER: the true modification target for CTX-001 fixtures."""
                subtotal = sum(item.unit_price * item.quantity for item in items)
                tax = subtotal * BASE_TAX_RATE
                return round(subtotal + tax, 2)
    '''),
    "core/billing/caller.py": textwrap.dedent('''\
        """Consumer of InvoiceCalculator - must remain untouched by a correctly
        scoped change to StandardInvoiceCalculator's tax computation."""
        from typing import List

        from core.billing.invoice_impl import StandardInvoiceCalculator
        from core.billing.invoice_interface import LineItem


        class CheckoutService:
            def __init__(self) -> None:
                self.calculator = StandardInvoiceCalculator()

            def checkout(self, items: List[LineItem]) -> float:
                return self.calculator.calculate_total(items)
    '''),
    "core/billing/test_invoice.py": textwrap.dedent('''\
        """Caller-side test - exercises CheckoutService, not the calculator directly."""
        from core.billing.caller import CheckoutService
        from core.billing.invoice_interface import LineItem


        def test_checkout_basic_total():
            svc = CheckoutService()
            items = [LineItem("sku-1", 10.0, 2), LineItem("sku-2", 5.0, 1)]
            total = svc.checkout(items)
            assert total > 0
    '''),
}

CORE_GROUND_TRUTH = {
    "must_change": ["core/billing/invoice_impl.py"],
    "must_preserve": ["core/billing/invoice_interface.py", "core/billing/caller.py"],
    "relevant_members": [
        ("core/billing/invoice_interface.py", "InvoiceCalculator.calculate_total"),
        ("core/billing/invoice_impl.py", "StandardInvoiceCalculator.calculate_total"),
    ],
    "required_relationships": [
        ("core/billing/caller.py", "core/billing/invoice_impl.py", "imports+calls"),
        ("core/billing/invoice_impl.py", "core/billing/invoice_interface.py", "imports+implements"),
        ("core/billing/test_invoice.py", "core/billing/caller.py", "imports+calls"),
    ],
    "goal": (
        "Apply a 7% regional surcharge on top of the existing base tax inside "
        "StandardInvoiceCalculator's total computation, without changing the "
        "InvoiceCalculator interface or any caller."
    ),
}


def write_core(root: str) -> None:
    for rel_path, content in CORE_FILES.items():
        _write(root, rel_path, content)


# ---------------------------------------------------------------------------
# S1 - repository breadth filler. Deterministic, template-based, irrelevant
# Python modules (unrelated domain: inventory/shipping/reporting boilerplate)
# so they are realistic-shaped but never touch billing/invoice/tax/surcharge
# vocabulary at all - a precision probe for retrieval can therefore check
# "did an irrelevant filler file get selected instead of the real target."
# ---------------------------------------------------------------------------

_FILLER_DOMAINS = [
    "inventory", "shipping", "reporting", "auth", "notifications",
    "catalog", "search", "analytics", "scheduling", "audit",
]

_FILLER_TEMPLATE = textwrap.dedent('''\
    """Generated filler module {index} ({domain}) - irrelevant to the billing fixture."""
    from typing import Dict, List, Optional


    class {class_name}:
        """Unrelated {domain} helper #{index}."""

        def __init__(self, config: Optional[Dict[str, str]] = None) -> None:
            self.config = config or {{}}
            self._cache: Dict[str, List[str]] = {{}}

        def process(self, payload: List[str]) -> List[str]:
            result = []
            for entry in payload:
                normalized = entry.strip().lower()
                if normalized:
                    result.append(normalized)
            self._cache[str(len(result))] = result
            return result

        def summarize(self) -> str:
            return f"{class_name} processed {{len(self._cache)}} batches"


    def helper_{index}(value: int) -> int:
        return value * {index} + 1
''')


def write_filler(root: str, count: int, start_index: int = 0) -> None:
    rng = random.Random(SEED)
    for i in range(start_index, start_index + count):
        domain = _FILLER_DOMAINS[i % len(_FILLER_DOMAINS)]
        class_name = f"{domain.capitalize()}Handler{i}"
        content = _FILLER_TEMPLATE.format(index=i, domain=domain, class_name=class_name)
        pkg = f"filler_{domain}"
        _write(root, f"{pkg}/module_{i}.py", content)
        _write(root, f"{pkg}/__init__.py", "")


# Bands: (name, filler_file_count) - core (5 files) held constant across all bands.
S1_BANDS = [
    ("small", 45),      # ~50 files total
    ("medium", 245),    # ~250 files total
    ("large", 745),     # ~750 files total
    ("very_large", 1495),  # ~1500 files total
]


# ---------------------------------------------------------------------------
# S2 - large file depth. Embeds the SAME relevant method (byte-identical
# logic to StandardInvoiceCalculator.calculate_total) inside a single large
# class, padded with deterministic boilerplate methods, at controlled
# positions: near the top, near the bottom, or with two required members
# (interface-shaped abstract + impl) split far apart across the file.
# ---------------------------------------------------------------------------

_PAD_METHOD_TEMPLATE = textwrap.dedent('''\
    def _padding_method_{index}(self, value: int) -> int:
        """Padding method #{index} - irrelevant, exists only to add file length."""
        total = 0
        for i in range(value % 7 + 1):
            total += i * {index}
        return total
''')


def _indent(text: str, spaces: int = 4) -> str:
    pad = " " * spaces
    return "\n".join(pad + line if line else line for line in text.splitlines())


def build_large_file(target_lines: int, placement: str) -> str:
    """placement: 'near_start' | 'near_end' | 'far_apart'.

    For 'far_apart' the file contains TWO relevant members (an interface-shaped
    abstract method near the top, and the concrete override near the bottom) -
    both required to correctly reason about the change (F3 probe).
    """
    header = textwrap.dedent('''\
        """Synthetic large file - CTX-001 S2 fixture (placement={placement}, target_lines={target_lines})."""
        from typing import List


        class LineItem:
            def __init__(self, sku: str, unit_price: float, quantity: int) -> None:
                self.sku = sku
                self.unit_price = unit_price
                self.quantity = quantity


        class LargeInvoiceProcessor:
    ''').format(placement=placement, target_lines=target_lines)

    relevant_method = textwrap.dedent('''\
        def calculate_total(self, items: List[LineItem]) -> float:
            """RELEVANT_MEMBER: the true modification target for CTX-001 S2 fixtures."""
            subtotal = sum(item.unit_price * item.quantity for item in items)
            tax = subtotal * 0.05
            return round(subtotal + tax, 2)
    ''')

    relevant_interface_method = textwrap.dedent('''\
        def calculate_total_contract(self, items: List[LineItem]) -> float:
            """RELEVANT_MEMBER (interface half, far_apart placement only)."""
            raise NotImplementedError
    ''')

    lines = header.splitlines()
    body_lines: list = []

    def pad_count_for(remaining_lines: int) -> int:
        # Each padding method is 6 lines (template) - solve how many needed.
        return max(0, remaining_lines // 6)

    if placement == "near_start":
        body_lines.append(_indent(relevant_method))
        remaining = target_lines - len(lines) - len(relevant_method.splitlines())
        n_pad = pad_count_for(remaining)
        for i in range(n_pad):
            body_lines.append(_indent(_PAD_METHOD_TEMPLATE.format(index=i)))
    elif placement == "near_end":
        remaining = target_lines - len(lines) - len(relevant_method.splitlines())
        n_pad = pad_count_for(remaining)
        for i in range(n_pad):
            body_lines.append(_indent(_PAD_METHOD_TEMPLATE.format(index=i)))
        body_lines.append(_indent(relevant_method))
    elif placement == "far_apart":
        body_lines.append(_indent(relevant_interface_method))
        remaining = target_lines - len(lines) - len(relevant_method.splitlines()) - len(relevant_interface_method.splitlines())
        n_pad = pad_count_for(remaining)
        half = n_pad // 2
        for i in range(half):
            body_lines.append(_indent(_PAD_METHOD_TEMPLATE.format(index=i)))
        body_lines.append(_indent(relevant_method))
        for i in range(half, n_pad):
            body_lines.append(_indent(_PAD_METHOD_TEMPLATE.format(index=i)))
    else:
        raise ValueError(f"unknown placement: {placement}")

    return "\n\n".join(lines) + "\n\n" + "\n\n".join(body_lines) + "\n"


S2_LINE_BANDS = [500, 1000, 2000, 3000]
S2_PLACEMENTS = ["near_start", "near_end", "far_apart"]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _write(root: str, rel_path: str, content: str) -> None:
    full = os.path.join(root, rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(content)


def build_s1_fixture(root: str, band_filler_count: int) -> None:
    write_core(root)
    write_filler(root, band_filler_count)


def build_s2_fixture(root: str) -> None:
    """One file per (line_band, placement) combination under s2/."""
    for lines in S2_LINE_BANDS:
        for placement in S2_PLACEMENTS:
            content = build_large_file(lines, placement)
            _write(root, f"s2/large_{lines}_{placement}.py", content)
