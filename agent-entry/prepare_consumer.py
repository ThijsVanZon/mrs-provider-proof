"""Prepare a disposable consumer for one fresh agent-entry observation of multi-repo-stack at exact C.

usage: python3 -I -B prepare_consumer.py --shared <clean checkout of C> --pstack <clean cursor/plugins checkout at
       the pin> --root <new folder>

Harness-neutral; Python 3.11+ and Git only, on Linux, macOS or Windows. It changes nothing outside --root:

  <root>/consumer/  a new Git repository with one commit: README.md, AGENTS.md (a one-use random marker line plus the
                    route section copied verbatim from the shared checkout's docs/connect.md), VERSION 26.1.0 and
                    multi-repo-stack.json, the only selector, choosing lifecycle applicability and exact C.
  <root>/evidence/  empty; the session writes its preflight outputs here.
  <root>/private/   consumer.json (marker, file hashes, commit) and launch.json (the two prompts). Never give this
                    folder or the marker to the session.

The shared and pstack checkouts stay outside the consumer and are only read. Nothing is pushed or installed.
"""
import argparse
import hashlib
import json
import os
import platform
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

C = "ffa9febec8bb97f4a07f828073ae452b1ff34800"
C_TREE = "d33145b1b8e02ba50bcd7624fdc371362f8c4509"
PSTACK_COMMIT, PSTACK_TREE = "7022c81efb48d8b5eb15498ce6043a3bd74b694c", "975600f2f90dc6f755d58cccdccee27f950edcd2"
FIXED_DATE = "2026-10-04T00:00:00+00:00"  # consumer commit dates; the marker alone makes each consumer unique
IDENTITY = ["-c", "user.name=Entry Proof", "-c", "user.email=entry-proof@example.invalid"]
INSTRUCTION_NAMES = ["AGENTS.md", "AGENTS.override.md", "CLAUDE.md", "CLAUDE.local.md", ".claude/CLAUDE.md",
                     ".claude/AGENTS.md"]


def git(*args, cwd=None, env=None) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          env=dict(os.environ, GIT_ALLOW_PROTOCOL="file", **(env or {})))
    if done.returncode != 0:
        raise SystemExit(f"git {' '.join(args[:3])} failed: {done.stderr.decode('utf-8', 'replace')}")
    return done.stdout.decode("utf-8").strip()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def instruction_files_above(start: Path) -> list[str]:
    """Instruction files a harness might load from the consumer's ancestors; reported, never changed."""
    found = []
    for folder in [start, *start.parents]:
        found += [str(folder / name) for name in INSTRUCTION_NAMES if (folder / name).is_file()]
    return found


parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
parser.add_argument("--shared", required=True, type=Path)
parser.add_argument("--pstack", required=True, type=Path)
parser.add_argument("--root", required=True, type=Path)
args = parser.parse_args()
shared, pstack, root = args.shared.resolve(), args.pstack.resolve(), args.root.resolve()

problems = []
if git("-C", str(shared), "rev-parse", "HEAD") != C or git("-C", str(shared), "rev-parse", "HEAD^{tree}") != C_TREE:
    problems.append(f"shared checkout is not exactly {C} (tree {C_TREE})")
if git("-C", str(shared), "status", "--porcelain", "--untracked-files=all", "--ignored"):
    problems.append("shared checkout is not clean")
if git("-C", str(pstack), "rev-parse", "HEAD") != PSTACK_COMMIT or \
        git("-C", str(pstack), "rev-parse", "HEAD:pstack") != PSTACK_TREE:
    problems.append(f"pstack checkout is not {PSTACK_COMMIT} with pstack tree {PSTACK_TREE}")
if git("-C", str(pstack), "status", "--porcelain", "--untracked-files=all", "--ignored"):
    problems.append("pstack checkout is not clean")
for inner, outer in ((root, shared), (root, pstack), (shared, root), (pstack, root)):
    if inner == outer or outer in inner.parents:
        problems.append(f"{inner} must not be inside {outer}")
if root.exists():
    problems.append(f"{root} already exists; use a new folder")
if problems:
    raise SystemExit("REFUSED: " + "; ".join(problems))

connect = (shared / "docs" / "connect.md").read_text(encoding="utf-8")
route = connect.split("```markdown\n", 1)[1].split("```", 1)[0].replace("\n   ", "\n").strip() + "\n"
marker = f"Consumer marker: mrs-entry-proof-{secrets.token_hex(8)}"
files = {
    "README.md": "# Example consumer\n\nA disposable consumer for one agent-entry observation. It has no product "
                 "code.\n",
    "AGENTS.md": f"# Example consumer\n\n{marker}. It shows whether this file was in context at session start.\n\n"
                 f"{route}",
    "VERSION": "26.1.0\n",
    "multi-repo-stack.json": json.dumps({"format": 1, "repository": "example/consumer", "applicability": "lifecycle",
                                         "shared": {"repository": "ThijsVanZon/multi-repo-stack", "commit": C}},
                                        indent=2) + "\n",
}
consumer, evidence, private = root / "consumer", root / "evidence", root / "private"
for folder in (root, evidence, private):
    folder.mkdir(parents=folder is root)
git("init", "--quiet", "--template=", str(consumer))
git("-C", str(consumer), "config", "core.autocrlf", "false")  # repository-local: committed bytes stay LF
for name, text in files.items():
    (consumer / name).write_bytes(text.encode("utf-8"))
git("-C", str(consumer), "add", "--", *files)
git("-C", str(consumer), *IDENTITY, "commit", "--quiet", "-m", "Connect a disposable consumer",
    env={"GIT_AUTHOR_DATE": FIXED_DATE, "GIT_COMMITTER_DATE": FIXED_DATE})

prompts = {
    "turn_1": (
        "This is a fresh session in a disposable consumer repository. Execution inputs, which are not committed "
        f"anywhere: shared checkout = {shared}; pstack checkout = {pstack}; evidence folder = {evidence}.\n"
        "1. Before you use any tool: if the project instructions already in your context contain a line beginning "
        "'Consumer marker:', quote that whole line exactly; otherwise write NO MARKER IN CONTEXT. Name the "
        "instruction files you have in context.\n"
        "2. Then do what those instructions require before dependent work. Run preflight with --json and save its "
        f"JSON output unchanged to {evidence / 'entry-preflight.json'}.\n"
        "3. Read every file that preflight lists and report the first heading line of each.\n"
        "4. Report the consumer, shared and pstack identities you verified. Change nothing in any repository; do not "
        "commit, push or install anything."),
    "turn_2": (
        "You are resuming this session after a pause. Before any further dependent work, revalidate as your project "
        f"instructions require, against {evidence / 'entry-preflight.json'}. Save that run's output to "
        f"{evidence / 'resume-preflight.txt'}. Report the verdict and whether dependent work may continue."),
}
facts = {
    "prepared_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "host": {"os": platform.platform(), "machine": platform.machine(), "python": platform.python_version(),
             "git": git("--version")},
    "shared": {"path": str(shared), "commit": C, "tree": C_TREE},
    "pstack": {"path": str(pstack), "commit": PSTACK_COMMIT, "pstack_tree": PSTACK_TREE},
    "root": str(root), "consumer": str(consumer), "evidence": str(evidence),
    "marker": marker,
    "consumer_commit": git("-C", str(consumer), "rev-parse", "HEAD"),
    "consumer_tree": git("-C", str(consumer), "rev-parse", "HEAD^{tree}"),
    "files": {name: {"bytes": len(text.encode("utf-8")), "sha256": sha(text.encode("utf-8"))}
              for name, text in files.items()},
    "route_sha256": sha(route.encode("utf-8")),
    "instruction_files_in_consumer_or_above": instruction_files_above(consumer),
    "user_level_instruction_files": [str(p) for p in (
        Path.home() / ".claude" / "CLAUDE.md",
        Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "AGENTS.md",
        Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "AGENTS.override.md") if p.is_file()],
}
(private / "consumer.json").write_text(json.dumps(facts, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
(private / "launch.json").write_text(json.dumps(prompts, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(json.dumps({k: facts[k] for k in ("consumer", "evidence", "consumer_commit", "consumer_tree",
                                        "instruction_files_in_consumer_or_above", "user_level_instruction_files")},
                 indent=2, ensure_ascii=False))
print(f"\nPrompts (no marker): {private / 'launch.json'}. Start the harness fresh in {consumer}.")
if facts["instruction_files_in_consumer_or_above"] != [str(consumer / "AGENTS.md")]:
    print("NOTE: other instruction files exist in the consumer's ancestors; record which ones load.", file=sys.stderr)
