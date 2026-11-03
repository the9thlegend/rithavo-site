"""
A stray control character (for example a backspace where a regular-expression "\b" was meant) silently changes what code does.
No tracked source or content file may contain a control byte other than tab, line feed and carriage return.
"""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUFFIXES = {".py", ".js", ".css", ".html", ".json", ".md", ".webmanifest", ".txt", ".toml", ".yml", ".yaml"}


def tracked_files():
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, cwd=ROOT, timeout=60)
    return [ROOT / line for line in out.stdout.splitlines() if (ROOT / line).suffix.lower() in SUFFIXES and (ROOT / line).is_file()]


def test_no_tracked_source_file_contains_a_control_byte():
    bad = {}
    for p in tracked_files():
        data = p.read_bytes()
        hits = [i for i, b in enumerate(data) if b < 32 and b not in (9, 10, 13)]
        if hits:
            bad[p.relative_to(ROOT).as_posix()] = hits[:3]
    assert bad == {}


def test_the_guard_would_catch_one(tmp_path):
    f = tmp_path / "x.js"
    f.write_bytes(b"var r = /" + bytes([8]) + b"word" + bytes([8]) + b"/;")
    assert [i for i, b in enumerate(f.read_bytes()) if b < 32 and b not in (9, 10, 13)] == [9, 14]
