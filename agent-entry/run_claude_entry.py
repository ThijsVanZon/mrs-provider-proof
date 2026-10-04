"""Run one fresh Claude Code headless session in a prepared consumer, then resume it once.

usage: python3 -I -B run_claude_entry.py --root <root from prepare_consumer.py> --out <new folder>

Uses the prompts in <root>/private/launch.json (they carry no marker). Turn 1 starts `claude -p` in the consumer;
turn 2 resumes that same session with `--resume`. The flags are those of the earlier native-Windows observation
plus one allow rule, `Bash(python3:*)`, for hosts whose Python is named python3:
- `--output-format stream-json --verbose`: the raw transcript, written unchanged to --out;
- `--permission-mode acceptEdits`: file edits and common file-system commands run without a prompt only inside the
  working directories (the consumer and the --add-dir folder); other commands still need approval, which nobody
  gives in print mode, so they are denied and listed as permission denials;
- `--allowedTools "Bash(python:*)" "Bash(python3:*)" Read Write`: allow rules, so these calls run without a prompt.
  They do not restrict which tools are available (that is `--tools`, not used here): the init event lists the
  available tools. A `python` command can read and write anything the operating-system user can;
- `--add-dir <evidence>`: a second working directory for file tools and acceptEdits. It is not an OS sandbox;
- `--strict-mcp-config` without `--mcp-config`: no MCP server.
User, project and managed settings still apply and merge with these flags; a deny rule at any level wins. run.json
records which user and project settings files exist, with their SHA-256 before and after (never their content);
managed policy (file, MDM or server) is not observed here. Nothing confines the session but these rules and the
prompts: check_entry.py checks afterwards that the consumer, shared and pstack checkouts are unchanged and clean.
Authentication comes from the environment the caller supplies (for example CLAUDE_CODE_OAUTH_TOKEN or
ANTHROPIC_API_KEY); only variable names are recorded, never values. Writes both raw transcripts and run.json to
--out. Installs nothing and changes no settings. A zero exit means both turns ran, not that the observation holds.
"""
import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
parser.add_argument("--root", required=True, type=Path)
parser.add_argument("--out", required=True, type=Path)
args = parser.parse_args()
root, out = args.root.resolve(), args.out.resolve()
facts = json.loads((root / "private" / "consumer.json").read_text(encoding="utf-8"))
prompts = json.loads((root / "private" / "launch.json").read_text(encoding="utf-8"))
consumer, evidence = Path(facts["consumer"]), Path(facts["evidence"])
out.mkdir(parents=True, exist_ok=True)
claude = shutil.which("claude")
if claude is None:
    raise SystemExit("claude is not on PATH; the harness is unavailable, so this path stays UNPROVED")
flags = ["--output-format", "stream-json", "--verbose", "--permission-mode", "acceptEdits",
         "--allowedTools", "Bash(python:*)", "Bash(python3:*)", "Read", "Write",
         "--add-dir", str(evidence), "--strict-mcp-config"]
run = {"started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
       "host": {"os": platform.platform(), "machine": platform.machine(), "python": platform.python_version()},
       "claude": claude, "flags": flags, "consumer": str(consumer),
       "auth_variables_present": sorted(k for k in os.environ
                                        if k in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")),
       "turns": []}
run["claude_version"] = subprocess.run([claude, "--version"], capture_output=True).stdout.decode().strip()
SETTINGS = [Path.home() / ".claude" / "settings.json", consumer / ".claude" / "settings.json",
            consumer / ".claude" / "settings.local.json"]


def settings_files() -> dict:
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in SETTINGS if p.is_file()}


run["settings_files_before"] = settings_files()


def turn(name: str, argv: list[str]) -> int:
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(out / name, "wb") as transcript:
        done = subprocess.run(argv, cwd=consumer, stdin=subprocess.DEVNULL, stdout=transcript, stderr=subprocess.PIPE)
    run["turns"].append({"file": name, "argv_without_prompt": [a for a in argv if a not in prompts.values()],
                         "started": started, "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                         "exit": done.returncode, "stderr_tail": done.stderr.decode("utf-8", "replace")[-2000:]})
    return done.returncode


turn("turn-1.jsonl", [claude, "-p", prompts["turn_1"], *flags])
session = None
for line in (out / "turn-1.jsonl").read_text(encoding="utf-8", errors="replace").splitlines():
    try:
        event = json.loads(line)
    except ValueError:
        continue
    if event.get("type") == "system" and event.get("subtype") == "init":
        session = event.get("session_id")
        break
run["session_id"] = session
if session:
    turn("turn-2.jsonl", [claude, "-p", prompts["turn_2"], "--resume", session, *flags])
run["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
run["settings_files_after"] = settings_files()
run["settings_files_unchanged"] = run["settings_files_after"] == run["settings_files_before"]
(out / "run.json").write_text(json.dumps(run, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(json.dumps({k: run[k] for k in ("claude_version", "session_id", "auth_variables_present",
                                       "settings_files_unchanged")}, indent=2))
sys.exit(0 if session and all(t["exit"] == 0 for t in run["turns"]) else 1)
