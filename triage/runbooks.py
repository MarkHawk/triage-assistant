"""Load identity-lab's runbooks and cut them into the parts the assistant uses.

Every runbook has the same shape: a title line, a small metadata table, then
Symptom, Where to look, Diagnosis, Fix, Prevention and "Questions I'd ask the
customer". They are read from the lab at run time and never copied.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_TITLE = re.compile(r"^#\s+(\d+)\s*[·.:-]\s*(.+)$")
_META_ROW = re.compile(r"^\|\s*\*\*(.+?)\*\*\s*\|\s*(.*?)\s*\|$")
_CUSTOMER_LINE = re.compile(r"^How customers describe it:\s*(.+)$", re.MULTILINE)

# Order the sections are shown in, and how much of an excerpt each may use.
_EXCERPT_ORDER = ("Symptom", "Diagnosis", "Fix", "Questions I'd ask the customer")
_EXCERPT_SHARE = {"header": 0.18, "Symptom": 0.28, "Diagnosis": 0.26, "Fix": 0.18,
                  "Questions I'd ask the customer": 0.10}
_TRIMMED = "[... trimmed]"


@dataclass(frozen=True)
class Runbook:
    number: int
    title: str
    path: str  # relative to the lab, e.g. runbooks/15-redirect-loop.md
    meta: dict[str, str]
    sections: dict[str, str] = field(default_factory=dict)

    @property
    def customer_phrases(self) -> str:
        """The 'How customers describe it' paragraph, if the runbook has one."""
        match = _CUSTOMER_LINE.search(self.sections.get("Symptom", ""))
        return match.group(1) if match else ""

    def header(self) -> str:
        parts = [f"# {self.number:02d} · {self.title}", f"File: {self.path}"]
        for key in ("Blast radius", "Typical trigger"):
            if key in self.meta:
                parts.append(f"{key}: {self.meta[key]}")
        return "\n".join(parts)

    def excerpt(self, max_chars: int) -> str:
        """A version of the runbook that fits max_chars, keeping the useful parts.

        Fix and the customer questions are kept before the long Diagnosis is cut.
        """
        parts = {"header": self.header(), **{k: self.sections.get(k, "") for k in _EXCERPT_ORDER}}
        caps = {k: int(max_chars * _EXCERPT_SHARE[k]) for k in parts}
        used = {k: min(len(v), caps[k]) for k, v in parts.items()}
        spare = max_chars - sum(used.values())
        for key in ("header", "Diagnosis", "Symptom", "Fix", "Questions I'd ask the customer"):
            extra = min(spare, len(parts[key]) - used[key])
            if extra > 0:
                used[key] += extra
                spare -= extra
        out = [_cut(parts["header"], used["header"])]
        for key in _EXCERPT_ORDER:
            if parts[key]:
                out.append(f"## {key}\n{_cut(parts[key], used[key])}")
        return "\n\n".join(out)


def _cut(text: str, limit: int) -> str:
    """Cut at a line boundary, closing any code fence left open."""
    if len(text) <= limit:
        return text
    room = max(limit - len(_TRIMMED) - 5, 0)
    head = text[:room]
    if "\n" in head:
        head = head[: head.rindex("\n")]
    if head.count("```") % 2:
        head += "\n```"
    return f"{head}\n{_TRIMMED}"


def parse_runbook(path: Path, lab_dir: Path) -> Runbook:
    lines = path.read_text(encoding="utf-8").splitlines()
    match = _TITLE.match(lines[0]) if lines else None
    if not match:
        raise ValueError(f"{path.name}: first line is not '# NN · Title'")
    meta: dict[str, str] = {}
    sections: dict[str, list[str]] = {}
    current: str | None = None
    fenced = False
    for line in lines[1:]:
        if line.lstrip().startswith("```"):
            fenced = not fenced
        if not fenced and line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
        elif current is None:
            row = _META_ROW.match(line)
            if row:
                meta[row.group(1)] = row.group(2)
        else:
            sections[current].append(line)
    return Runbook(
        number=int(match.group(1)),
        title=match.group(2).strip(),
        path=str(path.relative_to(lab_dir)),
        meta=meta,
        sections={k: "\n".join(v).strip() for k, v in sections.items()},
    )


def load_runbooks(lab_dir: Path) -> dict[int, Runbook]:
    folder = lab_dir / "runbooks"
    if not folder.is_dir():
        raise FileNotFoundError(f"no runbooks folder in the lab at {folder}")
    books = [parse_runbook(p, lab_dir) for p in sorted(folder.glob("[0-9][0-9]-*.md"))]
    return {b.number: b for b in books}


_FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_MIN_DOCUMENTED_CHARS = 15


def _normal(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def documented_lines(books: dict[int, Runbook]) -> dict[int, tuple[str, ...]]:
    """The error text each runbook documents, from the code blocks of its Symptom section.

    Only lines that can tell one runbook from another are kept: at least 15 characters, not a shell
    command, and not found anywhere in another runbook's Symptom code blocks, even as part of a longer
    line (so "502 Bad Gateway", which runbook 06 shows inside its HTML page, cannot name runbook 10).
    """
    per_book: dict[int, list[str]] = {}
    for number, book in books.items():
        lines = []
        for block in _FENCE.findall(book.sections.get("Symptom", "")):
            for raw in block.splitlines():
                line = raw.strip()
                if len(line) >= _MIN_DOCUMENTED_CHARS and not line.startswith(("$", "#", "curl ", "http")):
                    lines.append(line)
        per_book[number] = lines
    shown = {n: _normal("\n".join(b for b in _FENCE.findall(book.sections.get("Symptom", "")))) for n, book in books.items()}
    return {n: tuple(l for l in lines if not any(_normal(l) in text for m, text in shown.items() if m != n))
            for n, lines in per_book.items()}
