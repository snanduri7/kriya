"""Shared value helpers (MODULE-CONTRACTS §1). Intentionally empty in slice
A1: the contract's ``sha256_json`` ("the one canonical-JSON digest") is not
added yet because the existing copies are not one function - MEASURED,
``core/model_runtime._sha256_json`` returns ``"sha256:" + hex`` over
``json.dumps(sort_keys, compact, ensure_ascii)`` while
``workflow/execution_plan._sha256`` returns a bare hex over its own
``_canonical_json``. Choosing one is a behaviour change for whichever
identity digest moves, so it belongs to the slice that consolidates them."""

__all__: tuple = ()
