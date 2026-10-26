"""Command line entry point. Only `retrieve` exists so far."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import ConfigError, load_config
from .names import load_names
from .retrieval import Retriever
from .runbooks import load_runbooks
from .scenarios import load_scenarios


def build_retriever(lab_dir: Path) -> Retriever:
    company, systems = load_names(lab_dir)
    vocabulary = company + " " + " ".join(s.display + " " + " ".join(s.staff_say) for s in systems.values())
    return Retriever(load_runbooks(lab_dir), load_scenarios(lab_dir), vocabulary=vocabulary)


def cmd_retrieve(args: argparse.Namespace) -> int:
    config = load_config()
    text = args.text if args.text else sys.stdin.read()
    logs = Path(args.logs).read_text(encoding="utf-8") if args.logs else ""
    result = build_retriever(config.lab_dir).search(text, logs, k=args.k)
    if not result.matches:
        print("No runbook fits this ticket.")
    for match in result.matches:
        strength = "strong" if match.strong else "weak (keywords only)"
        print(f"{match.runbook.number:02d} {match.runbook.title}  score={match.score}  {strength}")
        for reason in match.reasons:
            print(f"     - {reason}")
    for hint in result.hints:
        print(f"Hint: {hint}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="triage")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("retrieve", help="show which runbooks a ticket would retrieve")
    p.add_argument("text", nargs="?", help="the ticket text (default: read stdin)")
    p.add_argument("--logs", help="a file of log lines")
    p.add_argument("-k", type=int, default=3)
    p.set_defaults(func=cmd_retrieve)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
