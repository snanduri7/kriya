"""PRD027-PRECISION-001: estimate candidate rules from recorded retrieval data.

This is an ESTIMATE. It unions independent per-seed neighbourhoods; production
runs one combined BFS with a shared visited set and the 30-result cap (which
cannot bind in these 14-file repositories).

Usage: python3 simulate_rules.py diag_real_embedder.json
"""
import json
import sys

TOP_K = 5
RULES = ("current", "A_confident_seeds", "B_two_leg_only", "A+B")


def select(rule, hits):
    two_leg = [h for h in hits if h["vector_rank_cos"] and h["lexical_rank"]]
    corroborated = [h for h in two_leg if h["vector_rank_cos"][0] <= TOP_K and h["lexical_rank"] <= TOP_K]
    if rule == "current":
        return hits, hits
    if rule == "A_confident_seeds":
        return hits, corroborated or hits
    matched = two_leg or hits
    if rule == "B_two_leg_only":
        return matched, matched
    return matched, corroborated or matched


def simulate(cases, rule):
    relevant_total = packaged_total = 0
    recall_ok = True
    rows = []
    for case in cases:
        matched, seeds = select(rule, case["hybrid_top5"])
        packaged = {h["file"] for h in matched}
        for seed_file in {h["file"] for h in seeds}:
            packaged |= set(case["neighborhood_by_matched_file"][seed_file])
        relevant = set(case["golden"]) | set(case["acceptable"])
        missing = sorted(set(case["golden"]) - packaged)
        recall_ok = recall_ok and not missing
        hits = sum(1 for path in packaged if path in relevant)
        relevant_total += hits
        packaged_total += len(packaged)
        rows.append(f"{case['case']}: {hits}/{len(packaged)} miss={missing}")
    print(rule, f"{relevant_total}/{packaged_total}={relevant_total / packaged_total:.4f}", "recall_ok", recall_ok)
    for row in rows:
        print("   ", row)


def main():
    with open(sys.argv[1], encoding="utf-8") as stream:
        cases = json.load(stream)
    for rule in RULES:
        simulate(cases, rule)


if __name__ == "__main__":
    main()
