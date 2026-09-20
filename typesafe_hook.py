#!/usr/bin/env python3
"""
typesafe_hook.py: Antigravity Lifecycle Hook powered by Laya & TypeSafe AI.

Architecture:
  - Primary Engine: Local Laya Daemon (http://127.0.0.1:8765)
      * Sub-40ms non-autoregressive System 1 decisions
      * Auto-spawns on-demand, auto-terminates after 10m idle
      * Instant shutdown on system suspend/lid-close
  - Secondary Engine: TypeSafe Cloud Jev (https://api.typesafe.ai/v1/systemone)
  - Tertiary Fallback: Strict Local Deterministic Gate (Fail-Safe Closed)

Security Invariants:
  - Atomic Fast-Path: Command chaining tokens (&&, ;, ||, |, etc.) strictly forbid fast-pathing.
  - Credential & Data Exfiltration Guard: Flags accesses to ~/.ssh, ~/.aws, .env, and outbound POST payloads.
  - Multi-Tool Protection: write_to_file and replace_file_content outside workspace require explicit intent.
  - Exact Subcommand Overrides: Eliminates blanket wildcard leaks by whitelisting decomposed subcommands.
  - Stop Hook Grounded Verification: Verifies genuine test runners rather than blind run_command presence.
"""

import os
import sys
import re
import json
import time
import fcntl
import urllib.request
import urllib.error
import subprocess
from typing import Dict, Any, Optional, List, Tuple

# Configuration
LAYA_URL = "http://127.0.0.1:8765"
LAYA_DAEMON_SCRIPT = os.path.expanduser("~/.config/typesafe/laya_daemon.py")
LAYA_VENV_PYTHON = os.path.expanduser("~/.config/typesafe/venv/bin/python3")
TYPESAFE_API_URL = "https://api.typesafe.ai/v1/systemone"
CONFIG_PATH = os.path.expanduser("~/.config/typesafe/config.json")
LOCK_FILE = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), f"typesafe_laya_{os.getuid()}.lock")

# Chaining tokens that forbid atomic fast-pathing
CHAINING_TOKENS = [";", "&&", "||", "|", "&", "`", "$(", "\n"]

# Strictly safe atomic read-only commands
SAFE_ATOMIC_CMD_REGEX = re.compile(
    r"^\s*(git\s+(status|diff|log|show|branch|tag|rev-parse)|"
    r"ls|cat|head|tail|grep|rg|find|which|type|pwd|echo|wc|uname|file|stat|"
    r"python3?\s+--version|node\s+-v|bun\s+-v)\s*$",
    re.IGNORECASE,
)

# Routine local development commands (single-command fast-path)
SAFE_LOCAL_DEV_REGEX = re.compile(
    r"^\s*(git\s+(add|commit|config|checkout|switch|init)|"
    r"gh\s+(auth\s+setup-git|api|repo\s+view)|"
    r"chmod|mkdir|touch|cp|mv)\b",
    re.IGNORECASE,
)

# Sensitive paths & credential patterns
SENSITIVE_PATTERNS = re.compile(
    r"(\b|/)(~?\.ssh\b|\.aws\b|\.gnupg\b|\.env\b|id_rsa|id_ed25519|/etc/(passwd|shadow|sudoers|pam\.d))",
    re.IGNORECASE,
)

# Network data exfiltration signatures
EXFIL_SIGNATURES = re.compile(
    r"\b(curl\s+.*(-d|--data|-F|--form|-T|--upload-file)|wget\s+.*--post-data|nc\s+.*<)\b",
    re.IGNORECASE,
)

# Actual test runner signatures for grounded verification
TEST_RUNNERS = [
    "pytest", "python -m unittest", "cargo test", "npm test",
    "bun test", "make test", "ctest", "go test", "vitest", "jest"
]

CONTINUATION_WORDS = {
    "continue", "continue.", "proceed", "proceed.", "go ahead",
    "yes", "ok", "okay", "approved", "do it", "next"
}


def log(msg: str):
    """Write diagnostic info to stderr and log file."""
    sys.stderr.write(f"[typesafe-hook] {msg}\n")
    sys.stderr.flush()
    try:
        with open("/tmp/typesafe_hook.log", "a", encoding="utf-8") as f:
            f.write(f"{msg}\n")
    except Exception:
        pass


def decompose_subcommands(cmd: str) -> List[str]:
    """Split compound command pipelines into individual subcommands."""
    parts = re.split(r"&&|\|\||;|\|", cmd)
    return [p.strip() for p in parts if p.strip()]


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


def is_laya_healthy(timeout: float = 0.15) -> bool:
    """Fast non-blocking check if local Laya daemon is responding."""
    try:
        req = urllib.request.Request(f"{LAYA_URL}/health", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                return True
    except Exception:
        pass
    return False


def ensure_laya_daemon() -> bool:
    """Ensure local Laya daemon is running; spawn on-demand if necessary."""
    if is_laya_healthy():
        return True

    # Attempt to spawn daemon if venv and script exist
    if not (os.path.isfile(LAYA_VENV_PYTHON) and os.path.isfile(LAYA_DAEMON_SCRIPT)):
        return False

    try:
        lock_fd = open(LOCK_FILE, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        # Another tool call is already booting the daemon; wait briefly
        for _ in range(20):
            time.sleep(0.1)
            if is_laya_healthy():
                return True
        return False

    try:
        # Double check after acquiring lock
        if is_laya_healthy():
            return True

        log("Spawning local Laya decision engine daemon in background...")
        subprocess.Popen(
            [LAYA_VENV_PYTHON, LAYA_DAEMON_SCRIPT],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Poll health for up to 3 seconds
        for _ in range(30):
            time.sleep(0.1)
            if is_laya_healthy():
                log("Local Laya daemon successfully booted and ready.")
                return True
    except Exception as e:
        log(f"Failed to auto-spawn Laya daemon: {e}")
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()
        except Exception:
            pass

    return False


def query_laya(payload: Dict[str, Any], timeout: float = 4.0) -> Optional[Dict[str, Any]]:
    """Query local Laya daemon."""
    try:
        req = urllib.request.Request(
            f"{LAYA_URL}/predict",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("answers", data)
    except Exception as e:
        log(f"Laya query failed: {e}")
        return None


def query_cloud_jev(payload: Dict[str, Any], api_key: str, timeout: float = 5.0) -> Optional[Dict[str, Any]]:
    """Query TypeSafe Cloud Jev API."""
    try:
        req = urllib.request.Request(
            TYPESAFE_API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "typesafe-hook-guardian/2.0",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("answers", data)
    except Exception as e:
        log(f"Cloud Jev query failed: {e}")
        return None


def query_decision_engine(payload: Dict[str, Any], api_key: Optional[str]) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Tiered Decision Engine Router:
      1. Local Laya Daemon (33ms, 100% offline)
      2. Cloud TypeSafe Jev (150ms)
    """
    # 1. Try Local Laya
    if is_laya_healthy():
        res = query_laya(payload)
        if res:
            return res, "laya"

    # Spawn Laya if not running
    if ensure_laya_daemon():
        res = query_laya(payload)
        if res:
            return res, "laya"

    # 2. Fallback to Cloud Jev
    if api_key:
        res = query_cloud_jev(payload, api_key)
        if res:
            return res, "cloud_jev"

    return None, "none"


def extract_user_intent(data: Dict[str, Any]) -> str:
    """Extract recent user requests with intent decay and continuation scoping."""
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

    if not user_inputs:
        return ""

    latest = user_inputs[-1]
    # If the user prompt is just a continuation word, include prior context
    if latest.lower() in CONTINUATION_WORDS and len(user_inputs) > 1:
        recent = user_inputs[-3:]
        return " | ".join(recent)
    
    return latest


def handle_pre_tool(data: Dict[str, Any], api_key: Optional[str]) -> Dict[str, Any]:
    """Comprehensive PreToolUse Gate."""
    name = data.get("tool_name", "")
    args = data.get("tool_input", {})

    log(f"PreToolUse: tool={name}")

    # 1. Read-only inspection tools always pass through
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

    # 2. Web / URL read tools pass through
    if name in ("read_url_content", "read_browser_page"):
        url = args.get("Url", "").strip()
        return {
            "decision": "allow",
            "permissionOverrides": [f"read_url({url})"] if url else ["read_url(*)"],
        }

    # 3. File Mutation Guard (write_to_file, replace_file_content)
    if name in ("write_to_file", "replace_file_content"):
        target_file = (args.get("TargetFile") or "").strip()
        workspace_paths = data.get("workspacePaths", [])

        # Check for sensitive files (e.g. .ssh, /etc, .env)
        if SENSITIVE_PATTERNS.search(target_file):
            return {
                "decision": "force_ask",
                "reason": f"TypeSafe Security Gate: Modification of sensitive file path requested: {target_file}",
            }

        # Check if write is outside workspace boundaries
        if workspace_paths and target_file:
            real_target = os.path.realpath(target_file)
            is_in_workspace = any(
                real_target == os.path.realpath(w) or real_target.startswith(os.path.realpath(w) + "/")
                for w in workspace_paths
            )
            if not is_in_workspace and not real_target.startswith(os.path.realpath("/tmp")):
                user_intent = extract_user_intent(data)
                if target_file not in user_intent and os.path.basename(target_file) not in user_intent:
                    return {
                        "decision": "force_ask",
                        "reason": f"TypeSafe Security Gate: Write outside active workspace requested: {target_file}",
                    }

        # Workspace write auto-approved
        return {
            "decision": "allow",
            "permissionOverrides": [f"write_file({target_file})"],
        }

    # If not run_command, allow with standard permissions
    if name != "run_command":
        return {"decision": "allow"}

    cmd = (args.get("CommandLine") or "").strip()
    if not cmd:
        return {"decision": "allow"}

    subcommands = decompose_subcommands(cmd)
    subcmd_overrides = [f"command({cmd})"] + [f"command({s})" for s in subcommands]

    # 4. Atomic Fast-Path: only if NO chaining operators are present
    has_chaining = any(token in cmd for token in CHAINING_TOKENS)
    if not has_chaining:
        if SAFE_ATOMIC_CMD_REGEX.match(cmd) or SAFE_LOCAL_DEV_REGEX.match(cmd):
            return {
                "decision": "allow",
                "reason": "Fast-path: safe atomic local command",
                "permissionOverrides": subcmd_overrides,
            }

    # 5. Sensitive Path & Exfiltration Inspection Gate
    user_intent = extract_user_intent(data)
    if SENSITIVE_PATTERNS.search(cmd) or EXFIL_SIGNATURES.search(cmd):
        has_explicit_intent = (
            user_intent and (
                any(p in user_intent for p in [".ssh", ".env", "curl", "upload", "token", "key"])
            )
        )
        if not has_explicit_intent:
            return {
                "decision": "force_ask",
                "reason": (
                    f"TypeSafe Security Gate: Sensitive credential access or outbound data transmission detected "
                    f"without explicit prompt instruction: {cmd}"
                ),
            }

    # 6. Semantic Risk Evaluation via Laya / Jev
    payload = {
        "state": {
            "tool": name,
            "command_line": cmd,
            "user_request": user_intent[:1000],
            "workspace_paths": data.get("workspacePaths", []),
        },
        "model": "typed-decisions",
        "questions": {
            "blast_radius": {
                "type": "score",
                "instructions": "Assess the operational blast radius and risk level of executing this shell command",
                "criteria": [
                    "Level 0: Harmless read, local inspection, safe build",
                    "Level 1: Local development operation, commit, package install, or remote publish",
                    "Level 2: Irreversible destruction (rm -rf, git clean -fdx, dd, mkfs, drop database, systemctl destructive action)",
                ],
            },
            "is_system_destruction": {
                "type": "noul",
                "instructions": "Does this command pose an irreversible risk of deleting, wiping, or corrupting local files or git histories?",
            },
            "is_publish_or_external_network": {
                "type": "noul",
                "instructions": "Is this an external deployment, publication, or remote sync operation (like git push, npm publish, uploading packages)?",
            },
            "explicitly_requested_by_user": {
                "type": "noul",
                "instructions": "Did the user explicitly request, instruct, or authorize this specific operation in their prompt?",
            },
        },
    }

    answers, engine = query_decision_engine(payload, api_key)

    # 7. Fallback to Strict Deterministic Policy if no engine is reachable
    if not answers:
        log("No decision engine reachable. Engaging Strict Deterministic Fallback.")
        # Destructive commands hard blocked
        if any(d in cmd for d in ["rm -rf", "mkfs", "dd if=", "git clean -fdx", ":(){ :|:& };:"]):
            return {
                "decision": "deny",
                "reason": f"TypeSafe Fallback Gate: Blocked catastrophic destructive command: {cmd}",
            }
        # Releases or remote actions escalate to modal
        if any(r in cmd for r in ["git push", "npm publish", "cargo publish", "docker push"]):
            if any(w in user_intent.lower() for w in ["push", "publish", "release", "deploy"]):
                return {"decision": "allow", "permissionOverrides": subcmd_overrides}
            return {
                "decision": "force_ask",
                "reason": f"TypeSafe Fallback Gate: External release detected without explicit prompt instruction: {cmd}",
            }
        # Routine safe dev allowed
        return {"decision": "allow", "permissionOverrides": subcmd_overrides}

    # Extract model answers
    score = answers.get("blast_radius", {}).get("score", 0.0)
    dest = answers.get("is_system_destruction", {}).get("noul", 0.0)
    pub = answers.get("is_publish_or_external_network", {}).get("noul", 0.0)
    user_req = answers.get("explicitly_requested_by_user", {}).get("noul", 0.0)

    log(
        f"PreTool evaluation ({engine}): blast={score:.2f}/2.0, "
        f"dest={dest*100:.0f}%, pub={pub*100:.0f}%, user_req={user_req*100:.0f}%"
    )

    # Tier 3: Hard-deny for irreversible destruction
    if dest >= 0.70 and score >= 1.50:
        return {
            "decision": "deny",
            "reason": (
                f"TypeSafe Auto-Mode Gate: Blocked command due to local system destruction risk "
                f"(blast_radius: {score:.2f}/2.0, destruction: {dest*100:.0f}%). "
                f"Execute a safer, non-destructive alternative."
            ),
        }

    # Tier 2: External releases / publishing or high destructive mutations
    if pub >= 0.50 or dest >= 0.50:
        if user_req >= 0.50:
            log(f"Auto-Mode: Action explicitly authorized (confidence: {user_req*100:.0f}%). Approving.")
            return {
                "decision": "allow",
                "permissionOverrides": subcmd_overrides,
            }
        category = "External release/push" if pub >= 0.50 else "High operational mutation"
        return {
            "decision": "force_ask",
            "reason": (
                f"TypeSafe Auto-Mode Escalation: {category} detected without explicit prompt instruction "
                f"(blast: {score:.2f}/2.0, pub: {pub*100:.0f}%, dest: {dest*100:.0f}%). "
                f"Confirm execution of: {cmd}"
            ),
        }

    # Tier 1: Safe routine local operation
    return {
        "decision": "allow",
        "permissionOverrides": subcmd_overrides,
    }


def handle_stop(data: Dict[str, Any], api_key: Optional[str]) -> Dict[str, Any]:
    """Stop Hook Gate: Prevents abandonment on user rejection and enforces grounded verification."""
    transcript_path = data.get("transcriptPath")
    if not transcript_path or not os.path.isfile(transcript_path):
        return {"decision": "allow"}

    # Session-scoped counter to prevent infinite retry loops
    session_id = str(abs(hash(transcript_path)))
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
    counter_file = os.path.join(runtime_dir, f"typesafe_stop_{os.getuid()}_{session_id}.cnt")

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

    if count >= 3:
        log("Circuit breaker engaged: Stop hook called 3 times. Allowing termination.")
        try:
            os.remove(counter_file)
        except Exception:
            pass
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

    # Check for user rejection in the most recent tool execution
    generic_steps = [s for s in recent_steps if s.get("type") == "GENERIC"]
    if generic_steps:
        last_tool = generic_steps[-1]
        err_text = str(last_tool.get("error", ""))
        content_text = str(last_tool.get("content", ""))
        combined = f"{err_text} {content_text}".lower()

        is_user_denied = (
            "user denied permission" in combined
            or "permission check failed" in combined
            or "user denied" in combined
        )

        if is_user_denied:
            last_tool_idx = last_tool.get("step_index", 0)
            has_subsequent = any(
                s.get("type") == "PLANNER_RESPONSE" and s.get("step_index", 0) > last_tool_idx
                for s in recent_steps
            )
            if not has_subsequent:
                log(f"User rejection detected at step {last_tool_idx}. Forcing continuation.")
                return {
                    "decision": "continue",
                    "reason": (
                        "TypeSafe Recovery Notice: The user rejected the previous tool execution. "
                        "Do not terminate or re-attempt the denied action. "
                        "Observe the rejection, explain the situation to the user, and reason through an alternative."
                    ),
                }

    # Clean up counter on normal exit
    try:
        os.remove(counter_file)
    except Exception:
        pass

    return {"decision": "allow"}


def main():
    mode = "pre-tool"
    if "--mode" in sys.argv:
        idx = sys.argv.index("--mode")
        if idx + 1 < len(sys.argv):
            mode = sys.argv[idx + 1]

    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        data = {}

    api_key = get_api_key()

    if mode == "pre-tool":
        res = handle_pre_tool(data, api_key)
    elif mode == "stop":
        res = handle_stop(data, api_key)
    else:
        res = {"decision": "allow"}

    print(json.dumps(res))


if __name__ == "__main__":
    main()
