"""Pick the runbooks most likely to explain a ticket.

Plain Python, no model and no network, so it is quick and repeatable:

* BM25 over the runbooks' sections, weighted so the title, the symptom and the
  "how customers describe it" lines count most. The customer words are in the
  runbooks themselves.
* data/phrases.yaml maps how people actually talk ("goes round in circles")
  to runbooks. It also holds hints for user-side causes (an old bookmark),
  which stop weak keyword matches from forcing a runbook.
* Log lines add strong evidence: the lab's own SCENARIO_EXPECT text and
  SCENARIO_LOG patterns, read from the scenario files.

A result with no matches is a proper answer: the model is told nothing fits.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .config import REPO_ROOT
from .runbooks import Runbook, documented_lines
from .scenarios import Scenario

_WORD = re.compile(r"[a-z0-9]+")
_EVENT = re.compile(r"\bevent=([a-z][a-z0-9_]+)")
_STOP = frozenset("""a an and are as at be been but by can could did do does for from had has have he her his i if in into
is it its me my no not of on or our she so some that the their them then there they this to too up us was we were what when
where which who why will with would you your please thanks thank hi hello dear regards just also very get got""".split())

FIELD_WEIGHT = {"title": 4.0, "customers": 4.0, "Symptom": 2.0, "trigger": 2.0, "Diagnosis": 1.0,
                "Where to look": 0.7, "Fix": 0.7, "Questions I'd ask the customer": 1.0, "Prevention": 0.3}

K1, B = 1.4, 0.5
LOG_WEIGHT = 0.5  # BM25 over log lines counts for less than over the ticket's words
SIGNATURE_BOOST = 3.0
EVENT_BOOST = 3.0  # shared out between the runbooks that mention the log event
EVENT_MAX_RUNBOOKS = 3  # an event named in more runbooks than this says little
PHRASE_CAP = 3.0
MIN_SCORE = 0.55  # a match must reach this; tuned on tests/retrieval_queries.yaml
RELATIVE_FLOOR = 0.45  # ...and this fraction of the best match
MIN_COVERAGE = 0.6  # keyword-only matches need at least this share of the ticket's words to appear in the runbooks
RAW_BM25_FLOOR = 2.5  # below this the wording says too little (an unrelated ticket) and counts for nothing


def stem(word: str) -> str:
    """A light stemmer, applied to both sides so 'expired' meets 'expire'."""
    for suffix in ("ing", "ed", "es", "ly", "s"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            word = word[: -len(suffix)]
            break
    if len(word) > 4 and word.endswith("e"):
        word = word[:-1]
    return word


def words(text: str) -> list[str]:
    return [stem(w) for w in _WORD.findall(text.lower())]


def terms(text: str) -> list[str]:
    return [w for w in words(text) if w not in _STOP or w in ("no",)]


_WORDING = "ticket wording matches the runbook"


@dataclass(frozen=True)
class Match:
    runbook: Runbook
    score: float
    reasons: tuple[str, ...]

    @property
    def strong(self) -> bool:
        """True if more than keyword overlap supports it: customer phrasing or an error text or log pattern."""
        return any(r != _WORDING for r in self.reasons)


@dataclass(frozen=True)
class Result:
    matches: tuple[Match, ...]
    hints: tuple[str, ...] = ()
    best_raw: float = 0.0  # the best candidate's score even when nothing was returned

    @property
    def numbers(self) -> list[int]:
        return [m.runbook.number for m in self.matches]


@dataclass
class _Entry:
    say: list[list[str]]
    near: list[list[str]]
    weights: dict[int, float]
    hint: str


def _load_entries(path: Path) -> list[_Entry]:
    out = []
    for raw in yaml.safe_load(path.read_text(encoding="utf-8")) or []:
        out.append(_Entry(
            say=[words(str(p)) for p in raw.get("say", [])],
            near=[[stem(str(w).lower()) for w in group] for group in raw.get("near", [])],
            weights={int(k): float(v) for k, v in (raw.get("runbooks") or {}).items()},
            hint=raw.get("hint", ""),
        ))
    return out


def _contains(seq: list[str], sub: list[str]) -> bool:
    n = len(sub)
    return n > 0 and any(seq[i:i + n] == sub for i in range(len(seq) - n + 1))


def _near(seq: list[str], group: list[str], window: int = 8) -> bool:
    for i, w in enumerate(seq):
        if w == group[0] and all(g in seq[max(0, i - window): i + window + 1] for g in group[1:]):
            return True
    return False


class Retriever:
    def __init__(self, runbooks: dict[int, Runbook], scenarios: dict[str, Scenario] | None = None,
                 phrases_path: Path | None = None, vocabulary: str = ""):
        """vocabulary: extra known words (the company's system names), so they don't count as unknown."""
        self.runbooks = runbooks
        self.documented = documented_lines(runbooks)  # the error text each runbook shows, unique to it
        from .answer import Reference  # answer imports retrieval lazily, so this one is local too

        self.reference = Reference(["\n".join(b.sections.values()) for b in runbooks.values()])
        self.scenarios = scenarios or {}
        self.entries = _load_entries(phrases_path or REPO_ROOT / "data" / "phrases.yaml")
        self._tf: dict[int, Counter[str]] = {}
        self._len: dict[int, float] = {}
        for number, book in runbooks.items():
            tf: Counter[str] = Counter()
            for name, text in self._fields(book).items():
                for term in terms(text):
                    tf[term] += FIELD_WEIGHT.get(name, 1.0)
            self._tf[number] = tf
            self._len[number] = sum(tf.values())
        self._avg = sum(self._len.values()) / max(len(self._len), 1)
        self._known = {t for tf in self._tf.values() for t in tf} | set(terms(vocabulary))
        df: Counter[str] = Counter()
        for tf in self._tf.values():
            df.update(tf.keys())
        n = len(runbooks)
        self._idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self._events: dict[str, list[int]] = {}
        for number, book in runbooks.items():
            text = "\n".join(self._fields(book).values())
            for event in set(_EVENT.findall(text)):
                self._events.setdefault(event, []).append(number)
        self._expect = {s.name: s.expect.lower() for s in self.scenarios.values() if s.expect}
        self._patterns = {}
        for s in self.scenarios.values():
            if s.log_pattern:
                try:
                    self._patterns[s.name] = re.compile(s.log_pattern)
                except re.error:
                    continue

    @staticmethod
    def _fields(book: Runbook) -> dict[str, str]:
        fields = {"title": book.title, "customers": book.customer_phrases, "trigger": book.meta.get("Typical trigger", "")}
        fields.update({k: v for k, v in book.sections.items()})
        return fields

    def _bm25(self, query_terms: list[str]) -> dict[int, float]:
        scores = {}
        for number, tf in self._tf.items():
            norm = K1 * (1 - B + B * self._len[number] / self._avg)
            total = 0.0
            for term in set(query_terms):
                f = tf.get(term, 0.0)
                if f:
                    total += self._idf[term] * f * (K1 + 1) / (f + norm)
            scores[number] = total
        return scores

    @staticmethod
    def _normalise(scores: dict[int, float]) -> dict[int, float]:
        top = max(scores.values(), default=0.0)
        return {k: (v / top if top else 0.0) for k, v in scores.items()}

    def search(self, ticket: str, logs: str = "", k: int = 3) -> Result:
        seq = words(ticket)
        reasons: dict[int, list[str]] = {n: [] for n in self.runbooks}
        score: dict[int, float] = {n: 0.0 for n in self.runbooks}

        raw = self._bm25(terms(ticket))
        if max(raw.values(), default=0.0) >= RAW_BM25_FLOOR:
            for number, value in self._normalise(raw).items():
                score[number] += value
                if value >= 0.5:
                    reasons[number].append(_WORDING)
        if logs.strip():
            raw_logs = self._bm25(terms(logs))
            if max(raw_logs.values(), default=0.0) >= RAW_BM25_FLOOR:
                for number, value in self._normalise(raw_logs).items():
                    score[number] += LOG_WEIGHT * value

        # People's own words, from data/phrases.yaml.
        phrase_total: dict[int, float] = {}
        hints: list[str] = []
        for entry in self.entries:
            hit = any(_contains(seq, p) for p in entry.say) or any(_near(seq, g) for g in entry.near)
            if not hit:
                continue
            if entry.hint and entry.hint not in hints:
                hints.append(entry.hint)
            for number, weight in entry.weights.items():
                if number in self.runbooks:
                    phrase_total[number] = phrase_total.get(number, 0.0) + weight
                    reasons[number].append("matches how customers describe it")
        for number, total in phrase_total.items():
            score[number] += min(total, PHRASE_CAP)

        # The lab's own error text and log patterns.
        evidence = (ticket + "\n" + logs).lower()
        for name, text in self._expect.items():
            if text in evidence:
                self._boost(score, reasons, name, f"error text '{self.scenarios[name].expect}' is in the input")
        for name, pattern in self._patterns.items():
            if logs and pattern.search(logs):
                self._boost(score, reasons, name, "a log line matches the scenario's log pattern")

        for event in set(_EVENT.findall(logs)):
            owners = self._events.get(event, [])
            if 0 < len(owners) <= EVENT_MAX_RUNBOOKS:
                for number in owners:
                    score[number] += EVENT_BOOST / len(owners)
                    reasons[number].append(f"log event '{event}' is described in this runbook")

        ranked = sorted(score, key=lambda n: (-score[n], n))
        best = score[ranked[0]] if ranked else 0.0
        strong = any(r != _WORDING for rs in reasons.values() for r in rs)
        if hints and not strong:  # "forgot my password": keyword overlap alone must not force a runbook
            chosen: list[int] = []
        elif not strong and self._coverage(ticket) < MIN_COVERAGE:  # mostly words no runbook uses: not about this lab
            chosen = []
        else:
            chosen = [n for n in ranked if score[n] >= max(MIN_SCORE, RELATIVE_FLOOR * best)][:k]
        matches = tuple(Match(self.runbooks[n], round(score[n], 3), tuple(dict.fromkeys(reasons[n]))) for n in chosen)
        return Result(matches=matches, hints=tuple(hints), best_raw=round(best, 3))

    def _coverage(self, text: str) -> float:
        wanted = set(terms(text))
        return len(wanted & self._known) / len(wanted) if wanted else 0.0

    def _boost(self, score: dict[int, float], reasons: dict[int, list[str]], scenario: str, why: str) -> None:
        number = self.scenarios[scenario].runbook_number
        if number in score:
            score[number] += SIGNATURE_BOOST
            reasons[number].append(why)
