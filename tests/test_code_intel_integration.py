"""Code Intelligence R1, Stage 7: production integration through the one
shared seam (``member_boundaries_for`` -> language adapter -> structural
model). Developer known-target member context, compiler/stack-trace failure
-> member localization, edit windows and investigation all read these
boundaries; the tests prove the shapes the regex scanner could not see."""
import textwrap

from kriya.workflow.context_source import (
    boundaries_matching_member_id,
    extract_member_body,
    member_boundaries_for,
    resolve_member_hints_from_failure_location,
)

SOURCE = textwrap.dedent("""\
    package shop;

    import java.util.List;

    public class Orders {
        private final List<String> ids;

        public Orders(List<String> ids) {
            this.ids = ids;
        }

        public int count() {
            return ids.size();
        }

        static final class Line {
            private final int qty;

            Line(int qty) {
                this.qty = qty;
            }

            int doubled() {
                return qty * 2;
            }
        }
    }

    record Receipt(String id, long cents) {
        Receipt {
            if (cents < 0) {
                throw new IllegalArgumentException("negative");
            }
        }

        String label() { return id + cents; }
    }
    """)


def test_boundaries_cover_nested_types_second_top_level_types_and_compact_constructors():
    ids = [(b.member_id, b.start_line, b.end_line) for b in member_boundaries_for("Orders.java", SOURCE)]
    assert ids == [
        ("Orders.Orders", 8, 10), ("Orders.count", 12, 14), ("Orders.Line.Line", 19, 21),
        ("Orders.Line.doubled", 23, 25), ("Receipt.Receipt", 30, 34), ("Receipt.label", 36, 36),
    ]


def test_compiler_error_inside_an_inner_class_localizes_to_that_member_with_its_exact_body():
    """Pre-R1 the boundaries covered the primary type's direct members only:
    a failure on line 24 produced no member hint and fell back to file level."""
    [hint] = resolve_member_hints_from_failure_location("src/main/java/shop/Orders.java", SOURCE, 24)
    assert (hint.member_id, hint.provenance) == ("Orders.Line.doubled", "failure_location")
    [boundary] = boundaries_matching_member_id(member_boundaries_for("Orders.java", SOURCE), hint.member_id)
    assert extract_member_body(SOURCE, boundary.start_line, boundary.end_line) == (
        "        int doubled() {\n            return qty * 2;\n        }")


def test_stack_frame_line_in_a_second_top_level_record_localizes_to_its_compact_constructor():
    [hint] = resolve_member_hints_from_failure_location("Orders.java", SOURCE, 32)
    assert hint.member_id == "Receipt.Receipt"


def test_a_line_outside_every_member_gives_no_hint_never_a_guess():
    assert resolve_member_hints_from_failure_location("Orders.java", SOURCE, 6) == []
    assert member_boundaries_for("notes.txt", "x") is None
