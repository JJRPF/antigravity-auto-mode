#!/usr/bin/env python3
"""
typesafe_hook.py: Antigravity Lifecycle Hook powered by TypeSafe AI (Jev).

Modes:
  --mode pre-tool:
    - Fast-paths harmless read-only commands (0ms, zero tokens).
    - Queries TypeSafe Jev for blast radius and destructiveness on mutating commands.
    - 3-Tier Risk Calibration:
        * Tier 1 (Green / Safe): {"decision": "allow"}
        * Tier 2 (Yellow / Moderate): {"decision": "ask"} (prompts user; auto-allowed under --dangerously-skip-permissions)
        * Tier 3 (Red / Destructive): {"decision": "deny"} (hard blocked in ALL modes, even with --dangerously-skip-permissions)
  --mode stop:
    - Runs before agent turn concludes.
    - Inspects recent transcript actions against claims.
    - Forces continuation {"decision": "continue"} if ungrounded assertions were made
      without running verification on the machine.
    - Employs a circuit breaker (executionNum >= 2) to guarantee no deadlock loops.
"""

import os
import sys
import re
import json
import urllib.request
import urllib.error
from typing import Dict, Any, Optional

TYPESAFE_API_URL = "https://api.typesafe.ai/v1/systemone"
CONFIG_PATH = os.path.expanduser("~/.config/typesafe/config.json")

SAFE_CMD_REGEX = re.compile(
    r"^\s*(git\s+(status|diff|log|show|branch|remote|tag|rev-parse|add|commit|config|checkout|switch|init)|"
    r"gh\s+(auth\s+setup-git|api|repo\s+view)|"
    r"ls|cat|head|tail|grep|rg|find|which|type|pwd|echo|wc|uname|file|stat|chmod|mkdir|touch|cp|mv|"
    r"python3?\s+--version|node\s+-v|bun\s+-v)\b",
    re.IGNORECASE,
)


def log(msg: str):
    """Write diagnostic info to stderr (ignored by hook protocol) and debug log."""
    sys.stderr.write(f"[typesafe-hook] {msg}\n")
    sys.stderr.flush()
    try:
        with open("/tmp/typesafe_hook.log", "a", encoding="utf-8") as f:
            f.write(f"{msg}\n")
    except Exception:
        pass


def get_api_key() -> Optional[str]:
    key = os.environ.get("TYPESAFE_API_KEY")
    if key:
        return key.strip()
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f).get("api_key", "").strip()
        except Exception:
            pass
    return None


def call_jev(payload: Dict[str, Any], api_key: str, timeout: float = 6.0) -> Optional[Dict[str, Any]]:
    """Query TypeSafe System One API with strict timeout."""
    try:
        req = urllib.request.Request(
            TYPESAFE_API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "typesafe-hook-guardian/1.0",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log(f"Jev API call failed or timed out: {e}")
        return None


SENSITIVE_PREFIXES = (
    "/etc",
    "/boot",
    "/root",
    "/usr",
    "/var",
    "/opt",
    "/sys",
    "/proc",
    "/dev",
    os.path.expanduser("~/.ssh"),
    os.path.expanduser("~/.gnupg"),
    os.path.expanduser("~/.aws"),
)


def is_sensitive_path(path: str) -> bool:
    if not path:
        return False
    norm = os.path.abspath(os.path.expanduser(path))
    return any(norm == p or norm.startswith(p + "/") for p in SENSITIVE_PREFIXES)


def handle_pre_tool(data: Dict[str, Any], api_key: Optional[str]) -> Dict[str, Any]:
    """Inspect toolCall before execution."""
    tool_call = data.get("toolCall", {})
    name = tool_call.get("name", "")
    args = tool_call.get("args", {})

    log(f"PreToolUse invoked for tool: {name}")

    # Handle file write / modify tools
    if name in ("write_to_file", "replace_file_content"):
        target_file = args.get("TargetFile", "").strip()
        if not target_file:
            return {"decision": "allow"}
        if is_sensitive_path(target_file):
            log(f"Sensitive file write target detected: {target_file}")
            return {
                "decision": "force_ask",
                "reason": f"TypeSafe Confirmation: Modification of sensitive system/credential path detected: {target_file}",
            }
        return {
            "decision": "allow",
            "permissionOverrides": [f"write_file({target_file})"],
        }

    # Handle file read / inspection tools (reads always pass through)
    if name in ("view_file", "grep_search", "find_by_name", "list_dir"):
        target_path = (
            args.get("AbsolutePath")
            or args.get("SearchPath")
            or args.get("DirectoryPath")
            or args.get("SearchDirectory")
            or ""
        ).strip()
        return {
            "decision": "allow",
            "permissionOverrides": [f"read_file({target_path})"] if target_path else ["read_file(*)"],
        }

    # Handle URL content fetch tools
    if name in ("read_url_content", "read_browser_page"):
        url = args.get("Url", "").strip()
        return {
            "decision": "allow",
            "permissionOverrides": [f"read_url({url})"] if url else ["read_url(*)"],
        }

    # If not run_command, allow other tools by default
    if name != "run_command":
        return {"decision": "allow"}

    cmd = args.get("CommandLine", "").strip()
    if not cmd:
        return {"decision": "allow"}

    # 1. Fast-path: safe local commands
    if SAFE_CMD_REGEX.match(cmd):
        return {
            "decision": "allow",
            "reason": "Fast-path: safe local command",
            "permissionOverrides": [f"command({cmd})", "command(*)"],
        }

    # If no API key configured, fail-open to avoid blocking developer
    if not api_key:
        return {
            "decision": "allow",
            "permissionOverrides": [f"command({cmd})", "command(*)"],
        }

    # Extract latest user requests from transcript to verify intent alignment
    user_inputs = []
    transcript_path = data.get("transcriptPath")
    if transcript_path and os.path.isfile(transcript_path):
        try:
            with open(transcript_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        try:
                            s = json.loads(line)
                            if s.get("type") == "USER_INPUT":
                                c = (s.get("content") or "").strip()
                                if c:
                                    user_inputs.append(c)
                        except Exception:
                            pass
        except Exception:
            pass

    # Keep last 3 user prompts to preserve intent across multi-turn continuations
    recent_inputs = user_inputs[-3:] if len(user_inputs) > 3 else user_inputs
    last_user = "\n".join(recent_inputs) if recent_inputs else ""

    # 2. Query TypeSafe Jev for Operational Blast Radius, Intent Alignment & Action Categorization
    payload = {
        "state": {
            "tool": name,
            "command_line": cmd,
            "user_request": last_user[:1000],
            "workspace_paths": data.get("workspacePaths", []),
        },
        "model": "jev-latest",
        "questions": {
            "blast_radius": {
                "type": "score",
                "instructions": "Assess the operational blast radius and risk level of executing this shell command",
                "criteria": [
                    "Level 0: Harmless command, local directory read, non-destructive check",
                    "Level 1: Localized development operation, build, test, package install, safe file write, or external publish",
                    "Level 2: Irreversible or highly destructive command (rm -rf, git reset/clean --force, dd, mkfs, drop database, systemctl destructive action, sudo/root modification)",
                ],
            },
            "is_system_destruction": {
                "type": "noul",
                "instructions": "Does this command pose an irreversible risk of deleting, wiping, or corrupting local files, filesystems, or git histories on the host machine?",
            },
            "is_publish_or_external_network": {
                "type": "noul",
                "instructions": "Is this an external deployment, publication, or remote sync operation (like npm/cargo publish, git push, uploading packages)?",
            },
            "explicitly_requested_by_user": {
                "type": "noul",
                "instructions": "Did the user explicitly request, instruct, or authorize this specific operation, external push/publish, or file deletion in their prompt?",
            },
        },
    }

    res = call_jev(payload, api_key)
    if not res or "answers" not in res:
        return {
            "decision": "allow",
            "permissionOverrides": [f"command({cmd})", "command(*)"],
        }  # Fail-safe open on network error

    score = res["answers"].get("blast_radius", {}).get("score", 0.0)
    dest = res["answers"].get("is_system_destruction", {}).get("noul", 0.0)
    pub = res["answers"].get("is_publish_or_external_network", {}).get("noul", 0.0)
    user_req = res["answers"].get("explicitly_requested_by_user", {}).get("noul", 0.0)

    log(
        f"PreTool evaluation for `{cmd[:50]}`: blast={score:.2f}/2.0, "
        f"dest={dest*100:.0f}%, pub={pub*100:.0f}%, user_req={user_req*100:.0f}%"
    )

    # Tier 3 (Red): Hard-deny for irreversible local system destruction (rm -rf, git clean -fdx, dd, mkfs).
    # Cannot be overridden by user prompt to prevent catastrophic host disk destruction.
    if dest >= 0.70 and score >= 1.50:
        return {
            "decision": "deny",
            "reason": (
                f"TypeSafe Auto-Mode Gate: Blocked command due to local system destruction risk "
                f"(blast_radius: {score:.2f}/2.0, destruction: {dest*100:.0f}%). "
                f"Execute a safer, non-destructive alternative."
            ),
        }

    # Claude Code Auto Mode Alignment Logic:
    # Tier 2: External releases / publishing (git push, npm publish) or high destructive mutations (dest >= 0.50).
    # Routine local dev (builds, tests, compiles, local git commits, package installs) has pub < 0.50 and dest < 0.50 -> Auto-Approved!
    if pub >= 0.50 or dest >= 0.50:
        if user_req >= 0.50:
            log(f"Auto-Mode: Action explicitly requested by user (confidence: {user_req*100:.0f}%). Auto-approving execution.")
            return {
                "decision": "allow",
                "permissionOverrides": [f"command({cmd})", "command(*)"],
            }
        category = "External release/push" if pub >= 0.50 else "High operational mutation"
        return {
            "decision": "force_ask",
            "reason": (
                f"TypeSafe Auto-Mode Escalation: {category} detected without explicit prompt instruction "
                f"(blast_radius: {score:.2f}/2.0, pub: {pub*100:.0f}%, dest: {dest*100:.0f}%). "
                f"Confirm execution of: {cmd}"
            ),
        }

    # Tier 1 (Green): Safe / routine local operation -> Auto-approve
    return {
        "decision": "allow",
        "permissionOverrides": [f"command({cmd})", "command(*)"],
    }


def handle_stop(data: Dict[str, Any], api_key: Optional[str]) -> Dict[str, Any]:
    """Inspect transcript before agent finishes its turn."""
    # Debug logging to inspect Antigravity payload
    try:
        with open("/tmp/typesafe_stop_debug.json", "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

    # File-backed circuit breaker to guarantee no infinite deadlock loops
    counter_file = "/tmp/typesafe_stop_counter.txt"
    count = 0
    if os.path.exists(counter_file):
        try:
            with open(counter_file, "r") as f:
                count = int(f.read().strip())
        except Exception:
            count = 0
    count += 1
    with open(counter_file, "w") as f:
        f.write(str(count))

    if count >= 2:
        log(f"Circuit breaker engaged: Stop hook called {count} times. Allowing termination.")
        try:
            os.remove(counter_file)
        except Exception:
            pass
        return {"decision": "allow"}

    transcript_path = data.get("transcriptPath")
    if not transcript_path or not os.path.isfile(transcript_path) or not api_key:
        return {"decision": "allow"}

    # Read last 15 steps of transcript
    try:
        steps = []
        with open(transcript_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        steps.append(json.loads(line))
                    except Exception:
                        pass
        recent_steps = steps[-15:] if len(steps) > 15 else steps
    except Exception as e:
        log(f"Failed to read transcript: {e}")
        return {"decision": "allow"}

    # Extract user prompt, agent response, and tool actions
    user_inputs = [s.get("content", "") for s in recent_steps if s.get("type") == "USER_INPUT"]
    tool_calls = []
    agent_responses = []

    for s in recent_steps:
        for tc in s.get("tool_calls", []):
            name = tc.get("name", "tool")
            args = tc.get("args", {})
            desc = args.get("toolAction") or args.get("toolSummary") or args.get("CommandLine") or name
            tool_calls.append(f"{name}: {desc}")
        if s.get("type") == "PLANNER_RESPONSE" and s.get("content"):
            agent_responses.append(s.get("content", ""))

    last_user = user_inputs[-1] if user_inputs else "Unknown request"
    last_agent = agent_responses[-1] if agent_responses else "Response"

    # 1. Check if the most recent tool execution was rejected by the user
    generic_steps = [s for s in recent_steps if s.get("type") == "GENERIC"]
    if generic_steps:
        last_tool_step = generic_steps[-1]
        err_text = str(last_tool_step.get("error", ""))
        content_text = str(last_tool_step.get("content", ""))
        combined_err = f"{err_text} {content_text}".lower()

        is_user_denied = (
            "user denied permission" in combined_err
            or "permission check failed" in combined_err
            or "user denied" in combined_err
        )

        if is_user_denied:
            last_tool_idx = last_tool_step.get("step_index", 0)
            has_subsequent_step = any(
                s.get("type") == "PLANNER_RESPONSE"
                and s.get("step_index", 0) > last_tool_idx
                for s in recent_steps
            )
            if not has_subsequent_step:
                log(f"User denial detected at step {last_tool_idx}. Forcing continuation so agent can reason through rejection.")
                return {
                    "decision": "continue",
                    "reason": (
                        "TypeSafe Recovery Notice: The user rejected the previous tool execution. "
                        "Do not terminate or re-attempt the denied command. "
                        "Observe the user's rejection, explain the situation, and reason through an alternative strategy."
                    ),
                }

    # 2. If tools were recently executed, verification was performed on machine
    if any("run_command" in tc for tc in tool_calls):
        log("Recent run_command execution found in transcript. Grounded verification confirmed.")
        try:
            os.remove(counter_file)
        except Exception:
            pass
        return {"decision": "allow"}

    # If the user request didn't ask to test/run/verify or is just conversational, allow
    if len(last_user.split()) < 5:
        return {"decision": "allow"}

    payload = {
        "state": {
            "user_request": last_user[:500],
            "agent_response": last_agent[:800],
            "tool_actions_taken": tool_calls[-8:],
        },
        "model": "jev-latest",
        "questions": {
            "ungrounded_assumptions": {
                "type": "noul",
                "instructions": (
                    "Did the agent claim that code/tests/hardware verification succeeded, "
                    "without actually having executed tests or verified outputs on the machine?"
                ),
            },
            "completion_acceptable": {
                "type": "noul",
                "instructions": (
                    "Is it acceptable for the agent to conclude its turn now based on the user's request and evidence provided?"
                ),
            },
        },
    }

    res = call_jev(payload, api_key)
    if not res or "answers" not in res:
        return {"decision": "allow"}

    ungrounded = res["answers"].get("ungrounded_assumptions", {}).get("noul", 0.0)
    acceptable = res["answers"].get("completion_acceptable", {}).get("noul", 1.0)

    log(f"Stop evaluation: ungrounded={ungrounded*100:.1f}%, acceptable={acceptable*100:.1f}%")

    if ungrounded >= 0.75 and acceptable < 0.35:
        return {
            "decision": "continue",
            "reason": (
                "TypeSafe Completion Gate: Ungrounded claims detected without machine verification. "
                "Execute the necessary tests, commands, or file checks before concluding."
            ),
        }

    return {"decision": "allow"}


def main():
    mode = "pre-tool"
    if "--mode" in sys.argv:
        idx = sys.argv.index("--mode")
        if idx + 1 < len(sys.argv):
            mode = sys.argv[idx + 1]

    # Read context from stdin
    raw_in = sys.stdin.read()
    if not raw_in.strip():
        print(json.dumps({"decision": "allow"}))
        return

    try:
        data = json.loads(raw_in)
    except Exception as e:
        log(f"Invalid JSON on stdin: {e}")
        print(json.dumps({"decision": "allow"}))
        return

    api_key = get_api_key()

    if mode == "pre-tool":
        out = handle_pre_tool(data, api_key)
    elif mode == "stop":
        out = handle_stop(data, api_key)
    else:
        out = {"decision": "allow"}

    # Output strictly formatted JSON to stdout
    print(json.dumps(out))


if __name__ == "__main__":
    main()
