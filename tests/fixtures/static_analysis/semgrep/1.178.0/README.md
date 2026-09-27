# Recorded Semgrep 1.178.0 outputs (PRD-031A)

Real `semgrep scan --json` stdout, captured once from **Semgrep 1.178.0** (pipx install,
macOS arm64, 2026-09-27) and used by `tests/test_prd031a_semgrep_adapter.py` to drive the
adapter's parser without a real scanner. The real tier (`tests/test_prd031a_semgrep_live.py`,
marker `live_static_analysis`) re-runs the same cases against the installed scanner.

- `manifest.json`: version, the exact fixed flags, the environment keys, per-case exit code,
  rule packs, targets and the §5.5 observation each case records, the source files the targets
  were created from (`sources`), and every rule pack's digest (`rule_pack_digests`, computed with
  the adapter's own algorithm; `rules` = `6abc9d9e5d10527a51e25d47594a984877a92d95e892cd3d2a233cd279fee591`).
- `rules/`: the valid rule pack (a directory pack: `java.yml`, `python.yml`).
- `bad/`: one invalid rule file per config-error case.
- `outputs/<case>.json`: the recorded stdout.

Invocation (cwd = the scratch repository, relative targets):

```
semgrep scan --metrics=off --disable-version-check --disable-nosem --no-git-ignore --oss-only \
  --json --verbose --timeout 5 --timeout-threshold 3 --max-target-bytes 1000000 \
  --config <pack> [--config <pack> ...] -- <targets...>
```

Environment: exactly `PATH`, `HOME` and `TMPDIR` (scratch directories), `SEMGREP_SEND_METRICS=off`,
`SEMGREP_ENABLE_VERSION_CHECK=0`. Only local rules; nothing fetched from the registry.

Neutralization: the absolute scratch directory is replaced with `__SCRATCH__` in both its
slash form (messages, spans) and Semgrep's dotted form (the `check_id` prefix, e.g.
`__SCRATCH__.rules.java-runtime-exec`); the random name of Semgrep's own temporary rule file
(created under the scratch `TMPDIR`, reported in `paths.skipped` for the invalid-pattern case) is replaced with
`__SCRATCH__/tmp/__SEMGREP_TEMP__.json`. Nothing else is edited.
