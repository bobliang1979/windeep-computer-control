#!/usr/bin/env python3
"""
winctl_mcp_adapter.py — stdio MCP adapter for winctl.

Bridges custom REST API (winctl_mcp_server.py) to standard MCP JSON-RPC
over stdio, so Hermes can register it as: hermes mcp add winctl --command ... 

Usage:
    hermes mcp add winctl --command "python C:/Users/10074/Desktop/_Projects/电脑控制/windeep/winctl_mcp_adapter.py"
"""
import json
import sys
import urllib.request
import urllib.parse
import uuid

WINCTL_URL = "http://127.0.0.1:59322"


def _rpc(method: str, params: dict = None) -> dict:
    """Call winctl REST API and return result."""
    if method == "tools/list":
        req = urllib.request.Request(f"{WINCTL_URL}/tools")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        return {"tools": data["tools"]}

    elif method == "tools/call":
        name = params["name"]
        args = params.get("arguments", {})
        body = json.dumps({"name": name, "arguments": args}).encode()
        req = urllib.request.Request(
            f"{WINCTL_URL}/tools/call",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        # Unwrap MCP content format
        content = data.get("content", [])
        text = ""
        for c in content:
            if c.get("type") == "text":
                text += c.get("text", "")
        return {
            "content": [{"type": "text", "text": text}],
            "isError": False,
        }

    elif method == "initialize":
        return {
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "winctl", "version": "0.1.0"},
        }

    elif method == "notifications/initialized":
        return {}

    elif method == "ping":
        return {}

    return {"error": f"Unknown method: {method}"}


def main():
    """Stdio MCP transport: read JSON-RPC lines from stdin, respond on stdout."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            req_id = msg.get("id")
            method = msg.get("method", "")
            params = msg.get("params", {})

            # MCP notifications have no id — never respond to them.
            # The MCP client crashes on {"id": null} responses.
            if req_id is None:
                continue

            result = _rpc(method, params)

            response = {"jsonrpc": "2.0", "id": req_id}
            if "error" in result:
                response["error"] = {"code": -32603, "message": result["error"]}
            else:
                response["result"] = result

            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()

        except json.JSONDecodeError:
            continue
        except Exception as e:
            response = {
                "jsonrpc": "2.0",
                "id": req_id if "req_id" in dir() else None,
                "error": {"code": -32603, "message": str(e)},
            }
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
