"""Offline checks (no Claude calls, no LaTeX): run with `python -m pytest tests` or `python tests/test_basics.py`."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from applyai.context import SECRET, claude_code_threads
from applyai.tailor import _ensure_skills, _restore_kept, _validate_body
from applyai.salary import find as find_salary
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


def test_salary():
    assert find_salary("The base salary range is $120,000 - $160,000 per year.")["min"] == 120000
    assert find_salary("Gehalt: 60.000 € bis 75.000 € brutto")["currency"] == "EUR"
    assert find_salary("CHF 100'000 - 120'000 salary")["max"] == 120000
    assert find_salary("Pay: $55 - $70 per hour")["min"] == 114400
    assert find_salary("We raised $40M in our Series B") is None


def test_dashboard_builds():
    from types import SimpleNamespace
    from applyai import dashboard
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "state").mkdir()
        (root / "logs").mkdir()
        (root / "logs" / "2026-11-20_0730_run.log").write_text("07:30 ERROR   Stopping: no internet connection\n")
        folder = root / "Germany" / "Munich" / "Acme" / "Engineer"
        folder.mkdir(parents=True)
        (folder / "CV_A_B_Acme.pdf").write_text("x")
        store = {"k1": {"status": "tailored", "first_seen": "2026-11-20", "fit": 8, "ats": 81, "title": "Engineer",
                        "company": "Acme", "city": "Munich", "url": "https://jobs.lever.co/acme/1", "folder": str(folder),
                        "job": {"country": "Germany", "description": "Salary: 60.000 € - 70.000 €", "source": "lever"}},
                 "k2": {"status": "rejected", "first_seen": "2026-11-20", "fit": 3}}
        ws = SimpleNamespace(root=root, state=root / "state", logs=root / "logs")
        out = dashboard.write(ws, store, {"k1": {"status": "applied", "date": "2026-11-21", "salary": "EUR 65k"}}, {})
        html = out.read_text()
        data = dashboard.build_data(ws, store, {"k1": {"status": "applied"}}, {})
        assert data["apps"][0]["docs"]["cv"] == "Germany/Munich/Acme/Engineer/CV_A_B_Acme.pdf"
        assert data["apps"][0]["via"] == "Lever" and data["apps"][0]["salary"]["min"] == 60000
        assert data["runs"][0]["ok"] is False and "no internet" in data["runs"][0]["error"]
        assert "/*__DATA__*/" not in html and "</script>" in html


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
