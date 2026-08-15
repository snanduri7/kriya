"""Fixed prompt set for the local-model speed/accuracy comparison.

Same idea as spikes/mlx_benchmark/prompts.py (short/medium/long code-gen
tasks so the benchmark isn't measuring one prompt length) plus an optional
`functional_test` field: a small, unambiguous assertion snippet run against
the model's extracted code to get a real correctness signal, not just
"did it produce syntactically valid Python."

Only `short_fn` gets a functional_test - its spec (ignore case/non-alnum,
return bool) is fully unambiguous. `medium_refactor` and `longer_task` are
intentionally left without one: their prompts don't pin down edge-case
behavior (e.g. what LRUCache.get() returns on a missing key), so a strict
assertion there would fail on reasonable-but-different interpretations
rather than on actual model mistakes - that would be measuring prompt
ambiguity, not model quality. Those two only get the syntax check.
"""

PROMPTS = [
    {
        "id": "short_fn",
        "prompt": (
            "Write a Python function `is_palindrome(s: str) -> bool` that "
            "ignores case and non-alphanumeric characters. Return only the code."
        ),
        "max_tokens": 256,
        "functional_test": (
            "assert is_palindrome('A man a plan a canal Panama') is True\n"
            "assert is_palindrome('hello') is False\n"
            "assert is_palindrome('') is True\n"
            "assert is_palindrome('Was it a car or a cat I saw?') is True\n"
            "print('FUNCTIONAL_TEST_PASS')\n"
        ),
    },
    {
        "id": "medium_refactor",
        "prompt": (
            "Write a Python class `LRUCache` with `get(key)` and `put(key, value)` "
            "methods, O(1) average time complexity, fixed capacity passed to __init__. "
            "Include type hints. Return only the code."
        ),
        "max_tokens": 512,
        "functional_test": None,
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
        "functional_test": None,
    },
]
