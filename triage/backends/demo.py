"""Demo mode: replay saved answers, so reviewers need neither a key nor a GPU.

Answers are saved by `triage ticket --record` after a real run, one file per
request, keyed by the ticket, the logs and the retrieved runbooks (not by the
prompt wording, so editing the prompt does not orphan them). Clarify-mode answers
are built by code and need no recording.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from ..config import REPO_ROOT
from ..prompt import Budget, Prompt
from .base import Backend, NoRecordedAnswer

DEMO_DIR = REPO_ROOT / "demo" / "answers"


def key_for(prompt: Prompt) -> str:
    return hashlib.sha256(prompt.key_material.encode()).hexdigest()[:24]


class DemoBackend(Backend):
    name = "demo"
    model = "recorded"
    offline = True

    def __init__(self, guard, budget: Budget, directory: Path = DEMO_DIR):
        super().__init__(guard, budget)
        self.directory = directory

    def can_replay(self, prompt: Prompt) -> bool:
        return (self.directory / f"{key_for(prompt)}.json").is_file()

    def _complete(self, prompt: Prompt, schema: dict) -> dict:
        path = self.directory / f"{key_for(prompt)}.json"
        if not path.is_file():
            raise NoRecordedAnswer(
                "Demo mode has no saved answer for this input. Run it once with BACKEND=claude or ollama and --record, "
                "or try one of the saved examples.")
        return json.loads(path.read_text(encoding="utf-8"))["answer"]

    def record(self, prompt: Prompt, raw: dict, backend: str, model: str) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{key_for(prompt)}.json"
        meta = {"recorded": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "backend": backend, "model": model}
        path.write_text(json.dumps({"meta": meta, "answer": raw}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return path
