#!/usr/bin/env bash
# ==============================================================================
# install.sh: One-Command Installer for Antigravity Auto Mode (TypeSafe AI)
# ==============================================================================
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TYPESAFE_CONFIG_DIR="$HOME/.config/typesafe"
GEMINI_CONFIG_DIR="$HOME/.gemini/config"
AGY_SETTINGS_DIR="$HOME/.gemini/antigravity-cli"
LOCAL_BIN_DIR="$HOME/.local/bin"

echo "=== Installing Antigravity Auto Mode (Claude Code Emulation) ==="

# 1. Check Python 3
if ! command -v python3 >/dev/null 2>&1; then
    echo "[-] Error: python3 is required but not found."
    exit 1
fi

# 2. Setup directories
mkdir -p "$TYPESAFE_CONFIG_DIR"
mkdir -p "$GEMINI_CONFIG_DIR"
mkdir -p "$AGY_SETTINGS_DIR"
mkdir -p "$LOCAL_BIN_DIR"

# 3. Configure TypeSafe API Key
CONFIG_FILE="$TYPESAFE_CONFIG_DIR/config.json"
if [ ! -f "$CONFIG_FILE" ]; then
    if [ -n "$TYPESAFE_API_KEY" ]; then
        echo "{\"api_key\": \"$TYPESAFE_API_KEY\"}" > "$CONFIG_FILE"
        chmod 600 "$CONFIG_FILE"
        echo "[+] API key configured from \$TYPESAFE_API_KEY."
    else
        cp "$REPO_DIR/templates/config.json.example" "$CONFIG_FILE"
        chmod 600 "$CONFIG_FILE"
        echo "[!] Notice: Created $CONFIG_FILE template. Please edit it and set your TypeSafe API key."
    fi
else
    echo "[*] Existing TypeSafe config found at $CONFIG_FILE."
fi

# 4. Install Hook Script
cp "$REPO_DIR/typesafe_hook.py" "$TYPESAFE_CONFIG_DIR/typesafe_hook.py"
chmod +x "$TYPESAFE_CONFIG_DIR/typesafe_hook.py"
echo "[+] Installed lifecycle hook to $TYPESAFE_CONFIG_DIR/typesafe_hook.py."

# 5. Install CLI Diagnostic Tool
cp "$REPO_DIR/typesafe_auto_mode.py" "$LOCAL_BIN_DIR/typesafe-auto-mode"
chmod +x "$LOCAL_BIN_DIR/typesafe-auto-mode"
echo "[+] Installed CLI diagnostic utility to $LOCAL_BIN_DIR/typesafe-auto-mode."

# 6. Configure Antigravity Baseline Settings (~/.gemini/antigravity-cli/settings.json)
python3 - <<EOF
import json, os

settings_path = os.path.expanduser("~/.gemini/antigravity-cli/settings.json")
cfg = {}
if os.path.isfile(settings_path):
    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}

if "permissions" not in cfg or not isinstance(cfg["permissions"], dict):
    cfg["permissions"] = {}

allow_grants = [
    "command(*)",
    "read_file(*)",
    "write_file(*)",
    "read_url(*)",
    "execute_url(*)",
    "mcp(*)"
]

current_allows = cfg["permissions"].get("allow", [])
for g in allow_grants:
    if g not in current_allows:
        current_allows.append(g)
cfg["permissions"]["allow"] = current_allows

trusted = cfg.get("trustedWorkspaces", [])
home = os.path.expanduser("~")
for p in ["/", home, os.getcwd()]:
    if p not in trusted:
        trusted.append(p)
cfg["trustedWorkspaces"] = trusted

with open(settings_path, "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2)
print("[+] Configured wildcard baseline permissions in", settings_path)
EOF

# 7. Configure Lifecycle Hooks (~/.gemini/config/hooks.json)
python3 - <<EOF
import json, os

hooks_path = os.path.expanduser("~/.gemini/config/hooks.json")
cfg = {}
if os.path.isfile(hooks_path):
    try:
        with open(hooks_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}

guardian = {
    "enabled": True,
    "PreToolUse": [
        {
            "matcher": "*",
            "hooks": [
                {
                    "type": "command",
                    "command": f"python3 {os.path.expanduser('~/.config/typesafe/typesafe_hook.py')} --mode pre-tool",
                    "timeout": 10
                }
            ]
        }
    ],
    "Stop": [
        {
            "type": "command",
            "command": f"python3 {os.path.expanduser('~/.config/typesafe/typesafe_hook.py')} --mode stop",
            "timeout": 10
        }
    ]
}

cfg["typesafe-guardian"] = guardian

with open(hooks_path, "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2)
print("[+] Configured PreToolUse and Stop hooks in", hooks_path)
EOF

# 8. Configure Autonomous Execution Standards (~/.gemini/config/AGENTS.md)
AGENTS_MD="$GEMINI_CONFIG_DIR/AGENTS.md"
STANDARDS_HEADER="# Autonomous Execution Standards (Claude Code Auto Mode Emulation)"

if [ -f "$AGENTS_MD" ]; then
    if ! grep -q "$STANDARDS_HEADER" "$AGENTS_MD"; then
        echo "" >> "$AGENTS_MD"
        cat "$REPO_DIR/templates/AGENTS.md" >> "$AGENTS_MD"
        echo "[+] Appended Auto Mode execution standards to $AGENTS_MD."
    else
        echo "[*] Auto Mode execution standards already present in $AGENTS_MD."
    fi
else
    cat "$REPO_DIR/templates/AGENTS.md" > "$AGENTS_MD"
    echo "[+] Created $AGENTS_MD with Auto Mode execution standards."
fi

# Ensure GEMINI.md symlink points to AGENTS.md
GEMINI_MD="$GEMINI_CONFIG_DIR/GEMINI.md"
if [ ! -e "$GEMINI_MD" ]; then
    ln -s "$AGENTS_MD" "$GEMINI_MD"
    echo "[+] Symlinked GEMINI.md -> AGENTS.md."
fi

echo ""
echo "=== Installation Complete! Running Diagnostics ==="
"$LOCAL_BIN_DIR/typesafe-auto-mode"
