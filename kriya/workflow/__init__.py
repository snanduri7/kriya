"""`WorkflowEngine` is re-exported lazily (PEP 562) rather than imported
eagerly at package-init time: kriya/workflow/workflow.py imports
kriya.agents.agent, which imports kriya.agents.contracts (MA3.1) - eagerly
importing WorkflowEngine here meant ANY import of a leaf module under
kriya.workflow (e.g. kriya.workflow.triage, which contracts.py needs for
ChangeKind) forced workflow.py -> agent.py -> contracts.py to load before
contracts.py itself had finished initializing, an ImportError-causing cycle.
Deferring the import until WorkflowEngine is actually accessed keeps
`from kriya.workflow import WorkflowEngine` working identically for every
existing caller while letting leaf modules be imported independently."""

__all__ = ["WorkflowEngine"]


def __getattr__(name: str):
    if name == "WorkflowEngine":
        from kriya.workflow.workflow import WorkflowEngine
        return WorkflowEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
