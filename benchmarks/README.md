# Runtime-path benchmark

Run the same goal from two disposable copies of the same repository snapshot,
one with `runtime_profile: legacy` and one with `runtime_profile: hardened`.
Capture the required content-free metrics in local JSON summaries, including
identical `snapshot_fingerprint` and `goal_fingerprint` values, then compare:

```bash
python benchmarks/compare_runtime_paths.py --legacy legacy.json --controller controller.json
```

The comparator refuses mismatched inputs and performs no network access.
