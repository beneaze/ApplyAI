"""Offline checks (no Claude calls, no LaTeX): run with `python -m pytest tests` or `python tests/test_basics.py`."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from applyai.context import SECRET, claude_code_threads
from applyai.tailor import _ensure_skills, _restore_kept, _validate_body
from applyai.util import normalize_location

LOCS = [{"name": "Munich", "country": "Germany"}, {"name": "Lyon", "country": "France", "aliases": ["villeurbanne"]}]

BASE = r"""\begin{rSection}{Education}
\begin{rSubsection}{EPFL}{Feb 2022 -- Jul 2022}{Semester Abroad}{Lausanne, Switzerland}
\item Specialization: Optoelectronics
\end{rSubsection}
\begin{rSubsection}{Humboldt University}{Oct 2018 -- Sep 2021}{B.Sc. Physics}{Berlin, Germany}
\item Thesis on lasers
\end{rSubsection}
\end{rSection}
\begin{rSection}{Skills}
\begin{tabular}{ @{} >{\bfseries}l @{\hspace{4ex}} l }
Software & Python, C++ \\
Languages & German, English \\
\end{tabular}
\end{rSection}"""


def test_locations():
    assert normalize_location("Garching bei München, Bayern", LOCS) == ("Germany", "Munich")
    assert normalize_location("Villeurbanne, France", LOCS) == ("France", "Lyon")
    assert normalize_location("Munich, North Dakota", LOCS) is None
    assert normalize_location("Remote (EU)", LOCS) is None
    assert normalize_location("Remote (EU)", LOCS, remote=True) == ("Remote", "Remote")


def test_restore_dropped_entry():
    body = BASE.replace(BASE[BASE.index(r"\begin{rSubsection}{EPFL}"):BASE.index(r"\begin{rSubsection}{Humboldt")], "")
    out = _restore_kept(body, BASE, ["EPFL"])
    assert "EPFL" in out and out.index("EPFL") < out.index("Humboldt")


def test_restore_bullets_of_bare_entry():
    body = BASE.replace(r"""\begin{rSubsection}{EPFL}{Feb 2022 -- Jul 2022}{Semester Abroad}{Lausanne, Switzerland}
\item Specialization: Optoelectronics
\end{rSubsection}""", r"\rEntry{EPFL}{Feb 2022 -- Jul 2022}{Semester Abroad}{Lausanne, Switzerland}")
    assert "Specialization" in _restore_kept(body, BASE, ["EPFL"])


def test_keep_entry_not_in_base():
    item = {"match": "Acme Robotics", "section": "Education", "position": "first",
            "latex": "\\begin{rSubsection}{Acme Robotics}{Sep 2026 -- Present}{Engineer}{Munich, Germany}\n\\item Built things\n\\end{rSubsection}"}
    out = _restore_kept(BASE, BASE, [item])
    assert out.index("Acme Robotics") < out.index("EPFL")


def test_ensure_skills_and_tabularx():
    out = _validate_body(BASE, BASE, {"tailoring": {"ensure_skills": {"Software": "agentic coding (Claude Code)"}}})
    assert "Python, C++, agentic coding (Claude Code)" in out
    assert "tabularx" in out
    assert "Claude Code" in _ensure_skills(BASE, {"Tools": "Claude Code"})


def test_secret_redaction():
    assert "[redacted]" in SECRET.sub("[redacted]", "token: sk-ant-abcdefghijklmnopqrstuvwxyz123")
    assert SECRET.sub("[redacted]", "I built a laser lock") == "I built a laser lock"


def test_transcript_parsing():
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d) / "-home-me-robot"
        proj.mkdir()
        rows = [{"type": "user", "cwd": "/home/me/robot", "timestamp": "2026-10-01T10:00:00Z",
                 "message": {"role": "user", "content": "I built a calibration rig for our robot arm, please document it"}},
                {"type": "assistant", "cwd": "/home/me/robot", "timestamp": "2026-10-01T10:05:00Z",
                 "message": {"role": "assistant", "content": [{"type": "text", "text": "Documented. " * 40}]}},
                {"type": "user", "isSidechain": True, "message": {"content": "subagent noise that should be skipped"}}]
        (proj / "s.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
        threads = claude_code_threads(Path(d), skip=[])
        assert len(threads) == 1 and threads[0].first == "2026-10-01"
        assert threads[0].asks == ["I built a calibration rig for our robot arm, please document it"]
        assert threads[0].summaries


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
