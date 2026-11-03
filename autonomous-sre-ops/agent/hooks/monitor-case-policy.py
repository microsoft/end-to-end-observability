#!/usr/bin/env python3
"""Nonblocking native tool-result audit; never echoes tool arguments or results."""
import json
import re
import sys


def main():
    try:
        text = sys.stdin.read(131073)
        if len(text) > 131072:
            raise ValueError()
        context = json.loads(text)
        if not isinstance(context, dict) or context.get("hook_event_name") != "PostToolUse":
            raise ValueError()
    except (ValueError, TypeError):
        print("ASRE tool audit received an invalid or oversized context.", file=sys.stderr)
        return 1
    name = context.get("tool_name")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", name):
        name = "framework-tool"
    print("ASRE native tool-result audit: " + name, file=sys.stderr)
    print(json.dumps({"decision": "allow"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
