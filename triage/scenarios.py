"""Read the lab's scenario metadata without running or importing any of it.

Each scenarios/<name>.sh starts with plain SCENARIO_X="value" lines. They are
parsed as text, so this stays in step with the lab as scenarios are added.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path

_ASSIGN = re.compile(r"^(SCENARIO_[A-Z_]+)=(.*)$")
_RUNBOOK_NUMBER = re.compile(r"(\d+)-")


@dataclass(frozen=True)
class Scenario:
    name: str
    summary: str
    runbook: str  # path relative to the lab, e.g. runbooks/15-redirect-loop.md
    app: str  # the tests container's target, or "radius"
    expect: str  # text the user sees when it is broken
    log_service: str  # compose service whose log shows it ("" if none)
    log_pattern: str  # extended regex for the line that proves it, without the service ("" if none)
    unaffected: str  # "<app> <user>" still working while broken ("" if none)

    @property
    def runbook_number(self) -> int:
        match = _RUNBOOK_NUMBER.match(Path(self.runbook).name)
        return int(match.group(1)) if match else 0


def _value(raw: str) -> str:
    try:
        parts = shlex.split(raw, comments=True)
    except ValueError:
        return raw.strip().strip("\"'")
    return parts[0] if parts else ""


def split_log(value: str) -> tuple[str, str]:
    """SCENARIO_LOG is '<service> <pattern>': the lab greps that service's log for the pattern."""
    service, _, pattern = value.partition(" ")
    return (service, pattern) if pattern else ("", value)


def parse_scenario(path: Path) -> Scenario:
    found: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _ASSIGN.match(line)
        if match:
            found[match.group(1)] = _value(match.group(2))
    missing = [k for k in ("SCENARIO_SUMMARY", "SCENARIO_RUNBOOK", "SCENARIO_APP", "SCENARIO_EXPECT") if not found.get(k)]
    if missing:
        raise ValueError(f"{path.name}: missing {', '.join(missing)}")
    service, pattern = split_log(found.get("SCENARIO_LOG", ""))
    return Scenario(
        name=path.stem,
        summary=found["SCENARIO_SUMMARY"],
        runbook=found["SCENARIO_RUNBOOK"],
        app=found["SCENARIO_APP"],
        expect=found["SCENARIO_EXPECT"],
        log_service=service,
        log_pattern=pattern,
        unaffected=found.get("SCENARIO_UNAFFECTED", ""),
    )


def load_scenarios(lab_dir: Path) -> dict[str, Scenario]:
    folder = lab_dir / "scenarios"
    if not folder.is_dir():
        raise FileNotFoundError(f"no scenarios folder in the lab at {folder}")
    return {p.stem: parse_scenario(p) for p in sorted(folder.glob("*.sh"))}
