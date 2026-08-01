"""LLM-based in-scope/out-of-scope safety gate for the Version B routing spike.

Narrow, single-purpose classification: does this input correspond to one of
Kriya's supported actions (generate/ask/fix/review/analyze/skills), or is it
out of scope (destructive, unrelated, or otherwise not something Kriya's
existing commands do)? This exists because the embeddings classifier (see
classify.py) proved structurally unable to separate "in scope" from
"topically similar but not actually in scope" across two different embedding
models and two aggregation strategies - unroutable detection specifically
needs a deliberate check, not raw similarity. This gate does NOT decide which
command to run, only whether to route at all.
"""
import json

from kriya.core.llm import LLMClient

_SYSTEM_PROMPT = """You are a strict scope classifier for a coding assistant CLI called Kriya.
Kriya supports exactly these actions, and nothing else:
- generate: implement/add/build/write new code, endpoints, tests, or config
  files that live IN the repo (this includes CI workflow YAML, Dockerfiles,
  and similar files - writing a file is always in scope, no matter what
  that file configures)
- ask: answer a question about how the existing repo/code works
- fix: repair a specific reported error, bug, or failing test
- review: review already-written code/diff/commit for issues
- analyze: summarize/describe the structure or tech stack of the repo
- skills: list, show, or check the status of domain knowledge Kriya has
  stored about a technology (this includes questions like "does Kriya know
  about X", "what's inside skill Y", or "is skill Y verified/unverified" -
  these are skills requests, not general questions)

Kriya does NOT execute commands, install packages, manage git branches or
commits, or operate any live system - it only writes/edits files inside a
reviewable, human-approved change. Answer "no" for anything that requires
actually RUNNING a command or acting on a real system, even if it sounds
like a reasonable developer request: installing/upgrading a package, running
tests, deploying, provisioning or restarting infrastructure, scaling
replicas, granting access, or any git operation (branch/commit/merge/rollback
- Kriya's own commit/approval steps handle that separately, a user should
never ask for git actions directly). Also answer "no" for anything
destructive, unrelated to software engineering on this repo, or that
bypasses a safety/approval step. Do not explain, just classify.

Examples:
Input: "show me what's inside the ignite skill" -> {"in_scope": true}
Input: "are there any unverified skills right now" -> {"in_scope": true}
Input: "does kriya have anything on redis" -> {"in_scope": true}
Input: "add retry logic to the http client" -> {"in_scope": true}
Input: "why is this test flaky" -> {"in_scope": true}
Input: "set up a github actions workflow that runs tests on push" -> {"in_scope": true}
Input: "write a Dockerfile for this service" -> {"in_scope": true}
Input: "delete all my files" -> {"in_scope": false}
Input: "what's the weather like today" -> {"in_scope": false}
Input: "install express and add it to package.json" -> {"in_scope": false}
Input: "deploy this to production" -> {"in_scope": false}
Input: "commit these changes for me" -> {"in_scope": false}
Input: "restart the production server" -> {"in_scope": false}

Respond with JSON only: {"in_scope": true} or {"in_scope": false}
"""

_USER_TEMPLATE = "Input: {text}"


async def is_in_scope(llm_client: LLMClient, text: str, model_override: str = None) -> bool:
    """Fails closed: any unparseable or errored gate response is treated as
    out of scope rather than silently letting an unclassifiable input
    through to an actionable command."""
    try:
        response = await llm_client.complete(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=_USER_TEMPLATE.format(text=text),
            json_mode=True,
            model_override=model_override,
            temperature_override=0.0,
        )
        data = json.loads(response)
        return bool(data.get("in_scope", False))
    except Exception:
        return False
