"""Where a user's ApplyAI data lives.

The code (this repo / the Claude plugin) and the user's data are kept apart. The data is one
folder, the *workspace* (default ``~/Job Applications``):

    config.yaml          what to search for, where, sources, models
    profile/             facts.md, cv_base.tex, letter_examples.md, cv_rules.md, answers.md,
                         optional photo.jpg / signature.png / resume.cls (own design)
    <Country>/<City>/<Company>/<Position>/   one folder per tailored application
    _Overview.md, _Tracker.csv
    .applyai/state, .applyai/logs

``APPLYAI_HOME`` overrides the location; ``applyai init PATH`` remembers it in
``~/.config/applyai/workspace``.
"""
from __future__ import annotations

import os
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
REPO = PACKAGE.parent
TEMPLATES = PACKAGE / "templates"
EXAMPLES = PACKAGE / "examples"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser() / "applyai"
POINTER = CONFIG_DIR / "workspace"
TOKEN_FILE = CONFIG_DIR / "claude_token"
DEFAULT = Path("~/Job Applications").expanduser()


def home() -> Path:
    env = os.environ.get("APPLYAI_HOME")
    if env:
        return Path(env).expanduser()
    if POINTER.exists() and POINTER.read_text().strip():
        return Path(POINTER.read_text().strip()).expanduser()
    return DEFAULT


class Workspace:
    def __init__(self, root: Path | None = None):
        self.root = (root or home()).expanduser().resolve()

    @property
    def config(self) -> Path:
        return self.root / "config.yaml"

    @property
    def profile(self) -> Path:
        return self.root / "profile"

    @property
    def state(self) -> Path:
        p = self.root / ".applyai" / "state"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def logs(self) -> Path:
        p = self.root / ".applyai" / "logs"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def exists(self) -> bool:
        return self.config.exists()

    def missing_profile(self) -> list[str]:
        need = ["facts.md", "cv_base.tex"]
        return [n for n in need if not (self.profile / n).exists()]
