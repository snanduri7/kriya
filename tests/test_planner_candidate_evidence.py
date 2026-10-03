"""SPRING-XML-PLANNER-EVIDENCE-001: a localization candidate line shows the
structural facts Code Intelligence already holds.

Measured (Spring XML pet-types live failure, reconstructed exact Planner
request, 2026-10-03): the request ranked the cache-name property 4th and the
cache-manager bean 6th, but rendered them as bare signatures -
``<property name="cacheNames">`` without its values, ``public
Collection<Vet> findVets()`` without ``@Cacheable``. The relationship the goal
names ("the same way the vets are cached") was invisible, and neither Planner
model included the configuration file (0/3, 1/3). The same request with the
annotation names and the configuration entry's own text: 3/3 for both.
Generic: no name, file or framework is special.
"""
import textwrap

import pytest

from kriya.code_intel.service import CodeIntelligenceService
from kriya.workflow.graph_retrieval import (
    CANDIDATE_SIGNATURE_CHARS,
    LocalizationCandidate,
    candidate_signature,
    fused_localization,
    render_localization_candidates,
)

XML = "src/main/resources/spring/tools-config.xml"
JAVA = "src/main/java/shop/CatalogService.java"
PROPS = "src/main/resources/application.properties"
TOOLS = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <beans xmlns="http://www.springframework.org/schema/beans">
        <bean id="cacheManager" class="org.springframework.cache.caffeine.CaffeineCacheManager">
            <property name="cacheNames">
                <set>
                    <value>default</value>
                    <value>items</value>
                </set>
            </property>
        </bean>
    </beans>
    """)
SERVICE = textwrap.dedent("""\
    package shop;

    public class CatalogService {
        @Override
        @Cacheable(value = "items")
        public java.util.List<String> findItems() { return null; }

        public java.util.List<String> findKinds() { return null; }
    }
    """)


@pytest.fixture
def service(tmp_path):
    repo = tmp_path / "repo"
    for rel, text in ((XML, TOOLS), (JAVA, SERVICE), (PROPS, "shop.page-size=5\n")):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    svc = CodeIntelligenceService(str(repo), str(tmp_path / "dependency_graph.db"))
    svc.refresh()
    yield svc
    svc.close()


def _symbol(service, key):
    found = service.find_symbol(key) + service.find_symbol(key.split(".")[-1])
    return next(s for s in found if s.lookup_key == key)


def test_a_code_member_shows_its_annotation_names(service):
    assert candidate_signature(service, _symbol(service, "shop.CatalogService.findItems")) == \
        "@Override @Cacheable public java.util.List<String> findItems()"
    assert candidate_signature(service, _symbol(service, "shop.CatalogService.findKinds")) == \
        "public java.util.List<String> findKinds()"  # no annotations: the bare signature


def test_a_configuration_entry_shows_its_own_values(service):
    assert candidate_signature(service, _symbol(service, "cacheManager.cacheNames")) == \
        '<property name="cacheNames"><set><value>default</value><value>items</value></set></property>'
    assert candidate_signature(service, _symbol(service, "cacheManager")).startswith(
        '<bean id="cacheManager" class="org.springframework.cache.caffeine.CaffeineCacheManager">'
        '<property name="cacheNames">')


def test_the_rule_is_kind_based_not_xml_specific(service):
    """A properties key is a configuration entry too: its value is shown."""
    assert candidate_signature(service, _symbol(service, "shop.page-size")) == "shop.page-size=5"


def test_changed_bytes_never_show_stale_text(service, tmp_path):
    """The declaration span belongs to the indexed bytes; once the file
    changes, the line falls back to the indexed signature."""
    symbol = _symbol(service, "cacheManager.cacheNames")
    (tmp_path / "repo" / XML).write_text(TOOLS.replace("<value>items</value>", "<value>other</value>"))
    assert candidate_signature(service, symbol) == '<property name="cacheNames">'


def test_the_fused_candidates_carry_it_and_the_planner_map_renders_it(service):
    candidates = fused_localization(service.current_view(), "cache the kinds the same way the items are cached", [])
    by_key = {c.lookup_key: c for c in candidates}
    assert "@Cacheable" in by_key["shop.CatalogService.findItems"].signature
    assert "<value>items</value>" in by_key["cacheManager.cacheNames"].signature
    rendered = render_localization_candidates(candidates)
    assert "@Cacheable public java.util.List<String> findItems()" in rendered
    assert '<property name="cacheNames"><set><value>default</value><value>items</value></set></property>' in rendered


def test_a_long_entry_is_bounded():
    long = LocalizationCandidate("id", "a.xml", "bean", "b", "x" * 1000, 1.0, (("fts", 1.0),))
    line = render_localization_candidates([long]).splitlines()[2]
    assert "x" * CANDIDATE_SIGNATURE_CHARS in line and "x" * (CANDIDATE_SIGNATURE_CHARS + 1) not in line
    assert CANDIDATE_SIGNATURE_CHARS == 240
