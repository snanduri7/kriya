# PRD-003 Live-Model Verification

## Verdict
LIVE_VERIFIED

The user ran `tests/test_live_generate_json.py` against local Ollama using:

- Base URL: `http://localhost:11434/v1`
- LLM: `qwen2.5-coder:1.5b`
- Embedding model: `all-minilm`
- Python 3.14.6, pytest 9.1.1

Result: 1 passed in 17.48 seconds. The test parsed stdout as one JSON object,
confirmed a completed workflow and recorded LLM call, checked the exit-code
contract, and wrote runtime/config/stdout/stderr evidence beneath:

`handover/evidence/PRD-003/user-live/test_real_generate_emits_one_j0/evidence`

The exact provider version and model artifact digests are stored in that
runtime evidence rather than inferred from model names.
