"""Collect career context from the user's earlier Claude conversations, for the setup.

Sources (all local files; nothing is uploaded anywhere):
- Claude Code session transcripts: ~/.claude/projects/<project>/<session>.jsonl
- Claude Code memory files: ~/.claude/projects/<project>/memory/*.md and ~/.claude/CLAUDE.md
- optionally a claude.ai data export (Settings > Privacy > Export data), passed as the
  unzipped folder, the .zip or its conversations.json

The transcript format is internal to Claude Code and may change, so parsing is defensive: a
record that doesn't look like a user/assistant message is skipped.

The output is a Markdown digest: per project / conversation the dates, the user's own requests
and Claude's closing summaries, ranked by how much they say about the user's work. The setup
skill reads it and proposes fact-bank entries, which the user confirms one by one.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

CAREER = re.compile(
    r"\b(built|build|designed|developed|implemented|wrote|published|paper|thesis|project|prototype|"
    r"experiment|measur|lab\b|internship|intern\b|job|work(ed|ing)? (at|for|on)|team|lead|led|"
    r"launched|shipped|customer|startup|founded|research|award|grant|patent|course|degree|"
    r"university|certificat|cv\b|resume|résumé|lebenslauf|bewerbung|application|interview|"
    r"salary|skill|python|c\+\+|matlab|cad|pcb|firmware|robot|optic|laser|data|model)", re.I)
SECRET = re.compile(r"(sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|gh[ousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|"
                    r"xox[abp]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
                    r"(?i:password|passwd|token|secret|api[_-]?key)\s*[:=]\s*\S+)")
NOISE = re.compile(r"^<(command-|local-command|system-reminder|task-notification|bash-|user-prompt)", re.I)


@dataclass
class Thread:
    source: str                 # "claude-code" | "claude.ai" | "memory"
    title: str
    first: str = ""
    last: str = ""
    asks: list[str] = field(default_factory=list)      # the user's own messages
    summaries: list[str] = field(default_factory=list)  # Claude's longer replies
    score: float = 0.0


def _clean(text: str, limit: int) -> str:
    text = SECRET.sub("[redacted]", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def _texts(content) -> list[str]:
    if isinstance(content, str):
        return [content]
    out = []
    for c in content or []:
        if isinstance(c, dict) and c.get("type") == "text" and isinstance(c.get("text"), str):
            out.append(c["text"])
    return out


def claude_code_threads(root: Path, skip: list[Path]) -> list[Thread]:
    threads = []
    for f in sorted(root.glob("*/*.jsonl")):
        t = Thread("claude-code", f.parent.name)
        last_reply = ""
        try:
            lines = f.open(encoding="utf-8", errors="replace")
        except OSError:
            continue
        with lines:
            for line in lines:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                kind = d.get("type")
                if kind == "custom-title" and d.get("customTitle"):
                    t.title = str(d["customTitle"])
                if kind not in ("user", "assistant") or d.get("isSidechain") or d.get("isMeta"):
                    continue
                cwd = d.get("cwd") or ""
                if cwd and any(str(cwd).startswith(str(s)) for s in skip):
                    t.asks = []
                    break  # ApplyAI's own sessions add nothing new
                if cwd and t.title == f.parent.name:
                    t.title = cwd.replace(str(Path.home()), "~")
                ts = (d.get("timestamp") or "")[:10]
                if ts:
                    t.first = t.first or ts
                    t.last = ts
                for text in _texts((d.get("message") or {}).get("content")):
                    if NOISE.match(text.strip()):
                        continue
                    if kind == "user" and len(text.strip()) > 15:
                        t.asks.append(_clean(text, 400))
                    elif kind == "assistant" and len(text) > 300:
                        last_reply = text
            if last_reply:
                t.summaries.append(_clean(last_reply, 900))
        if t.asks:
            threads.append(t)
    return threads


def memory_threads(root: Path) -> list[Thread]:
    out = []
    files = list(root.glob("*/memory/*.md")) + [root.parent / "CLAUDE.md"]
    for f in files:
        if not f.exists() or f.name == "MEMORY.md":
            continue
        text = f.read_text(errors="replace")
        t = Thread("memory", f"{f.parent.parent.name}/{f.name}" if f.parent.name == "memory" else str(f))
        t.summaries = [_clean(text, 1500)]
        t.last = dt.date.fromtimestamp(f.stat().st_mtime).isoformat()
        out.append(t)
    return out


def claude_ai_threads(path: Path) -> list[Thread]:
    """claude.ai export: a list of conversations with `name`, `created_at` and `chat_messages`
    (each with `sender` human/assistant and `text` or `content`)."""
    raw = None
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as z:
            name = next((n for n in z.namelist() if n.endswith("conversations.json")), None)
            raw = z.read(name) if name else None
    else:
        f = path / "conversations.json" if path.is_dir() else path
        raw = f.read_bytes() if f.exists() else None
    if not raw:
        raise SystemExit(f"No conversations.json found in {path}")
    out = []
    for c in json.loads(raw):
        if not isinstance(c, dict):
            continue
        t = Thread("claude.ai", c.get("name") or "(untitled chat)",
                   first=(c.get("created_at") or "")[:10], last=(c.get("updated_at") or c.get("created_at") or "")[:10])
        last_reply = ""
        for m in c.get("chat_messages") or []:
            text = m.get("text") or " ".join(_texts(m.get("content")))
            if m.get("sender") == "human" and len(text.strip()) > 15:
                t.asks.append(_clean(text, 400))
            elif m.get("sender") == "assistant" and len(text) > 300:
                last_reply = text
        if last_reply:
            t.summaries.append(_clean(last_reply, 900))
        if t.asks:
            out.append(t)
    return out


def _score(t: Thread, terms: re.Pattern | None) -> float:
    text = " ".join(t.asks + t.summaries)
    s = len(CAREER.findall(text)) ** 0.5 + min(len(t.asks), 30) ** 0.5
    if terms:
        s += 3 * len(terms.findall(text)) ** 0.5
    if t.source == "memory":
        s += 4  # memory files are already condensed facts
    try:  # recent work counts more
        age = (dt.date.today() - dt.date.fromisoformat(t.last)).days
        s *= 1.3 if age < 120 else 1.0 if age < 365 else 0.8
    except ValueError:
        pass
    return s


def digest(*, claude_dir: Path, export: Path | None = None, terms: list[str] | None = None,
           skip: list[Path] | None = None, max_chars: int = 60000, include_code: bool = True) -> tuple[str, dict]:
    threads: list[Thread] = []
    projects = claude_dir / "projects"
    if include_code and projects.exists():
        threads += claude_code_threads(projects, skip or [])
        threads += memory_threads(projects)
    if export:
        threads += claude_ai_threads(export)
    rx = re.compile("|".join(re.escape(t) for t in terms if t), re.I) if terms else None
    for t in threads:
        t.score = _score(t, rx)
    threads.sort(key=lambda t: -t.score)
    stats = {"claude_code": sum(t.source == "claude-code" for t in threads),
             "memory": sum(t.source == "memory" for t in threads),
             "claude_ai": sum(t.source == "claude.ai" for t in threads)}
    out = ["# Context from earlier Claude conversations", "",
           "Raw material for the fact bank. Secrets that look like keys or passwords are redacted.",
           "Nothing here goes into a CV unless the user confirms it.", "",
           f"Found: {stats['claude_code']} Claude Code projects/sessions, {stats['memory']} memory files, "
           f"{stats['claude_ai']} claude.ai chats. Most relevant first.", ""]
    size = sum(len(x) for x in out)
    for i, t in enumerate(threads):
        when = f"{t.first} – {t.last}" if t.first and t.first != t.last else t.last
        block = [f"## [{t.source}] {t.title}" + (f" ({when})" if when else "")]
        if t.asks:
            block.append("**User asked:**")
            block += [f"- {a}" for a in t.asks[:8]]
            if len(t.asks) > 8:
                block.append(f"- … {len(t.asks) - 8} more messages")
        if t.summaries:
            block.append("**Claude / notes:**")
            block += [f"> {x}" for x in t.summaries[-2:]]
        text = "\n".join(block) + "\n"
        if size + len(text) > max_chars:
            out.append(f"\n_(cut here: {len(threads) - i} more, less relevant threads not shown; "
                       "raise --max-chars to see them)_")
            break
        out.append(text)
        size += len(text)
    return "\n".join(out) + "\n", stats
