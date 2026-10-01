"""Platform mechanism behind explicit ports (ARCH-PLATFORM-001).

Kriya core (workflow, policy, control, config) owns policy; this package
owns operating-system mechanism. Core modules ask a port what it can do
and how it did it; an adapter never grants authority. See
handover/PLATFORM_ARCHITECTURE.md.
"""
