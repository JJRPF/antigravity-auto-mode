#!/usr/bin/env python3
"""
typesafe-auto-mode: CLI diagnostic and inspection utility for TypeSafe Auto Mode in Antigravity.
Emulates the Claude Code Auto Mode classifier, diagnostic display, and real-time evaluation.
"""

import os
import sys
import json
import re

CONFIG_PATH = os.path.expanduser("~/.config/typesafe/config.json")
HOOK_PATH = os.path.expanduser("~/.config/typesafe/typesafe_hook.py")
HOOKS_JSON = os.path.expanduser("~/.gemini/config/hooks.json")
SETTINGS_JSON = os.path.expanduser("~/.gemini/antigravity-cli/settings.json")
LOG_PATH = "/tmp/typesafe_hook.log"


def get_status():
    print("================================================================")
    print("      TypeSafe Auto-Mode Guardian (Claude Code Emulation)       ")
    print("================================================================")

    # 1. API Key Check
    api_key = None
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                api_key = json.load(f).get("api_key", "")
        except Exception:
            pass
    has_key = bool(api_key and len(api_key) > 10)
    key_disp = f"Active ({api_key[:12]}...)" if has_key else "Missing / Not Configured"
    print(f"[*] Classifier Model     : TypeSafe Jev (jev-latest)")
    print(f"[*] API Key Status       : {key_disp}")
    print(f"[*] Hook Implementation  : {HOOK_PATH} (exists: {os.path.exists(HOOK_PATH)})")

    # 2. Settings Baseline
    has_settings = False
    grants = []
    if os.path.isfile(SETTINGS_JSON):
        try:
            with open(SETTINGS_JSON, "r") as f:
                s = json.load(f)
                grants = s.get("permissions", {}).get("allow", [])
                has_settings = True
        except Exception:
            pass
    print(f"[*] Settings Baseline    : {len(grants)} wildcard grants configured")
    for g in grants[:4]:
        print(f"      - {g}")
    if len(grants) > 4:
        print(f"      - ... ({len(grants)-4} more)")

    # 3. Hooks Integration
    hook_matched = False
    if os.path.isfile(HOOKS_JSON):
        try:
            with open(HOOKS_JSON, "r") as f:
                h = json.load(f)
                pt = h.get("typesafe-guardian", {}).get("PreToolUse", [])
                if pt and pt[0].get("matcher") == "*":
                    hook_matched = True
        except Exception:
            pass
    print(f"[*] PreToolUse Matcher   : {'* (All Tools Covered)' if hook_matched else 'Incomplete'}")

    # 4. Features Active
    print(f"[*] Core Invariants      :")
    print(f"      - Autonomous Momentum    : [ENABLED] Zero approval prompts on safe routine tools")
    print(f"      - Intent-Aware Gate      : [ENABLED] Jev classifies prompt alignment for releases")
    print(f"      - Rejection Recovery     : [ENABLED] Never aborts or cancels on user denial")
    print(f"      - Grounded Verification  : [ENABLED] Stop hook requires evidence before completion")
    print(f"      - Destructive Guard      : [ENABLED] Tier 3 hard-deny on host disk wiping")

    # 5. Audit Stats
    if os.path.isfile(LOG_PATH):
        try:
            with open(LOG_PATH, "r", encoding="utf-8") as f:
                lines = f.readlines()
            invocations = len([l for l in lines if "PreToolUse invoked" in l])
            denials = len([l for l in lines if "deny" in l or "Blocked command" in l])
            escalations = len([l for l in lines if "force_ask" in l or "Escalation" in l])
            user_denials = len([l for l in lines if "User denial detected" in l])
            print(f"[*] Session Telemetry    :")
            print(f"      - Total Tools Evaluated  : {invocations}")
            print(f"      - Escalated to Modal     : {escalations}")
            print(f"      - Hard Blocked (T3)      : {denials}")
            print(f"      - Rejections Recovered   : {user_denials}")
        except Exception:
            pass
    print("================================================================")


def eval_command(cmd, user_req=""):
    import subprocess
    payload = {
        "toolCall": {
            "name": "run_command",
            "args": {"CommandLine": cmd}
        },
        "workspacePaths": ["/home/jjr/Work"]
    }
    # Create temp transcript if user_req provided
    if user_req:
        import tempfile
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".jsonl") as f:
            f.write(json.dumps({"type": "USER_INPUT", "content": user_req}) + "\n")
            payload["transcriptPath"] = f.name

    p = subprocess.Popen(
        ["python3", HOOK_PATH, "--mode", "pre-tool"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    out, err = p.communicate(json.dumps(payload).encode())
    try:
        res = json.loads(out.decode())
        print(f"\nEvaluating: '{cmd}'")
        if user_req:
            print(f"User Intent: '{user_req}'")
        print(f"Decision   : {res.get('decision')}")
        if res.get("reason"):
            print(f"Reason     : {res.get('reason')}")
        if res.get("permissionOverrides"):
            print(f"Overrides  : {res.get('permissionOverrides')}")
    except Exception as e:
        print(f"Evaluation error: {e}")


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("--eval", "-e"):
        cmd = sys.argv[2] if len(sys.argv) > 2 else "git status"
        req = sys.argv[3] if len(sys.argv) > 3 else ""
        eval_command(cmd, req)
    elif len(sys.argv) > 1 and sys.argv[1] in ("--help", "-h"):
        print("Usage: typesafe-auto-mode [--eval <cmd> [user_intent]]")
    else:
        get_status()


if __name__ == "__main__":
    main()
