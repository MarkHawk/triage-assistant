"""The fictional company's names for the lab's components."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .config import REPO_ROOT

_NORTHGATE = re.compile(r"Northgate [A-Z][\w]*(?: [A-Z][\w]*)*")
# lab app folder -> key in data/names.yaml
_LAB_APPS = {"saml-sp": "staff-portal", "oidc-rp": "expenses"}


@dataclass(frozen=True)
class System:
    key: str
    display: str
    lab_component: str
    protocol: str
    ports: tuple[int, ...]
    staff_say: tuple[str, ...]


def lab_template_names(lab_dir: Path) -> dict[str, str]:
    """Display names the lab's own templates use, if any: {names.yaml key: name}."""
    found: dict[str, str] = {}
    for folder, key in _LAB_APPS.items():
        for template in sorted((lab_dir / "apps" / folder / "templates").glob("*.html")):
            match = _NORTHGATE.search(template.read_text(encoding="utf-8"))
            if match:
                found[key] = match.group(0)
                break
    return found


def load_names(lab_dir: Path | None = None, path: Path | None = None) -> tuple[str, dict[str, System]]:
    """(company, systems). Names from the lab's templates win over data/names.yaml."""
    data = yaml.safe_load((path or REPO_ROOT / "data" / "names.yaml").read_text(encoding="utf-8"))
    preferred = lab_template_names(lab_dir) if lab_dir else {}
    systems: dict[str, System] = {}
    for key, item in data["systems"].items():
        systems[key] = System(
            key=key,
            display=preferred.get(key, item["display"]),
            lab_component=item["lab_component"],
            protocol=item.get("protocol", ""),
            ports=tuple(item.get("ports", ())),
            staff_say=tuple(item.get("staff_say", ())),
        )
    return data["company"], systems
