"""PRD-027: regression tests for the retrieval defects the context-recall
certification exposed. Each test fails on the pre-fix code:

1. the code-index query sites (Graph RAG, DEV-INV search_code) passed no
   embedding dimension, so query_hybrid's 768 default silently degraded any
   non-768 model to lexical-only;
2. query_lexical phrase-matched the whole query text, so a natural goal
   never matched;
3. the dependency graph never reported a caller (calls/imports are
   file-sourced), never walked a matched file's own dependencies, never
   reached a two-hop dependency, and let duplicate rows crowd distinct files
   out of the capped result;
4. configuration and non-Maven build descriptors were never indexed.
"""
import asyncio

from kriya.analyzer.analyzer import CONFIGURATION_INDEX_EXTENSIONS, RepositoryAnalyzer
from kriya.analyzer.graph import DependencyGraph
from kriya.config import AppConfig
from kriya.memory.vector import LocalVectorStore, lexical_query_terms
from kriya.workflow.context_budget import RetrievalLimits
from kriya.workflow.context_certification import DeterministicHashingEmbedder
from kriya.workflow.graph_retrieval import retrieve_graph_context


class _FixedEmbedder:
    def __init__(self, vector):
        self.vector = vector

    async def get_embedding(self, text, is_query=False):
        return list(self.vector)


def test_graph_retrieval_queries_with_the_real_embedding_dimension(tmp_path):
    (tmp_path / "Target.java").write_text("class Target {}\n")
    (tmp_path / "Other.java").write_text("class Other {}\n")
    store = LocalVectorStore(str(tmp_path / "vector_index.db"))
    store.add_document("Target.java", "class Target {}", [1.0, 0.0, 0.0], chunk_index=0, model_name="m", dimensions=3)
    store.add_document("Other.java", "class Other {}", [0.0, 1.0, 0.0], chunk_index=0, model_name="m", dimensions=3)

    result = asyncio.run(retrieve_graph_context(
        "zzzz qqqq", str(tmp_path), embed_client=_FixedEmbedder([1.0, 0.0, 0.0]), vector_store=store,
        dependency_graph_path=None, limits=RetrievalLimits(top_k=1, max_hops=2, max_neighborhood_results=30),
        embedding_model="m", budget_limit=lambda: 4000,
    ))
    store.close()
    # No lexical overlap at all: only the (3-dimensional) vector leg can match.
    assert result.matched_files == ["Target.java"]


def test_lexical_terms_are_identifiers_and_their_parts_without_function_words():
    terms = lexical_query_terms("Cap the discount in OrderService.computeDiscount at 50 percent")
    assert "orderservice" in terms and "order" in terms and "service" in terms
    assert "computediscount" in terms and "compute" in terms and "discount" in terms
    assert "the" not in terms and "at" not in terms and "50" not in terms
    assert len(terms) == len(set(terms))


def test_query_lexical_matches_goal_terms_not_only_the_whole_phrase(tmp_path):
    store = LocalVectorStore(str(tmp_path / "vector_index.db"))
    store.add_document(
        "OrderService.java", "public BigDecimal computeDiscount(Order order)", [1.0], chunk_index=0,
        model_name="m", dimensions=1,
    )
    store.add_document("Stock.java", "public void receive(int quantity)", [1.0], chunk_index=0,
                       model_name="m", dimensions=1)
    hits = store.query_lexical("Please cap what computeDiscount returns", top_k=5)
    store.close()
    assert [hit["filepath"] for hit in hits] == ["OrderService.java"]


def test_query_lexical_fallback_table_uses_terms_too(tmp_path):
    store = LocalVectorStore(str(tmp_path / "vector_index.db"))
    # The fallback table only exists where SQLite lacks FTS5; build it here.
    store.use_fts = False
    store.conn.execute(
        "CREATE TABLE IF NOT EXISTS fts_chunks_fallback (filepath TEXT, chunk_index INTEGER, text TEXT, "
        "split_text TEXT, PRIMARY KEY (filepath, chunk_index))"
    )
    store.add_document("OrderService.java", "BigDecimal computeDiscount(Order order)", [1.0], chunk_index=0,
                       model_name="m", dimensions=1)
    hits = store.query_lexical("cap what computeDiscount returns", top_k=5)
    store.close()
    assert [hit["filepath"] for hit in hits] == ["OrderService.java"]


def _python_graph(tmp_path):
    graph = DependencyGraph(str(tmp_path / "dependency_graph.db"))
    files = {
        "svc.py": "from helper import helper\n\nclass Service:\n    def charge(self, x):\n        return helper(x)\n",
        "caller.py": "def run(service):\n    return service.charge(1)\n",
        "helper.py": "from deep import deep\n\ndef helper(x):\n    return deep(x)\n",
        "deep.py": "def deep(x):\n    return x\n",
        "unrelated.py": "def noise():\n    return 0\n",
    }
    for path, content in files.items():
        graph.index_file(path, content, mtime=0.0)
    return graph


def test_graph_reports_the_calling_file_as_a_neighbor(tmp_path):
    graph = _python_graph(tmp_path)
    files = {hit["filepath"] for hit in graph.get_neighborhood(["charge"], max_hops=2)}
    graph.close()
    assert "caller.py" in files


def test_graph_reaches_one_and_two_hop_dependencies_from_a_file_seed(tmp_path):
    graph = _python_graph(tmp_path)
    hits = graph.get_neighborhood(["Service", "charge", "svc.py"], max_hops=2)
    graph.close()
    files = {hit["filepath"] for hit in hits}
    assert {"helper.py", "deep.py"} <= files
    assert "unrelated.py" not in files


def test_graph_neighborhood_is_one_entry_per_file_before_the_cap(tmp_path):
    graph = _python_graph(tmp_path)
    hits = graph.get_neighborhood(["Service", "charge", "svc.py"], max_hops=2, max_results=30)
    graph.close()
    paths = [hit["filepath"] for hit in hits]
    assert len(paths) == len(set(paths))


def test_configuration_and_build_descriptors_are_indexed(tmp_path):
    assert {".properties", ".yaml", ".yml", ".toml", ".gradle"} <= CONFIGURATION_INDEX_EXTENSIONS
    assert ".json" not in CONFIGURATION_INDEX_EXTENSIONS
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "application.properties").write_text("order.discount.max-percent=50\n")
    (repo / "settings.yaml").write_text("payments:\n  max_charge_retries: 3\n")
    (repo / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (repo / "package-lock.json").write_text("{}\n")
    cfg = AppConfig()
    cfg.paths.memory = str(tmp_path / "memory")
    cfg.paths.skills = str(tmp_path / "skills")
    (tmp_path / "memory").mkdir()
    asyncio.run(RepositoryAnalyzer(str(repo)).index_repository(
        cfg, force=True, embedding_client=DeterministicHashingEmbedder(), generate_conventions_skill=False,
    ))
    store = LocalVectorStore(str(tmp_path / "memory" / "vector_index.db"))
    indexed = set(store.file_metadata.keys())
    store.close()
    assert {"application.properties", "settings.yaml", "pyproject.toml"} <= indexed
    assert "package-lock.json" not in indexed
