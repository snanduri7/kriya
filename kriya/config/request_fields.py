"""MODEL-IDENTITY-EXTRA-BODY-OVERRIDE-001 (D-002): the request fields Kriya owns.

``extra_body`` is the provider's dialect, merged OVER the request by the SDK
(and spread over the payload by the native adapter), so a key it shares with a
field Kriya sets itself replaces that field on the wire. Measured on the
OpenAI SDK against a local HTTP server, chat and tool-call paths alike: every
name below was overridden. ``model`` alone made Kriya qualify and attribute one
model while another served the request; ``messages`` and ``tools`` would make
the recorded prompt differ from the one sent, and ``temperature`` /
``max_tokens`` would bypass the qualified settings digest and the output
budget.

The invariant: a provider extension payload may not override a Kriya-owned,
authority-bearing request field. Kriya's model selector is ``llm.model`` (or
the governed binding's model); ``extra_body`` is never a second selector.

B1 (Wave-2): a runtime may own further fields (``InferenceRuntimePort.owned_request_fields``): the native
runtime's ``format`` is where Kriya sends ``response_format`` (``"json"`` / the schema), so an ``extra_body.format`` either
replaced Kriya's value silently or, when Kriya sent none, grammar-constrained Kriya's unstructured calls from repository
configuration. It is refused through the same checker, never stripped.

Pure data and one pure function, so config validation (which cannot import
``kriya.core``) and the request boundary enforce the same set."""
from typing import Any, Mapping, Optional, Tuple

RESERVED_REQUEST_FIELDS: Tuple[str, ...] = (
    "max_tokens", "messages", "model", "response_format", "stream", "stream_options", "temperature", "tool_choice",
    "tools",
)


def reserved_extra_body_keys(extra_body: Optional[Mapping[str, Any]], runtime_owned: Tuple[str, ...] = ()) -> Tuple[str, ...]:
    """The Kriya-owned request fields ``extra_body`` tries to set (empty for
    None, a non-mapping or a clean body). ``runtime_owned`` are the fields
    Kriya owns on ONE runtime beyond the global set (B1: the native Ollama
    runtime carries structured output in ``format``, which the OpenAI-
    compatible runtime does not own)."""
    if not isinstance(extra_body, Mapping):
        return ()
    return tuple(key for key in (*RESERVED_REQUEST_FIELDS, *runtime_owned) if key in extra_body)


def reserved_extra_body_message(keys: Tuple[str, ...]) -> str:
    return (f"extra_body sets {', '.join(repr(key) for key in keys)}, a request field Kriya owns: the provider SDK "
            "would send it instead of Kriya's own value, so the request would not be the one that was qualified, "
            "budgeted and recorded. The model is selected by llm.model (or the governed model binding), never by "
            "extra_body; remove the key")
