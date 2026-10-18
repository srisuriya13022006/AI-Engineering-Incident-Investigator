"""
Frontend validation tests for Milestone 10:
- Verifies JavaScript syntax via node --check
- Verifies every onclick handler in index.html is defined in app.js
- Verifies Cytoscape.js vendored asset is present and loadable
"""

import os
import re
import subprocess
import pytest


def test_app_js_syntax():
    """Runs 'node --check app/static/app.js' to ensure zero syntax errors."""
    res = subprocess.run(
        ["node", "--check", "app/static/app.js"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"node --check failed: {res.stderr}"


def test_cytoscape_vendored_exists():
    """Confirms cytoscape.min.js exists in app/static/."""
    assert os.path.exists("app/static/cytoscape.min.js")
    size = os.path.getsize("app/static/cytoscape.min.js")
    assert size > 100_000, f"cytoscape.min.js is too small ({size} bytes)"


def test_all_html_onclick_handlers_exist_in_js():
    """Extracts all onclick handler names from index.html and asserts they exist in app.js."""
    with open("app/static/index.html", "r", encoding="utf-8") as f:
        html = f.read()

    with open("app/static/app.js", "r", encoding="utf-8") as f:
        js = f.read()

    # Find functions called in onclick="funcName(...)
    onclick_matches = re.findall(r'onclick="([a-zA-Z0-9_]+)\(', html)
    unique_handlers = set(onclick_matches)

    assert len(unique_handlers) > 0, "No onclick handlers found in index.html"

    missing = []
    for handler in sorted(unique_handlers):
        # Allow inline document expressions if any
        if handler == "document":
            continue
        # Function pattern in JS: function handler( or const handler = or let handler =
        fn_pattern = rf"(function\s+{handler}\s*\(|const\s+{handler}\s*=|let\s+{handler}\s*=|window\.{handler}\s*=)"
        if not re.search(fn_pattern, js):
            missing.append(handler)

    assert missing == [], f"Found onclick handlers in index.html missing from app.js: {missing}"
