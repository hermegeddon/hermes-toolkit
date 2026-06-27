"""Regression coverage for skill helper symlinks.

Git stores symlink targets as blob contents. If a script blob is accidentally
committed under mode 120000, Linux/WSL checkout tries to create a symlink whose
"target" is the whole script text and fails with ENAMETOOLONG.
"""

from __future__ import annotations

import os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_TRIAGE = REPO_ROOT / "skills/_shared/hermes-triage.sh"
TRIAGE_WRAPPERS = [
    REPO_ROOT / "skills/hermes-debug/scripts/hermes-triage.sh",
    REPO_ROOT / "skills/hermes-fork-maintainer/scripts/hermes-triage.sh",
]
EXPECTED_TARGET = "../../_shared/hermes-triage.sh"


def test_triage_wrappers_are_relative_symlinks_to_shared_script() -> None:
    assert SHARED_TRIAGE.is_file()

    for wrapper in TRIAGE_WRAPPERS:
        rel = wrapper.relative_to(REPO_ROOT)
        assert wrapper.is_symlink(), f"{rel} must be a real symlink, not a copied script blob"
        assert os.readlink(wrapper) == EXPECTED_TARGET
        assert wrapper.resolve(strict=True) == SHARED_TRIAGE.resolve(strict=True)
