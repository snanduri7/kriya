"""SEC-003 test fixture: a disposable, real MCP stdio server (same shape as
mock_mcp_server.py) whose one tool reports whether specific environment
variable NAMES are present in ITS OWN process environment - used to prove,
via a real child process (not a mock), exactly which variables an MCP
subprocess actually receives.
"""
import json
import os
import sys


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            method = req.get("method")
            req_id = req.get("id")

            res = {}
            if method == "initialize":
                res = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "env-report-server", "version": "1.0.0"},
                    },
                }
            elif method == "tools/list":
                res = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "tools": [
                            {
                                "name": "report_env",
                                "description": "Reports whether named env vars are present in this process.",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "names": {
                                            "type": "string",
                                            "description": "comma-separated variable names to check",
                                        }
                                    },
                                    "required": ["names"],
                                },
                            }
                        ]
                    },
                }
            elif method == "tools/call":
                tool_name = req.get("params", {}).get("name")
                args = req.get("params", {}).get("arguments", {})
                if tool_name == "report_env":
                    names = [n.strip() for n in args.get("names", "").split(",") if n.strip()]
                    report = {n: os.environ.get(n) for n in names}
                    res = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {"content": [{"type": "text", "text": json.dumps(report)}]},
                    }
                else:
                    res = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32601, "message": "Method not found"},
                    }
            else:
                if req_id is not None:
                    res = {"jsonrpc": "2.0", "id": req_id, "result": {}}
                else:
                    continue

            sys.stdout.write(json.dumps(res) + "\n")
            sys.stdout.flush()
        except Exception as e:
            sys.stderr.write(f"Error in env-report server: {e}\n")
            sys.stderr.flush()


if __name__ == "__main__":
    main()
