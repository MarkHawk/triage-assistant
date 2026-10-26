"""Command line: `retrieve` shows what a ticket pulls in; `ticket` triages one."""

from __future__ import annotations

import argparse
import dataclasses
import sys
import time
from pathlib import Path

from .answer import render
from .backends import make_backend
from .backends.base import BackendError
from .backends.demo import DemoBackend
from .config import ConfigError, load_config
from .guard import Guard, SecretFound
from .names import load_names
from .pipeline import run_triage
from .prompt import load_system
from .retrieval import Retriever
from .runbooks import load_runbooks
from .scenarios import load_scenarios
from .tickets import load_ticket, parse_ticket


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


def footer(backend, result, seconds: float, config) -> str:
    """One line for the engineer about how the answer was made. Goes to stderr."""
    parts = [f"{backend.name}" + (f" {backend.model}" if backend.model else ""), f"mode {result.mode}"]
    if backend.name == "ollama":
        parts.append(f"num_ctx {config.ollama_num_ctx}")
    parts.append(f"{seconds:.1f}s")
    if result.prompt is not None:
        parts.append(f"prompt estimated {result.prompt.estimated_tokens} tokens")
    if result.attempts:
        parts.append(f"{result.attempts} request{'s' if result.attempts != 1 else ''}")
    if result.attempts > 1:
        parts.append("RETRIED: first answer broke the rules")
    if result.code_fallback:
        parts.append("CODE FALLBACK: answer built by code")
    for key, value in result.stats.items():
        if value not in (None, "", 0, False) or key == "refusal_fallback_ran":
            parts.append(f"{key}={value}")
    if result.answer.source == "offline":
        parts.append("no model used")
    return "[" + "; ".join(parts) + "]"


def cmd_ticket(args: argparse.Namespace) -> int:
    config = load_config()
    if args.model:  # one flag for whichever backend is in use
        config = dataclasses.replace(config, ollama_model=args.model, claude_model=args.model)
    if args.num_ctx:
        if config.ollama_reserve_output_tokens >= args.num_ctx:
            raise ConfigError("--num-ctx must be larger than OLLAMA_RESERVE_OUTPUT_TOKENS")
        config = dataclasses.replace(config, ollama_num_ctx=args.num_ctx)
    guard = Guard.for_lab(config.lab_dir)
    backend = make_backend(config, guard, args.backend)
    company, systems = load_names(config.lab_dir)
    if args.file:
        ticket = load_ticket(Path(args.file))
    else:
        ticket = parse_ticket(args.text if args.text else sys.stdin.read())
    logs = "\n".join(part for part in (ticket.log_excerpt, Path(args.logs).read_text(encoding="utf-8") if args.logs else "") if part)
    started = time.monotonic()
    try:
        result = run_triage(ticket.text, logs, backend=backend, retriever=build_retriever(config.lab_dir),
                            scenarios=load_scenarios(config.lab_dir), guard=guard,
                            system_text=load_system(company, systems), log_secrets=config.log_secrets)
    except SecretFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except BackendError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 4
    print(render(result.answer, result.flags), end="")
    print(footer(backend, result, time.monotonic() - started, config), file=sys.stderr)
    if args.record and result.raw is not None and result.prompt is not None:
        demo = DemoBackend(guard, backend.budget)
        path = demo.record(result.prompt, result.raw, backend.name, backend.model)
        print(f"\nSaved for demo mode: {path.name}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="triage")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("retrieve", help="show which runbooks a ticket would retrieve")
    p.add_argument("text", nargs="?", help="the ticket text (default: read stdin)")
    p.add_argument("--logs", help="a file of log lines")
    p.add_argument("-k", type=int, default=3)
    p.set_defaults(func=cmd_retrieve)
    t = sub.add_parser("ticket", help="triage a ticket")
    t.add_argument("file", nargs="?", help="a ticket file (see triage/tickets.py); or use --text, or stdin")
    t.add_argument("--text", help="the ticket text")
    t.add_argument("--logs", help="a file of log lines")
    t.add_argument("--backend", choices=["claude", "ollama", "demo"], help="override BACKEND from .env")
    t.add_argument("--model", help="model name for this run, overriding OLLAMA_MODEL / CLAUDE_MODEL")
    t.add_argument("--num-ctx", type=int, help="Ollama context window for this run, overriding OLLAMA_NUM_CTX")
    t.add_argument("--record", action="store_true", help="save the answer for demo mode")
    t.set_defaults(func=cmd_ticket)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
