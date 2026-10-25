"""Batch behaviour in the editor and library pages.

The checks run the page scripts. pytest has no browser, so they go through node.
"""

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_editor_batch_page():
    node = shutil.which('node')
    assert node, 'node is required to run the editor page checks'
    result = subprocess.run(
        [node, 'tests/editor_page.test.mjs'],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
