#!/usr/bin/env python3
"""Run a command and answer its yes/no prompts from an explicit allowlist, recording every decision.

usage: answer_prompts.py --rules RULES.json --log GOVERNANCE.jsonl -- COMMAND [ARGS...]

A person answers Kriya's governed-lookup prompts at the terminal. This driver stands in for that person in the rehearsal and is deliberately NOT a bulk "yes": it answers only prompts that
match a rule in RULES.json, writes the prompt text, the answer and the rule to the governance log (the record that a governed lookup was approved), and STOPS the run on any prompt it does
not recognise (exit 3). Output is echoed unchanged. Exit status is the command's, except 3 for an unrecognised prompt.
"""
import argparse
import json
import os
import re
import select
import subprocess
import sys
import time

PROMPT_END = re.compile(r"\[(?:y/N|Y/n)\]: ?$")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rules", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a command is required after --")
    rules = json.load(open(args.rules, encoding="utf-8"))["rules"]
    log = open(args.log, "a", encoding="utf-8")
    child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
    window = ""
    unrecognised = False
    fd = child.stdout.fileno()
    while True:
        ready, _, _ = select.select([fd], [], [], 0.5)
        if not ready:
            if child.poll() is not None:
                break
            continue
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        text = chunk.decode("utf-8", "replace")
        sys.stdout.write(text)
        sys.stdout.flush()
        window = (window + text)[-4000:]
        if PROMPT_END.search(window):
            prompt = window.rstrip()
            # An allowlist rule is matched against the PROMPT LINE only (the last line), never against earlier output: a phrase printed above an unrelated prompt must not approve it.
            prompt_line = prompt.splitlines()[-1] if prompt else ""
            rule = next((r for r in rules if re.search(r["match"], prompt_line)), None)
            record = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "prompt": prompt[-700:]}
            if rule is None:
                record.update(answer=None, rule=None, result="UNRECOGNISED_PROMPT_RUN_STOPPED")
                log.write(json.dumps(record) + "\n")
                log.flush()
                unrecognised = True
                child.kill()
                break
            record.update(answer=rule["answer"], rule=rule["id"], why=rule["why"])
            log.write(json.dumps(record) + "\n")
            log.flush()
            child.stdin.write((rule["answer"] + "\n").encode())
            child.stdin.flush()
            window = ""
    code = child.wait()
    return 3 if unrecognised else code


if __name__ == "__main__":
    sys.exit(main())
