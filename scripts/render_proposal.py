"""Render docs/proposal/milestone1-proposal.md to HTML and (if Chrome/Chromium is found) PDF.

Usage:  uv run --extra dev python scripts/render_proposal.py
The course requires a 1-2 page PDF; the script prints the page count.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs" / "proposal" / "milestone1-proposal.md"
HTML = SRC.with_suffix(".html")
PDF = SRC.with_suffix(".pdf")
TITLE = "Resilience Gap: Federal Disaster Declarations and Mitigation Obligations"

CSS = """
@page { size: Letter; margin: 0.55in 0.6in; }
body { font: 9.5pt/1.29 "Helvetica Neue", Arial, sans-serif; color: #111; }
h1 { font-size: 14.5pt; margin: 0 0 2pt; line-height: 1.2; }
h2 { font-size: 10.8pt; margin: 7pt 0 2pt; color: #1c5cab; }
p, li { margin: 0 0 3pt; } ol, ul { margin: 0 0 3pt; padding-left: 16pt; }
table { border-collapse: collapse; width: 100%; margin: 3pt 0 4pt; font-size: 8.8pt; }
th, td { border-bottom: 0.5pt solid #ccc; padding: 1.5pt 4pt; text-align: left;
         vertical-align: top; }
thead th { background: #eef3fa; } em { color: #333; }
"""

CHROME_CANDIDATES = [
    os.environ.get("CHROME", ""),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    shutil.which("google-chrome") or "",
    shutil.which("chromium") or "",
    shutil.which("chromium-browser") or "",
]


def main() -> int:
    body = markdown.markdown(SRC.read_text(encoding="utf-8"), extensions=["tables"])
    HTML.write_text(
        f"<!doctype html><html><head><meta charset='utf-8'><title>{TITLE}</title>"
        f"<style>{CSS}</style></head>"
        f"<body>{body}</body></html>",
        encoding="utf-8",
    )
    print(f"wrote {HTML.relative_to(ROOT)}")
    chrome = next((c for c in CHROME_CANDIDATES if c and Path(c).exists()), None)
    if not chrome:
        print("Chrome/Chromium not found: open the HTML file and print to PDF (Letter).")
        return 0
    subprocess.run(
        [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-pdf-header-footer",
            f"--print-to-pdf={PDF}",
            HTML.as_uri(),
        ],
        check=True,
        capture_output=True,
    )
    from pypdf import PdfReader

    pages = len(PdfReader(PDF).pages)
    print(f"wrote {PDF.relative_to(ROOT)} ({pages} page{'s' if pages != 1 else ''})")
    if not 1 <= pages <= 2:
        print("WARNING: Milestone 1 must be 1-2 pages", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
