"""Thin wrapper around the Claude Code CLI in headless mode (`claude -p`).

Using the CLI instead of the API means the bot runs on the user's Claude subscription and
needs no ANTHROPIC_API_KEY. Plain calls run with no tools at all; the browser agent passes an
MCP config so Claude can drive a real browser (Playwright MCP).
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

from .log import log


class LLMError(RuntimeError):
    pass


def ask(prompt: str, *, model: str = "sonnet", system: str | None = None,
        timeout: int = 900, mcp_config: Path | None = None,
        allowed_tools: list[str] | None = None, max_budget_usd: float | None = None,
        tools: list[str] | None = None) -> str:
    """Run one headless Claude call and return its final text."""
    cmd = ["claude", "-p", "--model", model, "--output-format", "json",
           "--no-session-persistence", "--strict-mcp-config", "--setting-sources", ""]
    if mcp_config:
        cmd += ["--mcp-config", str(mcp_config)]
        cmd += ["--allowedTools", *(allowed_tools or [])]
        cmd += ["--tools", ""]  # no shell / file tools, only the MCP browser
    elif tools:  # built-in tools such as WebSearch; still no shell or file access
        cmd += ["--tools", ",".join(tools), "--allowedTools", *tools]
    else:
        cmd += ["--tools", ""]
    if system:
        cmd += ["--system-prompt", system]
    if max_budget_usd:
        cmd += ["--max-budget-usd", str(max_budget_usd)]
    # Run from an empty dir so no project CLAUDE.md / files leak into the context.
    with tempfile.TemporaryDirectory() as cwd:
        try:
            proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                  timeout=timeout, cwd=cwd)
        except subprocess.TimeoutExpired as e:
            raise LLMError(f"claude timed out after {timeout}s") from e
    if proc.returncode != 0 and not proc.stdout.strip():
        raise LLMError(f"claude exited {proc.returncode}: {proc.stderr[-800:]}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise LLMError(f"unparsable claude output: {proc.stdout[-800:]}") from e
    if data.get("is_error"):
        raise LLMError(f"claude error: {data.get('result') or data}")
    cost = data.get("total_cost_usd")
    if cost:
        log.debug("claude %s call cost $%.3f", model, cost)
    return data.get("result", "")


def extract_json(text: str):
    """Pull the first JSON object/array out of a model reply (tolerates ```json fences)."""
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    start = min([i for i in (text.find("{"), text.find("[")) if i != -1], default=-1)
    if start == -1:
        raise LLMError(f"no JSON in reply: {text[:300]}")
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    end = text.rfind(closer)
    return json.loads(text[start:end + 1])


def ask_json(prompt: str, **kw):
    reply = ask(prompt, **kw)
    try:
        return extract_json(reply)
    except (json.JSONDecodeError, LLMError):
        # one repair attempt
        fixed = ask("Return ONLY valid JSON, no prose, for this content:\n\n" + reply,
                    model="haiku", timeout=300)
        return extract_json(fixed)


def between(text: str, tag: str) -> str | None:
    """Extract a block delimited by <<<TAG>>> ... <<<END TAG>>>."""
    m = re.search(rf"<<<{tag}>>>\s*(.*?)\s*<<<END {tag}>>>", text, re.S)
    return m.group(1).strip() if m else None
