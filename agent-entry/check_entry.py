"""Collect and check one agent-entry observation after the harness ran, independently of the session. Read-only.

usage: python3 -I -B check_entry.py --root <root from prepare_consumer.py> --shared <shared checkout>
       --pstack <pstack checkout> --format claude-stream-json|codex-jsonl
       --transcript <turn 1 file> --transcript <turn 2 file> --out <new json file outside the root>
   or: python3 -I -B check_entry.py --root ... --shared ... --pstack ... --setup-only --out ...

Setup checks, made here whoever produced the files: an independent preflight --expect against the saved entry JSON;
the saved entry and resume preflight files; consumer, shared and pstack unchanged and clean. They do not show that
the session itself ran preflight or wrote those files.

Agent checks, from the two transcripts only. Each stays false while its evidence is missing, malformed or failed:
- both transcripts complete (session start, terminal result, no unreadable line) and successful;
- the marker quoted, and AGENTS.md named, before the first tool *start* (Claude tool_use, Codex item.started);
- turn 2 in the same session.
The behaviour stays UNPROVED (null) here: whether the session ran `mrs preflight`, read the routed files and ran
`mrs preflight --expect` on resume. A successful tool call can print a command, a verdict or a heading without doing
the thing, so this helper does not interpret commands or their output. It lists every tool call whose input or
output mentions preflight, --expect or a routed file under candidate_mentions, and the final verifier judges the
behaviour from the raw transcripts. The codex-jsonl reader follows the documented `codex exec --json` events and has
not seen a real Codex transcript. This is not a tamper-proof record.

Exit 0 when no check is false: the evidence is collected and consistent, and the behaviour is still UNPROVED.
all_checks_true is false when a check failed and otherwise null; it is never true. --setup-only reads no transcript
and leaves the whole agent path UNPROVED.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

C = "ffa9febec8bb97f4a07f828073ae452b1ff34800"
C_TREE = "d33145b1b8e02ba50bcd7624fdc371362f8c4509"
PSTACK_COMMIT, PSTACK_TREE = "7022c81efb48d8b5eb15498ce6043a3bd74b694c", "975600f2f90dc6f755d58cccdccee27f950edcd2"
ROUTED = ("docs/context.md", "docs/lifecycle.md")
BEHAVIOUR = ("turn_1_preflight_command_succeeded", "routed_reads_in_turn_1", "turn_2_expect_preflight_succeeded")
MENTIONS = ("preflight", "--expect", *ROUTED)  # pointers for raw review, never counted
EXCERPT = 600


def git(*args) -> str:
    done = subprocess.run(["git", *args], capture_output=True, env=dict(os.environ, GIT_ALLOW_PROTOCOL="file"))
    return done.stdout.decode("utf-8", "replace").strip() if done.returncode == 0 else f"<git exit {done.returncode}>"


def clean(path: Path) -> bool:
    return git("-C", str(path), "status", "--porcelain", "--untracked-files=all", "--ignored") == ""


def events(path: Path) -> tuple[list[dict], int]:
    """The transcript's JSON objects, and how many non-empty lines are not JSON objects."""
    parsed, unreadable = [], 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            event = None
        if isinstance(event, dict):
            parsed.append(event)
        else:
            unreadable += 1
    return parsed, unreadable


def text_of(content) -> str:
    if isinstance(content, list):
        return "\n".join(part.get("text", "") for part in content if isinstance(part, dict))
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)


def new_turn(parsed: list[dict], unreadable: int) -> dict:
    """items: ("text"|"tool"|"result", text) in transcript order, a "tool" item at the tool's start.
    tools: each tool with its start position, input text and result (None until one is seen)."""
    return {"session": None, "init": {}, "items": [], "tools": [], "terminal": None, "success": False,
            "events": len(parsed), "unreadable_lines": unreadable}


def claude_items(path: Path) -> dict:
    parsed, unreadable = events(path)
    turn, by_id = new_turn(parsed, unreadable), {}
    for event in parsed:
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            turn["session"] = event.get("session_id")
            turn["init"] = {k: event.get(k) for k in ("claude_code_version", "model", "cwd", "permissionMode",
                                                     "apiKeySource", "output_style", "tools")}
            turn["init"]["mcp_servers"] = [s.get("name") for s in event.get("mcp_servers") or [] if isinstance(s, dict)]
        elif kind == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if block.get("type") == "text":
                    turn["items"].append(("text", block.get("text", "")))
                elif str(block.get("type", "")).endswith("tool_use"):
                    data = block.get("input")
                    tool = {"at": len(turn["items"]), "id": block.get("id"), "name": block.get("name"),
                            "input": json.dumps(data, ensure_ascii=False), "result": None}
                    turn["items"].append(("tool", json.dumps({"name": tool["name"], "input": data}, ensure_ascii=False)))
                    turn["tools"].append(tool)
                    by_id[tool["id"]] = tool
        elif kind == "user":
            content = (event.get("message") or {}).get("content")
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    output = text_of(block.get("content"))
                    turn["items"].append(("result", output))
                    if block.get("tool_use_id") in by_id:
                        by_id[block["tool_use_id"]]["result"] = {"ok": block.get("is_error") is not True,
                                                                 "output": output}
        elif kind == "result":
            turn["terminal"] = {
                "subtype": event.get("subtype"), "is_error": event.get("is_error"), "num_turns": event.get("num_turns"),
                "result": str(event.get("result") or "")[:EXCERPT],
                "permission_denials": [{"tool": d.get("tool_name") or d.get("tool"),
                                        "input": json.dumps(d.get("tool_input"), ensure_ascii=False)[:EXCERPT]}
                                       for d in event.get("permission_denials") or [] if isinstance(d, dict)]}
            turn["success"] = event.get("subtype") == "success" and event.get("is_error") is not True
    return turn


def codex_items(path: Path) -> dict:
    parsed, unreadable = events(path)
    turn, by_id, errors = new_turn(parsed, unreadable), {}, []
    for event in parsed:
        kind, item = event.get("type"), event.get("item") or {}
        if kind == "thread.started":
            turn["session"] = event.get("thread_id")
        elif kind in ("item.started", "item.completed"):
            sort = item.get("type")
            if sort == "agent_message":
                if kind == "item.completed":
                    turn["items"].append(("text", item.get("text", "")))
            elif sort == "error":
                errors.append(str(item.get("message"))[:EXCERPT])
            elif sort != "reasoning":
                tool = by_id.get(item.get("id"))
                if tool is None:  # first sight of this tool: its start, or its completion if no start was emitted
                    tool = {"at": len(turn["items"]), "id": item.get("id"), "name": sort,
                            "input": json.dumps({k: v for k, v in item.items()
                                                 if k not in ("aggregated_output", "status", "exit_code")},
                                                ensure_ascii=False), "result": None}
                    turn["items"].append(("tool", json.dumps(item, ensure_ascii=False)))
                    turn["tools"].append(tool)
                    by_id[tool["id"]] = tool
                if kind == "item.completed":
                    output = item.get("aggregated_output") if sort == "command_execution" else json.dumps(item)
                    turn["items"].append(("result", output or ""))
                    tool["result"] = {"ok": item.get("status") in ("completed", None)
                                      and (sort != "command_execution" or item.get("exit_code") == 0),
                                      "output": output or "", "status": item.get("status"),
                                      "exit_code": item.get("exit_code")}
        elif kind in ("turn.completed", "turn.failed", "error"):
            if kind == "error":
                errors.append(str(event.get("message"))[:EXCERPT])
            elif turn["terminal"] is None or turn["terminal"]["event"] != "turn.failed":
                turn["terminal"] = {"event": kind, "error": event.get("error")}
    if turn["terminal"] is not None:
        turn["terminal"]["errors"] = errors
        turn["success"] = turn["terminal"]["event"] == "turn.completed" and not any(
            e.get("type") == "error" for e in parsed)
    return turn


def slashed(text: str) -> str:
    return text.replace("\\\\", "/").replace("\\", "/")


def judge(turns: list, marker: str) -> tuple[dict, list[dict]]:
    """Agent checks over turn 1 and turn 2 (None when missing), the behaviour left UNPROVED (None), and the tool calls
    that mention preflight, --expect or a routed file, for raw-transcript review."""
    first, second = (list(turns) + [None, None])[:2]
    complete = [t is not None and t["events"] > 0 and t["unreadable_lines"] == 0 and t["session"] is not None
                and t["terminal"] is not None for t in (first, second)]
    items = first["items"] if first else []
    tool_at = min((t["at"] for t in first["tools"]), default=len(items)) if first else 0
    quoted_at = next((i for i, (kind, text) in enumerate(items) if kind == "text" and marker in text), None)
    checks = {
        "turn_1_transcript_complete": complete[0],
        "turn_1_result_success": bool(first) and first["success"],
        "marker_quoted_before_first_tool_start": quoted_at is not None and quoted_at < tool_at,
        "names_agents_md_before_first_tool_start": any("AGENTS.md" in text for kind, text in items[:tool_at]
                                                       if kind == "text"),
        "turn_2_transcript_complete": complete[1],
        "turn_2_result_success": bool(second) and second["success"],
        "turn_2_same_session": bool(first) and bool(second) and second["session"] is not None
        and second["session"] == first["session"],
        **dict.fromkeys(BEHAVIOUR),
    }
    mentions = []
    for number, turn in ((1, first), (2, second)):
        for tool in (turn or {}).get("tools", []):
            result = tool["result"]
            output = (result or {}).get("output", "")
            if any(word in slashed(tool["input"] + "\n" + output).lower() for word in MENTIONS):
                mentions.append({"turn": number, "position": tool["at"], "tool": tool["name"],
                                 "input": tool["input"][:EXCERPT], "output": output[:EXCERPT],
                                 "tool_status": "missing" if result is None else "ok" if result["ok"] else "error"})
    return checks, mentions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--shared", required=True, type=Path)
    parser.add_argument("--pstack", required=True, type=Path)
    parser.add_argument("--format", choices=["claude-stream-json", "codex-jsonl"])
    parser.add_argument("--transcript", action="append", default=[], type=Path)
    parser.add_argument("--setup-only", action="store_true",
                        help="check the setup without reading transcripts; the agent path stays UNPROVED")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if args.setup_only and (args.transcript or args.format):
        parser.error("--setup-only reads no transcript; omit --format and --transcript")
    if not args.setup_only and (args.format is None or len(args.transcript) > 2):
        parser.error("an agent check needs --format and at most the two turns' --transcript files; "
                     "a turn not given counts as missing")
    root, shared, pstack = args.root.resolve(), args.shared.resolve(), args.pstack.resolve()
    facts = json.loads((root / "private" / "consumer.json").read_text(encoding="utf-8"))
    consumer, evidence, marker = Path(facts["consumer"]), Path(facts["evidence"]), facts["marker"]

    independent = subprocess.run([sys.executable, "-I", "-B", str(shared / "mrs"), "preflight", "--consumer",
                                  str(consumer), "--pstack", str(pstack), "--expect", str(evidence / "entry-preflight.json"),
                                  "--json"], capture_output=True)
    report = {"mode": "setup-only" if args.setup_only else "agent-observation",
              "independent_expect": {"exit": independent.returncode,
                                     "stdout": independent.stdout.decode("utf-8", "replace"),
                                     "stderr": independent.stderr.decode("utf-8", "replace")[-2000:]}}
    entry_file, resume_file = evidence / "entry-preflight.json", evidence / "resume-preflight.txt"
    try:
        entry = json.loads(entry_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        entry = None
    entry = entry if isinstance(entry, dict) else {}
    resume = resume_file.read_text(encoding="utf-8", errors="replace") if resume_file.is_file() else ""
    report["saved_files"] = {
        "entry_preflight_sha256": hashlib.sha256(entry_file.read_bytes()).hexdigest() if entry_file.is_file() else None,
        "resume_preflight_sha256": hashlib.sha256(resume_file.read_bytes()).hexdigest() if resume_file.is_file() else None,
        "resume_first_line": resume.splitlines()[0] if resume.strip() else None}
    agents = consumer / "AGENTS.md"

    def part(key: str) -> dict:
        return entry.get(key) if isinstance(entry.get(key), dict) else {}

    setup = {
        "independent_expect_ok": independent.returncode == 0,
        "saved_entry_preflight_ok_and_exact": entry.get("verdict") == "OK"
        and part("consumer").get("commit") == facts["consumer_commit"] and part("shared").get("commit") == C
        and part("pstack").get("commit") == PSTACK_COMMIT and part("pstack").get("tree") == PSTACK_TREE,
        "saved_resume_preflight_ok": bool(resume.strip()) and resume.splitlines()[0] == "PREFLIGHT OK",
        "consumer_unchanged_and_clean": git("-C", str(consumer), "rev-parse", "HEAD") == facts["consumer_commit"]
        and clean(consumer) and agents.is_file()
        and hashlib.sha256(agents.read_bytes()).hexdigest() == facts["files"]["AGENTS.md"]["sha256"],
        "shared_exact_and_clean": git("-C", str(shared), "rev-parse", "HEAD") == C
        and git("-C", str(shared), "rev-parse", "HEAD^{tree}") == C_TREE and clean(shared),
        "pstack_exact_and_clean": git("-C", str(pstack), "rev-parse", "HEAD") == PSTACK_COMMIT
        and git("-C", str(pstack), "rev-parse", "HEAD:pstack") == PSTACK_TREE and clean(pstack),
    }
    report["setup_checks"] = setup
    if args.setup_only:
        report.update({"agent_checks": None, "checks": setup, "all_checks_true": None,
                       "agent_observation": "UNPROVED: setup-only check, no transcript read"})
        passed = all(setup.values())
    else:
        reader = {"claude-stream-json": claude_items, "codex-jsonl": codex_items}[args.format]
        paths = (args.transcript + [None, None])[:2]
        turns = [reader(path) if path is not None and path.is_file() else None for path in paths]
        report["transcripts"] = [{"turn": n, "file": str(path) if path else None,
                                  "sha256": hashlib.sha256(path.read_bytes()).hexdigest()
                                  if path is not None and path.is_file() else None}
                                 for n, path in enumerate(paths, 1)]
        agent, mentions = judge(turns, marker)
        report["turns"] = [None if t is None else {
            "session": t["session"], "init": t["init"], "terminal": t["terminal"], "success": t["success"],
            "events": t["events"], "unreadable_lines": t["unreadable_lines"],
            "items": [{"kind": k, "text": v[:EXCERPT]} for k, v in t["items"]],
            "tools": [{"position": x["at"], "name": x["name"], "input": x["input"][:EXCERPT],
                       "result": None if x["result"] is None else {k: v[:EXCERPT] if isinstance(v, str) else v
                                                                   for k, v in x["result"].items()}}
                      for x in t["tools"]]} for t in turns]
        report["agent_checks"], report["candidate_mentions"] = agent, mentions
        report["checks"] = {**setup, **agent}
        failed = [k for k, v in report["checks"].items() if v is False]
        passed = not failed
        report["all_checks_true"] = None if passed else False
        report["agent_observation"] = (
            "COLLECTED: setup and transcript checks hold; preflight, routed reads and resume preflight UNPROVED until "
            "the final verifier judges the raw transcripts" if passed
            else "NOT ESTABLISHED: " + ", ".join(failed) + "; behaviour UNPROVED")
    report["note"] = ("Evidence collection for the final verifier to judge; not proof that the session ran preflight or "
                      "read the routed files, not a task verdict, not acceptance. The raw transcripts are the evidence.")
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("mode", "checks", "all_checks_true", "agent_observation")}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
