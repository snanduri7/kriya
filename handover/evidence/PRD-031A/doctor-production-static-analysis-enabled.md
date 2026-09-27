# `kriya doctor --production`: static analysis enabled and required (user-run, 2026-09-27 17:20)

## Command
Workspace: `kriya-live-demo/demo-03-brownfield/workspace/repo`, a Java project with 32 source files.
```
kriya --trust-file ../../config/prd031a-static-analysis.trust.json -c ../../config/generate-production-static-analysis.yaml doctor --production
```

## Config and its approval
- `generate-production-static-analysis.yaml` is `generate-production.yaml` (runtime_profile production) plus a `static_analysis` block (copy in this folder):
  - enabled, provider semgrep, requirement required;
  - version `1.178.0`;
  - image `semgrep/semgrep@sha256:32e459968daabe7ab86968184a29109b9564aa00392401156f9788452b42786b`;
  - rule pack `config/semgrep-rules/java-security.yml`, outside the workspace (copy here as `java-security.yml`; file sha256 `92c69468…aae4b0e`, Kriya pack digest `e92e25017ff249edc21c83425ff0341e64b7247701ea130ce1b72f1e87390277`, 2 Java rules).
- The user approved it with `kriya authority approve --out`, which writes a trust file outside the workspace. The approval for `generate-production.yaml` is untouched.

## Static-analysis rows

| Check | Status | Evidence |
|---|---|---|
| static_analysis.configuration | PASS | provider semgrep, requirement required |
| static_analysis.provider | PASS | semgrep 1.178.0, edition community, execution_location **container**, identity_digest d25214da29fcead4976c3ffc52477fa3a13f559dd17fb4a1e3c633cfa8f63ad0 |
| static_analysis.capability | PASS | java ga with 2 rules; limitations declared: silent syntax-error recovery, file-local analysis; rule-pack digest as above |
| static_analysis.coverage | PASS | repository_languages {java: 32}, covered [java], uncovered [] |
| static_analysis.prerequisites | PASS (`evidence.status` NOT_APPLICABLE) | none declared |
| static_analysis.waivers | PASS (`evidence.status` NOT_APPLICABLE) | no waivers recorded (store outside the workspace) |
| static_analysis.egress | PASS | local_only, admitted, network_requirement none, source_upload false, **network_enforced true** |

## Other rows
- Every other row is PASS except four WARNs. These are the same four pre-existing WARNs as in the disabled run:
  - persistence.traces: legacy database;
  - models.role_independence;
  - semantic.precision_boundary;
  - runtime.fixed_guarantees: follows from traces.
- containment.oci_smoke: PASS, with no leftover containers.

**PRODUCTION_READY=true**

The disabled counterpart, using the unchanged `generate-production.yaml`, is in `doctor-production-static-analysis-disabled.json`: PRODUCTION_READY=true, schema_version 1, and all seven static_analysis rows NOT_APPLICABLE with required=false.
