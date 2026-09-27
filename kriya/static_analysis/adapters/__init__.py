"""Built-in static-analysis provider adapters (PRD-031A).

The only package that names a provider. ``BUILTIN_PROVIDERS`` maps a
configured provider name to the module that registers it; the registry
imports it on first use.
"""

BUILTIN_PROVIDERS = {
    "semgrep": "kriya.static_analysis.adapters.semgrep",
}
