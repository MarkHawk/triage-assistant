"""Nothing personal in committed files.

Always checked: home paths, em dashes, and IPv4 addresses outside the lab's own
fictional ranges. Extra terms (your username, employer, host names) go one per
line in a gitignored .private-terms file, so the repo never has to contain them.
"""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
IPV4 = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?![\d.])")
# loopback, the lab's compose subnets, and the documentation ranges
ALLOWED_IP = re.compile(r"^(127\.|0\.0\.0\.0$|172\.30\.18\d\.|192\.0\.2\.|198\.51\.100\.|203\.0\.113\.)")
HOME_PATH = re.compile(r"(?:/home/|/Users/|C:\\Users\\)[A-Za-z0-9_.-]+")


def tracked_text_files():
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                         cwd=ROOT, capture_output=True, check=True).stdout.decode()
    for name in filter(None, out.split("\0")):
        path = ROOT / name
        if path.is_file() and path.name != "test_repo_clean.py":
            try:
                yield name, path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue


def private_terms():
    f = ROOT / ".private-terms"
    if not f.is_file():
        return []
    return [t.strip() for t in f.read_text().splitlines() if t.strip() and not t.startswith("#")]


FILES = list(tracked_text_files())


@pytest.mark.parametrize("name,text", FILES, ids=[n for n, _ in FILES])
def test_file_is_clean(name, text):
    assert "\u2014" not in text, f"{name}: em dash"
    assert not HOME_PATH.search(text), f"{name}: home path"
    bad = [ip for ip in IPV4.findall(text) if not ALLOWED_IP.match(ip)]
    assert not bad, f"{name}: address(es) outside the lab's ranges: {bad}"
    lowered = text.lower()
    # LICENSE carries the same copyright holder line as identity-lab, on purpose.
    hits = [] if name == "LICENSE" else [t for t in private_terms() if t.lower() in lowered]
    assert not hits, f"{name}: contains {len(hits)} term(s) from .private-terms"
