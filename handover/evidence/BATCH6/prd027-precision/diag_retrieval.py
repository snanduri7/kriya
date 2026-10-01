"""PRD-027 precision diagnosis: replay the certification's exact retrieval
(real embedder) and record every stage. Read-only; nothing in kriya/ changes."""
import asyncio
import json
import os
import sys
import tempfile

from kriya.analyzer.graph import DependencyGraph
from kriya.config.config import AppConfig
from kriya.memory.vector import LocalVectorStore, OllamaEmbeddingClient, lexical_query_terms
from kriya.workflow import context_certification as cc
from kriya.workflow.context_recall_fixtures import REPOSITORIES
from kriya.workflow.graph_retrieval import retrieve_graph_context

BASE = "http://localhost:11434/v1"
MODEL = "nomic-embed-text:latest"


def nb_detail(graph, seeds, limits):
    return graph.get_neighborhood(seeds, max_hops=limits.max_hops, max_results=limits.max_neighborhood_results)


async def main(out_path):
    cfg = AppConfig()
    cfg.embedding.model = MODEL
    cfg.embedding.base_url = BASE
    client = OllamaEmbeddingClient(base_url=BASE, model=MODEL, egress_policy=cfg.autonomy.egress_policy)
    limits = cc.DEFAULT_RETRIEVAL_LIMITS
    report = []
    with tempfile.TemporaryDirectory(prefix="diag-cert-") as scratch:
        for repo in REPOSITORIES:
            root = os.path.join(scratch, repo.name)
            rcfg = await cc._index_repository(repo, root, cfg, client)
            store = LocalVectorStore(os.path.join(rcfg.paths.memory, "vector_index.db"))
            gpath = os.path.join(rcfg.paths.memory, "dependency_graph.db")
            graph = DependencyGraph(gpath)
            all_files = sorted(p for p, _ in repo.files)
            for case in repo.cases:
                relevant_golden = {g.path for g in case.golden}
                acceptable = set(case.acceptable)
                q = await client.get_embedding(case.goal, is_query=True)
                vec = store.query(q, top_k=limits.top_k * 4, model_name=MODEL, dimensions=len(q))
                lex = store.query_lexical(case.goal, top_k=limits.top_k * 4)
                vrank = {(r["filepath"], r["chunk_index"]): (i, round(r["score"], 4)) for i, r in enumerate(vec, 1)}
                lrank = {(r["filepath"], r["chunk_index"]): i for i, r in enumerate(lex, 1)}
                hybrid = store.query_hybrid(case.goal, q, top_k=limits.top_k, model_name=MODEL, dimensions=len(q))
                hyb_rows = []
                for i, m in enumerate(hybrid, 1):
                    key = (m["filepath"], m["chunk_index"])
                    hyb_rows.append({
                        "rank": i, "file": m["filepath"], "chunk": m["chunk_index"], "rrf": round(m["score"], 5),
                        "vector_rank_cos": vrank.get(key), "lexical_rank": lrank.get(key),
                        "header": m.get("text", "").splitlines()[0][:90] if m.get("text") else "",
                    })
                matched = list(dict.fromkeys(m["filepath"] for m in hybrid))
                seeds_by_file = {}
                for f in matched:
                    syms = graph.get_symbols_for_file(f)
                    seeds_by_file[f] = (syms or [os.path.splitext(os.path.basename(f))[0]]) + [f]
                all_seeds = [s for f in matched for s in seeds_by_file[f]]
                combined = nb_detail(graph, all_seeds, limits)
                per_seed_file = {f: sorted({n["filepath"] for n in nb_detail(graph, seeds_by_file[f], limits)}) for f in matched}
                res = await retrieve_graph_context(
                    case.goal, root, embed_client=client, vector_store=store, dependency_graph_path=gpath,
                    limits=limits, embedding_model=MODEL, budget_limit=lambda: cc.CERTIFICATION_GRAPH_BUDGET_TOKENS,
                )
                pkg = res.context_package
                shown = [(it.path, it.tier) for it in pkg.relevant_files] if pkg else []
                report.append({
                    "repository": repo.name, "case": case.name, "goal": case.goal,
                    "repo_files": all_files,
                    "golden": sorted(relevant_golden), "acceptable": sorted(acceptable),
                    "lexical_terms": lexical_query_terms(case.goal),
                    "vector_top20": [[r["filepath"], r["chunk_index"], round(r["score"], 4)] for r in vec],
                    "lexical_top20": [[r["filepath"], r["chunk_index"]] for r in lex],
                    "hybrid_top5": hyb_rows,
                    "matched_files": matched,
                    "seeds_by_file": seeds_by_file,
                    "neighborhood_combined": [
                        {k: n[k] for k in ("filepath", "name", "relation_type", "hop", "score")} for n in combined
                    ],
                    "neighborhood_by_matched_file": per_seed_file,
                    "related_files": sorted(res.related_files),
                    "file_scores": {k: round(v, 5) for k, v in sorted(res.file_scores.items(), key=lambda kv: -kv[1])},
                    "packaged": [
                        {"path": p, "tier": t,
                         "label": "GOLDEN" if p in relevant_golden else ("ACCEPTABLE" if p in acceptable else "FALSE_POSITIVE"),
                         "entered_via": "matched" if p in matched else "graph"}
                        for p, t in shown
                    ],
                    "omitted": list(pkg.omitted) if pkg else [],
                })
            store.close()
            graph.close()
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
