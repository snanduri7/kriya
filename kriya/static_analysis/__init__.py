"""PRD-031A: the pluggable static-analysis gate.

Provider-neutral. The workflow and the controller use only ``service`` and
``model``; a provider (``adapters/``) is reached only through the
``StaticAnalysisPort`` returned by ``registry.create_provider``. Nothing
above the adapters names a provider, parses a provider's output, or maps
its severities. Policy, coverage, scope and waivers are Kriya's own
(handover/PRD-031A_TASK.md).
"""
