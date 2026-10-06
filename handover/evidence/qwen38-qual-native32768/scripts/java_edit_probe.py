"""Evidence-only Java edit probe (qwen3.8 NATIVE-32768 qualification follow-up; NOT a /8 case, never changes the
/8 verdict). Mirrors model_qualification.case_anchored_edit_protocol exactly - Kriya's own LLMClient built from
qualification_config with the Developer's inference settings, the case's own budget (_case_budget for
anchored_edit_protocol), the configured structured response protocol with whole-file output disallowed, Kriya's
parser and its one edit engine on LF and CRLF copies - with a generic Java fixture instead of the Python one.
Model output is never executed: the result is checked structurally with Kriya's tree-sitter Java parser.

usage (from the approved workspace, env-qwen38.sh sourced):
    python java_edit_probe.py <config.yaml> <model> <out.json>
"""
import asyncio
import json
import os
import sys
import tempfile

from kriya.agents.response_protocol import (
    STRUCTURED,
    developer_response_protocol,
    parse_structured,
    structured_contract,
)
from kriya.code_intel.model import ParseState
from kriya.code_intel.parsing import parse_text
from kriya.config.config import load_config
from kriya.core.inference_settings import role_inference_settings
from kriya.core.llm import LLMClient
from kriya.core.model_qualification import (
    _case_budget,
    _completion_evidence,
    qualification_config,
    qualification_policy_of,
)
from kriya.workflow.file_integrity import FileIntegrityError, load_snapshot, mutate_snapshot, newline_style

JAVA_PATH = "src/main/java/demo/Calc.java"
JAVA_SOURCE = (
    "package demo;\n"
    "\n"
    "public class Calc {\n"
    "    public static int total(int[] prices) {\n"
    "        int result = 0;\n"
    "        for (int p : prices) {\n"
    "            result += p;\n"
    "        }\n"
    "        return result;\n"
    "    }\n"
    "}\n"
)


async def probe(config_path: str, model: str) -> dict:
    config = load_config(config_path)
    settings = role_inference_settings(config, "developer", model)
    config = qualification_config(config, model, None, settings=settings)
    llm = LLMClient(config)
    ctx = {"policy": qualification_policy_of(config), "reasoning": bool(settings.reasoning)}
    protocol = developer_response_protocol(config)
    if protocol != STRUCTURED:
        raise SystemExit(f"probe mirrors the structured protocol only; configured: {protocol}")
    task = f"=== {JAVA_PATH} ===\n{JAVA_SOURCE}\nTask: total() must ignore negative prices.\n"
    system = ("You are a senior software engineer.\n"
              + structured_contract(JAVA_PATH, analysis_required=True, allow_edit=True, allow_file=False,
                                    allow_no_change=False))
    prompt = (task + "Write one sentence of analysis, then an EDIT block whose SEARCH copies the exact lines "
              "that change - only those lines plus the minimum context, not the whole file.")
    budget = _case_budget(ctx, "anchored_edit_protocol")
    result = await llm.complete_result(system, prompt, model_override=model, max_tokens_override=budget)
    raw = result.content or ""
    parsed = parse_structured(raw, JAVA_PATH, file_allowed=False)
    edits = parsed.edit_dicts() if parsed.kind == "edits" else None
    applied, conventions, notes = None, [], []
    if edits:
        with tempfile.TemporaryDirectory() as scratch:
            try:
                for name, data in (("lf.java", JAVA_SOURCE.encode()),
                                   ("crlf.java", JAVA_SOURCE.replace("\n", "\r\n").encode())):
                    path = os.path.join(scratch, name)
                    with open(path, "wb") as handle:
                        handle.write(data)
                    text, new_bytes = mutate_snapshot(load_snapshot(path), edits)
                    applied = applied or text
                    conventions.append(newline_style(new_bytes) == newline_style(data))
            except FileIntegrityError as error:
                applied = None
                notes.append(str(error))
    parses = defines_total = changed = False
    if applied:
        structure = parse_text(JAVA_PATH, applied)
        parses = structure.state == ParseState.PARSED
        defines_total = any(symbol.name == "total" for symbol in structure.symbols)
        changed = applied != JAVA_SOURCE
    convention_kept = len(conventions) == 2 and all(conventions)
    ok = (result.status.value == "OK" and bool(parsed.analysis) and bool(edits) and parses and defines_total
          and changed and convention_kept)
    await llm.aclose()
    # The completion evidence carries its own "status" (the call's status); the probe verdict is
    # "probe_status" and is merged last so nothing can overwrite it.
    return {**_completion_evidence(result), "probe": "java_edit_probe", "affects_8_verdict": False,
            "probe_status": "PASS" if ok else "FAIL",
            "budget": budget, "analysis": bool(parsed.analysis), "edit_count": len(edits or []),
            "applied": applied is not None, "applied_parses": parses, "defines_total": defines_total,
            "changed": changed, "convention_kept": convention_kept, "protocol_reason_code": parsed.reason_code,
            "notes": notes, "applied_text": applied, "raw_response": raw}


def main(config_path: str, model: str, out: str) -> None:
    record = asyncio.run(probe(config_path, model))
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=1, default=str)
    print(json.dumps({k: v for k, v in record.items() if k not in ("applied_text", "raw_response")}, default=str))


if __name__ == "__main__":
    main(*sys.argv[1:4])
