"""Code Intelligence R1 slice 2, item 4: embedding chunks from the structural
model.

Measured before (loc-N, commons-lang 4ee346e5): the Java chunker matched
method declarations with the one-line regex the graph used (36 % method
coverage), so only 97 of 171 behavior-goal gold members had a chunk of their
own, and no chunk carried the member's Javadoc - the natural-language text a
behavior goal shares with the code. The Spring XML chunker stripped xmlns
declarations and failed on prefixed elements, with every bean at line 1.
"""
import textwrap

from kriya.analyzer.analyzer import chunk_file_with_metadata_headers
from kriya.code_intel.parsing import parse_text
from kriya.workflow.context_source import parse_controlled_chunk_header_name

JAVA = textwrap.dedent("""\
    package shop;

    /** Orders of one customer. */
    public class Orders {
        private int count;

        /**
         * Adds an order.
         * Rejects negative quantities.
         */
        @Override
        public <T extends Comparable<T>> Map<String, List<T>> add(int n) throws IOException {
            count += n;
            return null;
        }

        static final class Line {
            int doubled(int q) { return q * 2; }
        }
    }

    record Receipt(String id) {
        String label() { return id; }
    }
    """)


def test_every_method_of_every_type_has_its_own_chunk_with_its_javadoc():
    chunks = chunk_file_with_metadata_headers(JAVA, "src/main/java/shop/Orders.java")
    methods = {parse_controlled_chunk_header_name(c["text"]): c for c in chunks if "Method:" in c["text"]}
    expected = {s.name for s in parse_text("Orders.java", JAVA).symbols if s.kind in ("method", "constructor")}
    assert set(methods) == expected == {"add", "doubled", "label"}
    add = methods["add"]
    assert (add["start"], add["end"]) == (7, 15)  # from the Javadoc to the closing brace
    assert "Rejects negative quantities." in add["text"] and "count += n;" in add["text"]
    assert "Class: Line\n" in methods["doubled"]["text"] and "Class: Receipt\n" in methods["label"]["text"]
    types = [c for c in chunks if "=== Class Declaration ===" in c["text"]]
    assert {parse_controlled_chunk_header_name(c["text"]) for c in types} == {"Orders", "Line", "Receipt"}
    assert "private int count;" in types[0]["text"] and "Orders of one customer." in types[0]["text"]


def test_spring_xml_chunks_are_namespace_aware_with_real_spans():
    xml = textwrap.dedent("""\
        <?xml version="1.0" encoding="UTF-8"?>
        <beans xmlns="http://www.springframework.org/schema/beans"
               xmlns:context="http://www.springframework.org/schema/context"
               xmlns:p="http://www.springframework.org/schema/p">
            <context:component-scan base-package="shop"/>
            <bean id="orders" class="shop.Orders" p:clock-ref="clock">
                <property name="limit" value="5"/>
            </bean>
        </beans>
        """)
    chunks = chunk_file_with_metadata_headers(xml, "beans.xml")
    bean = next(c for c in chunks if "Spring Bean: orders" in c["text"])
    assert (bean["start"], bean["end"]) == (6, 8) and 'p:clock-ref="clock"' in bean["text"]
    assert any("component-scan" in c["text"] for c in chunks)


def test_an_unparseable_java_file_falls_back_to_generic_chunks():
    chunks = chunk_file_with_metadata_headers("not java at all {{{", "Broken.java")
    assert chunks and "=== Code Content ===" in chunks[0]["text"]
