"""Shrink a log snapshot to fit a budget, and say what was left out.

Nothing is dropped silently: the result ends with a line giving how many lines
were omitted, and the caller reports it to the engineer.
"""

from __future__ import annotations

import re

_INTERESTING = re.compile(
    r"\b(ERROR|WARN(ING)?|CRITICAL|FATAL)\b|error|fail|denied|refused|reject|invalid|timed out|time-out|"
    r"unauthori[sz]ed|forbidden|expired|[^0-9][45]\d\d[^0-9]|TCP_DENIED|event=\w*(failed|rejected|force_https)",
    re.IGNORECASE,
)


def collapse_repeats(lines: list[str]) -> list[str]:
    """Replace runs of identical lines (ignoring a leading timestamp) with one line and a count."""
    def key(line: str) -> str:
        return re.sub(r"^\S*\d{2}:\d{2}:\d{2}[.,\d]*Z?\s*", "", line)

    out: list[str] = []
    run = 0
    for i, line in enumerate(lines):
        if out and key(line) == key(lines[i - 1]):
            run += 1
            continue
        if run:
            out[-1] = f"{out[-1]}   [repeated {run + 1} times]"
        out.append(line)
        run = 0
    if run:
        out[-1] = f"{out[-1]}   [repeated {run + 1} times]"
    return out


def cap_logs(text: str, max_chars: int) -> tuple[str, int]:
    """(text that fits max_chars, number of lines omitted). Keeps error-like lines first, then the newest."""
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    lines = collapse_repeats(lines)
    if sum(len(l) + 1 for l in lines) <= max_chars:
        return "\n".join(lines), 0
    room = max_chars - 60  # for the omission note
    chosen: set[int] = set()
    used = 0
    for rank in (True, False):  # error-like lines, then everything else newest first
        order = [i for i, l in enumerate(lines) if bool(_INTERESTING.search(l)) == rank]
        if not rank:
            order.reverse()
        for i in (order if rank else order):
            cost = len(lines[i]) + 1
            if i in chosen or used + cost > room:
                continue
            chosen.add(i)
            used += cost
    kept = [lines[i] for i in sorted(chosen)]
    omitted = len(lines) - len(kept)
    kept.append(f"[... {omitted} log lines omitted to fit the prompt budget]")
    return "\n".join(kept), omitted
