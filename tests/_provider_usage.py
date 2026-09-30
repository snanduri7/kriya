"""PROVIDER-CONTRACT-001 test helper: a plausible provider prompt-token
count for a fake transport that simulates an identified runtime.

A real provider cannot evaluate fewer tokens than a prompt's bytes divided by
its tokenizer's bytes-per-token ceiling; one that reports fewer dropped input
(PROVIDER_PROMPT_TRUNCATED). About 3.5 bytes per token sits inside the
calibrated range for code and prose (evidence/provider-contract-001)."""


def plausible_prompt_tokens(*texts) -> int:
    size = sum(len((text or "").encode("utf-8")) for text in texts)
    return max(1, size * 2 // 7)


def plausible_message_tokens(messages, tools=None) -> int:
    """For a request: every message's content plus the tool schemas the
    model reads (what Kriya's dispatch count includes)."""
    import json

    texts = [str(message.get("content") or "") for message in messages or ()]
    for message in messages or ():
        texts.extend(json.dumps(call) for call in message.get("tool_calls") or ())
    if tools:
        texts.append(json.dumps(tools))
    return plausible_prompt_tokens(*texts)
