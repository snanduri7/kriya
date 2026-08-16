"""Fixed prompt set for the MLX / oMLX / Ollama speed comparison.

Small, deliberately varied code-gen prompts: short answer, medium function,
and a longer multi-file-shaped task, so the benchmark isn't just measuring
one prompt length.
"""

PROMPTS = [
    {
        "id": "short_fn",
        "prompt": (
            "Write a Python function `is_palindrome(s: str) -> bool` that "
            "ignores case and non-alphanumeric characters. Return only the code."
        ),
        "max_tokens": 256,
    },
    {
        "id": "medium_refactor",
        "prompt": (
            "Write a Python class `LRUCache` with `get(key)` and `put(key, value)` "
            "methods, O(1) average time complexity, fixed capacity passed to __init__. "
            "Include type hints. Return only the code."
        ),
        "max_tokens": 512,
    },
    {
        "id": "longer_task",
        "prompt": (
            "Write a Python module implementing a simple in-memory task queue: a "
            "`Task` dataclass (id, payload, priority, status), a `TaskQueue` class "
            "with `enqueue`, `dequeue` (highest priority first, FIFO within same "
            "priority), and `mark_done(task_id)`. Include docstring-free type hints "
            "and a __main__ block with a couple of usage examples. Return only the code."
        ),
        "max_tokens": 1024,
    },
]
