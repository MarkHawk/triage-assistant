"""Real-ticket evaluation: your tickets, your expected runbook, one run each, ticket text only.

You write tests/real_tickets.yaml (copy tests/real_tickets.template.yaml). For each ticket the
runner triages it once, with no log lines, and reports:

  retrieval right   the expected runbook was among the top three retrieved (or, if none was
                    expected, nothing matched strongly)
  mode appropriate  diagnose or clarify, against the mode you expected
  cause right       in diagnose mode, the scenario named belongs to the expected runbook
                    (or is not_a_lab_fault / needs_more_info if none was expected);
                    in clarify mode no cause is asserted, so it reads n/a
  send-ready        left BLANK for you: yes / with edits / no

and writes a Markdown file (results/real-<date>-<model>-<window>.md) holding the table, then each
ticket with the full answer so you can judge the draft reply. Fill in the last column, then run
`python -m evals.real_tickets --summarise <that file>`.

Do not tune retrieval, phrases or prompts against this set before the first review. The file's
SHA-256 is recorded in the results so a later edit is visible.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from triage.answer import render
from triage.backends import make_backend
from triage.backends.base import Backend, BackendError
from triage.cli import build_retriever
from triage.config import REPO_ROOT, ConfigError, load_config
from triage.guard import Guard, SecretFound
from triage.names import load_names
from triage.pipeline import Triage, run_triage
from triage.prompt import load_system
from triage.retrieval import Retriever
from triage.scenarios import Scenario, load_scenarios

DEFAULT_FILE = REPO_ROOT / "tests" / "real_tickets.yaml"
TEMPLATE_FILE = REPO_ROOT / "tests" / "real_tickets.template.yaml"
DEFAULT_MODEL, DEFAULT_NUM_CTX = "qwen2.5:14b", 12288
VERDICTS = ("yes", "with edits", "no")
VERDICT_HEADER = "Send-ready (yes / with edits / no)"


class TicketFileError(ValueError):
    """The ticket file is not in the expected form. The message says where."""


@dataclass(frozen=True)
class RealTicket:
    id: str
    text: str
    runbooks: tuple[int, ...]  # acceptable runbook numbers; empty means none fits
    mode: str  # expected: diagnose or clarify
    mode_was_given: bool
    note: str = ""


def load_real_tickets(path: Path) -> list[RealTicket]:
    if not path.is_file():
        hint = f" Copy {TEMPLATE_FILE.name} to {path.name} and write your tickets." if path.parent == TEMPLATE_FILE.parent else ""
        raise TicketFileError(f"{path} does not exist.{hint}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise TicketFileError(f"{path.name} is not valid YAML: {exc}") from None
    if not isinstance(data, dict) or not isinstance(data.get("tickets"), list) or not data["tickets"]:
        raise TicketFileError(f"{path.name} needs a top-level 'tickets:' list with at least one ticket")
    tickets: list[RealTicket] = []
    seen: set[str] = set()
    for index, item in enumerate(data["tickets"], 1):
        where = f"ticket {index}" + (f" ({item['id']})" if isinstance(item, dict) and "id" in item else "")
        if not isinstance(item, dict):
            raise TicketFileError(f"{where}: each ticket must be a mapping with id, text and expect")
        tid, text, expect = str(item.get("id", "")).strip(), str(item.get("text", "")).strip(), item.get("expect")
        if not tid:
            raise TicketFileError(f"{where}: missing id")
        if tid in seen:
            raise TicketFileError(f"{where}: duplicate id")
        seen.add(tid)
        if not text:
            raise TicketFileError(f"{where}: missing text")
        if not isinstance(expect, dict) or "runbook" not in expect:
            raise TicketFileError(f"{where}: expect must be a mapping with a 'runbook' key "
                                  "(a number, a list of numbers, or null when no runbook fits)")
        raw = expect["runbook"]
        if raw is None:
            numbers: tuple[int, ...] = ()
        else:
            values = raw if isinstance(raw, list) else [raw]
            if not values or not all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in values):
                raise TicketFileError(f"{where}: expect.runbook must be a runbook number, a list of numbers, or null")
            numbers = tuple(values)
        mode = expect.get("mode")
        if mode is not None and mode not in ("diagnose", "clarify"):
            raise TicketFileError(f"{where}: expect.mode must be diagnose or clarify")
        tickets.append(RealTicket(tid, text, numbers, mode or ("diagnose" if numbers else "clarify"), mode is not None,
                                  str(item.get("note", "")).strip()))
    return tickets


@dataclass
class Row:
    ticket: RealTicket
    retrieved: list[tuple[int, bool]] = field(default_factory=list)  # (runbook number, strong)
    retrieval_right: bool = False
    mode: str = ""
    mode_right: bool = False
    outcome: str = ""
    confidence: str = ""
    cause: str = ""  # right, wrong, or n/a
    possibilities: list[int] = field(default_factory=list)
    attempts: int = 0
    code_fallback: bool = False
    warnings: int = 0
    dropped: int = 0
    runbook_quoted: int = 0  # of the dropped quotes, those taken from a runbook's own text
    seconds: float = 0.0
    error: str = ""
    rendered: str = ""


def score(ticket: RealTicket, result: Triage, scenarios: dict[str, Scenario]) -> Row:
    """Compare one triage result with what the ticket's author expected."""
    row = Row(ticket=ticket)
    row.retrieved = [(m.runbook.number, m.strong) for m in result.retrieval.matches]
    numbers = [n for n, _ in row.retrieved]
    if ticket.runbooks:
        row.retrieval_right = any(n in numbers for n in ticket.runbooks)
    else:
        row.retrieval_right = not any(strong for _, strong in row.retrieved)
    a = result.answer
    row.mode = result.mode
    row.mode_right = result.mode == ticket.mode or (result.mode == "request" and ticket.mode == "clarify")
    row.outcome, row.confidence = a.outcome, a.confidence
    row.possibilities = [p.runbook_number for p in a.possibilities]
    row.attempts, row.code_fallback = result.attempts, result.code_fallback
    row.warnings, row.dropped = len(a.warnings), len(a.dropped_evidence)
    row.runbook_quoted = len(a.runbook_quotes)
    if result.mode in ("clarify", "request"):
        row.cause = "n/a"
    else:
        wanted = {name for name, s in scenarios.items() if s.runbook_number in ticket.runbooks}
        ok = a.outcome in wanted if ticket.runbooks else a.outcome in ("not_a_lab_fault", "needs_more_info")
        row.cause = "right" if ok else "wrong"
    row.rendered = render(a, result.flags)
    return row


def run_all(tickets: list[RealTicket], *, backend: Backend, retriever: Retriever, scenarios: dict[str, Scenario],
            guard: Guard, system_text: str, log_secrets: str = "mask", persona: str = "vendor", progress=None) -> list[Row]:
    rows: list[Row] = []
    for ticket in tickets:
        started = time.monotonic()
        try:
            result = run_triage(ticket.text, "", backend=backend, retriever=retriever, scenarios=scenarios,
                                guard=guard, system_text=system_text, log_secrets=log_secrets, persona=persona)
            row = score(ticket, result, scenarios)
        except (BackendError, SecretFound) as exc:
            row = Row(ticket=ticket, error=str(exc))
        row.seconds = round(time.monotonic() - started, 1)
        rows.append(row)
        if progress:
            progress(row)
    return rows


# --- the report ----------------------------------------------------------------------------------

def _expected(t: RealTicket) -> str:
    return ", ".join(f"{n:02d}" for n in t.runbooks) if t.runbooks else "none"


def _retrieved(r: Row) -> str:
    return ", ".join(f"{n:02d}{'' if strong else ' (weak)'}" for n, strong in r.retrieved) or "nothing"


def _yn(flag: bool) -> str:
    return "yes" if flag else "NO"


def render_report(rows: list[Row], meta: dict) -> str:
    n = len(rows)
    done = [r for r in rows if not r.error]
    diag = [r for r in done if r.mode == "diagnose"]
    out = [f"# Real-ticket evaluation, {meta['date']}", "",
           f"- Backend and model: {meta['backend']} {meta['model']}" + (f", window {meta['num_ctx']}" if meta.get("num_ctx") else ""),
           f"- Tickets: {n} from `{meta['file']}` (SHA-256 {meta['sha256'][:12]}), one run each, ticket text only, no logs",
           f"- identity-lab commit: {meta['lab_commit']}", "",
           "Not for tuning: do not change retrieval, phrases or prompts against these tickets before the first review.", "",
           "## Summary", "",
           f"- Retrieval right: {sum(r.retrieval_right for r in done)} of {n}",
           f"- Mode appropriate: {sum(r.mode_right for r in done)} of {n}"
           + ("" if all(r.ticket.mode_was_given for r in rows) else " (where `expect.mode` was not given, diagnose is expected when a runbook is, clarify when none is)"),
           f"- Cause right: {sum(r.cause == 'right' for r in diag)} of {len(diag)} diagnosed (the other {len(done) - len(diag)} were clarify answers, which assert no cause)",
           f"- Quotes from the runbooks presented as evidence: {sum(r.runbook_quoted for r in done)} "
           f"(in {sum(1 for r in done if r.runbook_quoted)} answers). Other unverified quotes: {sum(r.dropped - r.runbook_quoted for r in done)}",
           f"- Errors: {sum(bool(r.error) for r in rows)}",
           "- Send-ready: fill in the last column, then run `python -m evals.real_tickets --summarise <this file>`", "",
           "## Results", "",
           f"| Id | Expected | Retrieved | Retrieval right | Mode (expected) | Mode right | Outcome | Cause | Confidence | Notes | {VERDICT_HEADER} |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if r.error:
            out.append(f"| {r.ticket.id} | {_expected(r.ticket)} | | | | | ERROR | | | {r.error[:80].replace('|', '/')} | |")
            continue
        notes = []
        if r.attempts > 1:
            notes.append("retried")
        if r.code_fallback:
            notes.append("built by code")
        if r.dropped - r.runbook_quoted:
            notes.append(f"{r.dropped - r.runbook_quoted} unverified quote(s)")
        if r.runbook_quoted:
            notes.append(f"{r.runbook_quoted} quoted from a runbook")
        if r.mode == "clarify" and r.ticket.runbooks:
            notes.append("listed: " + (", ".join(f"{p:02d}" for p in r.possibilities) or "none"))
        out.append(f"| {r.ticket.id} | {_expected(r.ticket)} | {_retrieved(r)} | {_yn(r.retrieval_right)} | {r.mode} ({r.ticket.mode}) "
                   f"| {_yn(r.mode_right)} | {r.outcome} | {r.cause} | {r.confidence} | {'; '.join(notes)} | |")
    out += ["", "## The answers", ""]
    for r in rows:
        out += [f"### {r.ticket.id}", "", "Ticket:", ""] + [f"> {line}" for line in r.ticket.text.splitlines()] + [""]
        if r.ticket.note:
            out += [f"Your note: {r.ticket.note}", ""]
        if r.error:
            out += [f"**Error:** {r.error}", ""]
            continue
        out += [f"Expected runbook: {_expected(r.ticket)}. Retrieved: {_retrieved(r)}. Time {r.seconds} s.", ""]
        out += [re.sub(r"^(#+) ", r"\1### ", line) for line in r.rendered.splitlines()] + [""]
    return "\n".join(out).rstrip() + "\n"


def summarise(path: Path) -> str:
    """Read the filled-in verdict column of a report and count the answers."""
    lines = path.read_text(encoding="utf-8").splitlines()
    header = next((i for i, l in enumerate(lines) if l.startswith("| Id |") and VERDICT_HEADER in l), None)
    if header is None:
        raise TicketFileError(f"{path.name} has no results table with a '{VERDICT_HEADER}' column")
    cols = [c.strip() for c in lines[header].strip().strip("|").split("|")]
    counts = {v: 0 for v in VERDICTS}
    blank, bad, by = 0, [], {}
    for line in lines[header + 2:]:
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != len(cols):
            continue
        row = dict(zip(cols, cells))
        verdict = row[VERDICT_HEADER].lower()
        if not verdict:
            blank += 1
        elif verdict in counts:
            counts[verdict] += 1
            by.setdefault((row["Retrieval right"], row["Mode right"], row["Cause"]), []).append(verdict)
        else:
            bad.append(f"{row['Id']}: {row[VERDICT_HEADER]!r}")
    total = sum(counts.values()) + blank + len(bad)
    out = [f"{path.name}: {total} tickets",
           f"  send-ready yes: {counts['yes']}, with edits: {counts['with edits']}, no: {counts['no']}, not yet filled in: {blank}"]
    if bad:
        out.append("  not understood (use yes / with edits / no): " + "; ".join(bad))
    judged = sum(counts.values())
    if judged:
        out.append(f"  usable as sent or with edits: {counts['yes'] + counts['with edits']} of {judged} judged")
        for (retr, mode, cause), verdicts in sorted(by.items()):
            out.append(f"  retrieval right={retr}, mode right={mode}, cause={cause}: "
                       + ", ".join(f"{verdicts.count(v)} {v}" for v in VERDICTS if v in verdicts))
    return "\n".join(out)


# --- command line --------------------------------------------------------------------------------

def _lab_commit(lab: Path) -> str:
    try:
        return subprocess.run(["git", "-C", str(lab), "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip() or "unknown"
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evals.real_tickets", description=__doc__.split("\n\n")[0])
    parser.add_argument("--file", default=str(DEFAULT_FILE), help="your tickets (default tests/real_tickets.yaml)")
    parser.add_argument("--backend", choices=["claude", "ollama", "demo"], default="ollama")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--num-ctx", type=int, default=DEFAULT_NUM_CTX)
    parser.add_argument("--out", default=str(REPO_ROOT / "results"), help="folder for the report")
    parser.add_argument("--summarise", metavar="REPORT", help="count the send-ready verdicts you filled in")
    args = parser.parse_args(argv)
    try:
        if args.summarise:
            print(summarise(Path(args.summarise)))
            return 0
        path = Path(args.file)
        tickets = load_real_tickets(path)
        config = dataclasses.replace(load_config(), ollama_model=args.model, claude_model=args.model, ollama_num_ctx=args.num_ctx)
        guard = Guard.for_lab(config.lab_dir)
        backend = make_backend(config, guard, args.backend)
        scenarios = load_scenarios(config.lab_dir)
        known = {s.runbook_number for s in scenarios.values()}
        for t in tickets:
            missing = [n for n in t.runbooks if n not in known]
            if missing:
                raise TicketFileError(f"ticket {t.id}: runbook {missing[0]} does not exist in the lab")
        company, systems = load_names(config.lab_dir)
        print(f"Running {len(tickets)} tickets on {args.backend} {args.model}" + (f" at {args.num_ctx}" if args.backend == "ollama" else ""), file=sys.stderr)
        rows = run_all(tickets, backend=backend, retriever=build_retriever(config.lab_dir), scenarios=scenarios, guard=guard,
                       system_text=load_system(company, systems, config.support_persona), log_secrets=config.log_secrets,
                       persona=config.support_persona,
                       progress=lambda r: print(f"  {r.ticket.id}: {r.error or r.mode + ' / ' + r.outcome} ({r.seconds} s)", file=sys.stderr))
    except (TicketFileError, ConfigError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    now = datetime.now(timezone.utc)
    meta = dict(date=now.strftime("%Y-%m-%d %H:%M UTC"), backend=args.backend, model=backend.model or args.model,
                num_ctx=args.num_ctx if args.backend == "ollama" else None, file=path.name,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(), lab_commit=_lab_commit(config.lab_dir))
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"real-{now.strftime('%Y%m%dT%H%M%SZ')}-{re.sub(r'[^A-Za-z0-9.]+', '_', meta['model'])}" + (f"-{args.num_ctx}" if meta["num_ctx"] else "")
    report = out_dir / f"{stem}.md"
    report.write_text(render_report(rows, meta), encoding="utf-8")
    (out_dir / f"{stem}.json").write_text(json.dumps({"meta": meta, "rows": [
        {**{k: v for k, v in dataclasses.asdict(r).items() if k not in ("ticket", "rendered")}, "id": r.ticket.id,
         "expected": list(r.ticket.runbooks)} for r in rows]}, indent=1) + "\n", encoding="utf-8")
    print(f"Wrote {report}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
