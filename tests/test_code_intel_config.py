"""E-08: structural configuration (Spring XML, application properties/YAML).

Before: ``DependencyGraph._parse_xml`` stripped every ``xmlns`` declaration and
then parsed with ElementTree, so any document using a prefixed element
(``<context:component-scan>``, ``<tx:annotation-driven/>``, ``p:``
attributes - every real Spring XML file) failed with "unbound prefix" and fell
back to a one-line regex that saw only ``<bean id=.. class=..>`` written on a
single line, at line 1 spans. Measured on the pinned spring-framework-petclinic
(09351b3e): business-config.xml and mvc-view-config.xml both failed.
"""
import os
import subprocess
import textwrap

import pytest

from kriya.analyzer.graph import DependencyGraph
from kriya.code_intel.config_parsing import config_language_for_path, parse_config_file
from kriya.code_intel.model import ParseState
from kriya.code_intel.service import CodeIntelligenceService

BUSINESS_XML = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <beans xmlns:p="http://www.springframework.org/schema/p"
           xmlns:c="http://www.springframework.org/schema/c"
           xmlns:context="http://www.springframework.org/schema/context"
           xmlns:tx="http://www.springframework.org/schema/tx"
           xmlns="http://www.springframework.org/schema/beans">
        <import resource="datasource-config.xml"/>
        <context:component-scan
            base-package="shop.service, shop.web"/>
        <context:property-placeholder location="classpath:spring/data-access.properties"/>
        <tx:annotation-driven/>
        <beans profile="jdbc">
            <bean id="orderRepository" class="shop.repository.JdbcOrderRepository"
                  p:dataSource-ref="dataSource" c:timeout="30">
                <property name="pageSize" value="${orders.pageSize}"/>
                <constructor-arg ref="clock"/>
            </bean>
        </beans>
        <bean id="orderService" class="shop.service.OrderService">
            <property name="repository">
                <ref bean="orderRepository"/>
            </property>
        </bean>
    </beans>
    """)

# The same document with every prefix renamed: a namespace-aware parser
# answers identically (prefixes are aliases, local names are the vocabulary).
RENAMED_PREFIXES = (BUSINESS_XML.replace('xmlns="http://www.springframework.org/schema/beans"',
                                         'xmlns:b="http://www.springframework.org/schema/beans"')
                    .replace("<beans", "<b:beans").replace("</beans>", "</b:beans>")
                    .replace("<bean ", "<b:bean ").replace("</bean>", "</b:bean>")
                    .replace("<property", "<b:property").replace("</property>", "</b:property>")
                    .replace("<constructor-arg", "<b:constructor-arg").replace("<import", "<b:import")
                    .replace("<ref ", "<b:ref ").replace("xmlns:p=", "xmlns:prop=").replace("p:dataSource-ref",
                                                                                          "prop:dataSource-ref"))


def _facts(structure):
    return {(s.kind, s.lookup_key, s.return_type, s.implements, s.modifiers) for s in structure.symbols}


def test_spring_xml_is_parsed_namespace_aware_with_exact_spans():
    data = BUSINESS_XML.encode()
    structure = parse_config_file("src/main/resources/spring/business-config.xml", data)
    assert structure.state is ParseState.PARSED and structure.language == "spring-xml"
    by_key = {s.lookup_key: s for s in structure.symbols}
    repo = by_key["orderRepository"]
    assert (repo.kind, repo.return_type, repo.modifiers) == ("bean", "shop.repository.JdbcOrderRepository",
                                                            ("profile:jdbc",))
    assert set(repo.implements) == {"dataSource", "clock"}
    assert (repo.declaration.start_line, repo.declaration.end_line) == (13, 17)
    text = data[repo.declaration.start_byte:repo.declaration.end_byte].decode()
    assert text.startswith('<bean id="orderRepository"') and text.endswith("</bean>")
    assert by_key["orderRepository.dataSource"].return_type == "dataSource"
    assert by_key["orderRepository.timeout"].kind == "bean_constructor_arg"
    assert by_key["orderRepository.pageSize"].return_type == "${orders.pageSize}"
    assert by_key["orderRepository.0"].return_type == "clock"
    assert by_key["orderService"].implements == ("orderRepository",)
    assert {s.lookup_key for s in structure.symbols if s.kind == "component_scan"} == {"shop.service", "shop.web"}
    assert {s.lookup_key for s in structure.symbols if s.kind == "config_import"} == {
        "datasource-config.xml", "classpath:spring/data-access.properties"}
    scan = by_key["shop.web"]
    assert (scan.declaration.start_line, scan.declaration.end_line) == (8, 9)


def test_prefix_choice_never_changes_the_structure():
    original = parse_config_file("a.xml", BUSINESS_XML.encode())
    renamed = parse_config_file("a.xml", RENAMED_PREFIXES.encode())
    assert renamed.state is ParseState.PARSED
    assert _facts(renamed) == _facts(original)


@pytest.mark.parametrize("xml, state", [
    ('<?xml version="1.0"?><project><modelVersion>4.0.0</modelVersion></project>', ParseState.UNSUPPORTED),
    ("<beans><bean id='x' class='A'>", ParseState.PARSE_FAILED),
])
def test_non_spring_and_malformed_xml_are_typed_states_never_guessed_beans(xml, state):
    structure = parse_config_file("pom.xml", xml.encode())
    assert structure.state is state and structure.symbols == ()


def test_properties_keys_with_continuations_comments_and_profiles():
    text = "# comment\n! bang\nserver.port=8080\nspring.datasource.url: jdbc:h2:mem\\\n    :test\nplain value\n"
    data = text.encode()
    structure = parse_config_file("src/main/resources/application-dev.properties", data)
    keys = {s.lookup_key: s for s in structure.symbols}
    assert list(keys) == ["server.port", "spring.datasource.url", "plain"]
    url = keys["spring.datasource.url"]
    assert (url.declaration.start_line, url.declaration.end_line, url.modifiers) == (4, 5, ("profile:dev",))
    assert data[url.declaration.start_byte:url.declaration.end_byte].decode() == (
        "spring.datasource.url: jdbc:h2:mem\\\n    :test")


def test_yaml_keys_flatten_with_spans_and_document_profiles():
    text = textwrap.dedent("""\
        spring:
          datasource:
            url: jdbc:h2:mem:db
          profiles:
            group: [a, b]
        servers:
          - host: one
        ---
        spring:
          config:
            activate:
              on-profile: prod
        server:
          port: 9090
        """)
    data = text.encode()
    structure = parse_config_file("config/application.yml", data)
    assert structure.state is ParseState.PARSED
    keys = {(s.lookup_key, s.modifiers): s for s in structure.symbols}
    url = keys[("spring.datasource.url", ())]
    assert url.declaration.start_line == 3
    assert data[url.declaration.start_byte:url.declaration.end_byte].decode() == "url: jdbc:h2:mem:db"
    assert ("servers[0].host", ()) in keys and ("spring.profiles.group", ()) in keys
    assert keys[("server.port", ("profile:prod",))].declaration.start_line == 14


def test_which_files_are_configuration():
    assert config_language_for_path("src/main/resources/application.yaml") == "yaml"
    assert config_language_for_path("bootstrap-cloud.properties") == "properties"
    assert config_language_for_path("src/main/resources/spring/data-access.properties") == "properties"
    assert config_language_for_path("src/main/resources/messages/messages_de.properties") is None
    assert config_language_for_path("docs/notes.properties") is None
    assert config_language_for_path("any/beans.xml") == "spring-xml"


def test_graph_spring_beans_have_real_lines_and_every_reference(tmp_path):
    graph = DependencyGraph(str(tmp_path / "g.db"))
    graph.index_file("business-config.xml", BUSINESS_XML, 1.0)
    symbols = {(r[0], r[1], r[2]) for r in graph.conn.execute(
        "SELECT name, start_line, end_line FROM symbols WHERE filepath = 'business-config.xml'")}
    assert symbols == {("orderRepository", 13, 17), ("orderService", 19, 23)}
    relations = {(r[0], r[1], r[2]) for r in graph.conn.execute("SELECT source, target, type FROM relations")}
    assert relations == {
        ("orderRepository", "shop.repository.JdbcOrderRepository", "declares_bean"),
        ("orderRepository", "dataSource", "references_bean"),
        ("orderRepository", "clock", "references_bean"),
        ("orderService", "shop.service.OrderService", "declares_bean"),
        ("orderService", "orderRepository", "references_bean"),
    }
    graph.close()


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_config_entries_are_retrieval_candidates_and_exact_key_lookups(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src/main/resources/spring").mkdir(parents=True)
    (repo / "src/main/java/shop").mkdir(parents=True)
    (repo / "src/main/resources/spring/business-config.xml").write_text(BUSINESS_XML)
    (repo / "src/main/resources/application.properties").write_text("orders.pageSize=25\nserver.port=8080\n")
    (repo / "src/main/java/shop/OrderService.java").write_text("package shop;\npublic class OrderService {}\n")
    _git(repo, "init", "-q")
    service = CodeIntelligenceService(str(repo), str(tmp_path / "ci.db"))
    service.refresh()
    [key] = service.find_symbol("orders.pageSize")
    assert (key.kind, key.path, key.declaration.start_line) == ("config_key", "src/main/resources/application.properties",
                                                                  1)
    hits = service.locate("the orderRepository bean should use a page size from orders.pageSize")
    top = {(h.lookup_key, h.kind) for h in hits[:3]}
    assert ("orders.pageSize", "config_key") in top and ("orderRepository", "bean") in top
    assert all(h.channels for h in hits)
    service.close()


PETCLINIC = os.path.expanduser("~/kriya-bench/spring-framework-petclinic")


@pytest.mark.skipif(not os.path.isdir(PETCLINIC), reason="pinned benchmark repository not cloned")
def test_pinned_spring_framework_petclinic_xml_parses_completely():
    """Regression against the pinned Spring XML benchmark (09351b3e): every
    Spring XML document parses, and the beans the old regex fallback missed
    (multi-line declarations, p:-namespace references) are present."""
    head = subprocess.run(["git", "-C", PETCLINIC, "rev-parse", "HEAD"], capture_output=True, text=True).stdout
    if not head.startswith("09351b3e"):
        pytest.skip("benchmark repository is not at the pinned commit")
    parsed = {}
    for rel in ("business-config.xml", "datasource-config.xml", "mvc-core-config.xml", "mvc-view-config.xml",
                "tools-config.xml"):
        path = os.path.join(PETCLINIC, "src/main/resources/spring", rel)
        with open(path, "rb") as handle:
            parsed[rel] = parse_config_file(rel, handle.read())
        assert parsed[rel].state is ParseState.PARSED, rel
    beans = {s.lookup_key: s for s in parsed["business-config.xml"].symbols if s.kind == "bean"}
    assert {"entityManagerFactory", "transactionManager", "jdbcClient", "namedParameterJdbcTemplate"} <= set(beans)
    assert beans["namedParameterJdbcTemplate"].implements == ("dataSource",)
    assert beans["entityManagerFactory"].implements == ("dataSource",)
    scans = {s.lookup_key for s in parsed["business-config.xml"].symbols if s.kind == "component_scan"}
    assert "org.springframework.samples.petclinic.repository.springdatajpa" in scans


def test_whitespace_before_the_xml_declaration_keeps_offsets_of_the_real_bytes():
    data = ("\n  " + BUSINESS_XML).encode()
    structure = parse_config_file("b.xml", data)
    bean = next(s for s in structure.symbols if s.lookup_key == "orderService")
    assert bean.declaration.start_line == 20
    assert data[bean.declaration.start_byte:bean.declaration.end_byte].decode().startswith('<bean id="orderService"')


def test_t3_links_beans_scans_and_referenced_keys_to_the_target_member(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src/main/resources/spring").mkdir(parents=True)
    (repo / "src/main/java/shop/repository").mkdir(parents=True)
    (repo / "src/main/resources/spring/business-config.xml").write_text(
        BUSINESS_XML.replace("shop.service, shop.web", "shop.repository"))
    (repo / "src/main/resources/application.properties").write_text(
        "orders.pageSize=25\norders.retention=30d\nserver.port=8080\n")
    (repo / "src/main/java/shop/repository/JdbcOrderRepository.java").write_text(textwrap.dedent("""\
        package shop.repository;

        @ConfigurationProperties(prefix = "orders")
        public class JdbcOrderRepository {
            @Value("${server.port}")
            private int port;

            int pageSize() {
                return Integer.parseInt(System.getProperty("x", "${orders.pageSize}"));
            }
        }
        """))
    _git(repo, "init", "-q")
    service = CodeIntelligenceService(str(repo), str(tmp_path / "ci.db"))
    service.refresh()
    [target] = service.find_symbol("JdbcOrderRepository.pageSize")
    package = service.build_context(target.symbol_id)
    linked = [symbol_id.split("#", 1)[1] for symbol_id, _ in package.config]
    assert linked == ["orderRepository", "shop.repository", "orders.pageSize", "server.port", "orders.retention"]
    rendered = package.render()
    assert "#### Linked configuration (read-only)" in rendered
    assert "src/main/resources/application.properties:1  orders.pageSize=25" in rendered
    # T0 stays last and authoritative; configuration is read-only context.
    assert rendered.rindex("#### Target member (authoritative source)") > rendered.index("#### Linked configuration")
    service.close()
