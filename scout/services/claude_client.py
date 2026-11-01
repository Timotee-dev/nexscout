"""
Claude via the Claude Code CLI in headless (print) mode, using your Claude Pro login.

No Anthropic API key is used. We even strip ANTHROPIC_API_KEY from the child
environment so Claude Code can't silently fall back to paid API billing.

    text = ask_claude(prompt, purpose="score", items=5)
"""
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import time

from django.conf import settings

from scout.models import LLMCall, ScoutSettings

LIMIT_MARKERS = ("usage limit", "limit reached", "rate limit", "5-hour limit", "weekly limit", "quota")


STDIN_INSTRUCTION = "Follow the instructions in the piped input exactly. Use no tools. Reply with JSON only."


class ClaudeError(Exception):
    pass


class ClaudeLimitError(ClaudeError):
    """Your Pro usage limit was hit. Stop the run; try later."""


class ClaudeNotFound(ClaudeError):
    pass


def resolve_cli():
    exe = shutil.which(settings.CLAUDE_CLI)
    if not exe:
        raise ClaudeNotFound(
            f"Claude Code CLI '{settings.CLAUDE_CLI}' not found on PATH. Open a new terminal and run "
            "`claude --version`. If that works, set CLAUDE_CLI in .env to the full path "
            "(e.g. C:\\Users\\<you>\\AppData\\Roaming\\npm\\claude.cmd)."
        )
    return exe


def build_command(model):
    # The full prompt goes in on stdin (no command-line length limits, no Windows
    # quoting problems); this short instruction is the documented `cat x | claude -p "query"` form.
    cmd = [resolve_cli(), "-p", STDIN_INSTRUCTION, "--output-format", "json", "--model", model]
    if settings.CLAUDE_EXTRA_ARGS.strip():
        cmd += shlex.split(settings.CLAUDE_EXTRA_ARGS, posix=(os.name != "nt"))
    return cmd


def parse_cli_output(stdout):
    """Claude Code `--output-format json` prints one JSON object with a `result` field."""
    stdout = (stdout or "").strip()
    if not stdout:
        raise ClaudeError("Claude returned nothing")
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        # Older/odd versions may print plain text; accept it.
        return stdout
    if isinstance(data, list):  # stream-ish output: take the final result event
        results = [d for d in data if isinstance(d, dict) and d.get("type") == "result"]
        data = results[-1] if results else {}
    if not isinstance(data, dict):
        raise ClaudeError("Unexpected Claude output")
    result = data.get("result") or ""
    if data.get("is_error") or data.get("subtype", "success") not in ("success", None):
        msg = str(result or data.get("subtype") or "unknown error")
        if any(m in msg.lower() for m in LIMIT_MARKERS):
            raise ClaudeLimitError(msg[:300])
        raise ClaudeError(msg[:300])
    return result


def ask_claude(prompt, purpose, items=1, model=None, timeout=None):
    model = model or ScoutSettings.get_solo().model_name
    timeout = timeout or settings.CLAUDE_TIMEOUT_SECONDS
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)  # force the Pro login, never API billing
    started = time.monotonic()
    call = LLMCall(purpose=purpose, model=model, items=items)
    try:
        cmd = build_command(model)
        # Run in an empty temp dir so Claude Code doesn't read any project files.
        with tempfile.TemporaryDirectory(prefix="nexscout-") as cwd:
            proc = subprocess.run(
                cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=timeout, cwd=cwd, env=env,
            )
        if proc.returncode != 0:
            msg = (proc.stderr or proc.stdout or "").strip()
            low = msg.lower()
            if any(m in low for m in LIMIT_MARKERS):
                raise ClaudeLimitError(msg[:300])
            if "login" in low or "auth" in low:
                raise ClaudeError("Claude Code isn't logged in. Run `claude` once in a terminal and log in with your Pro account.")
            raise ClaudeError(f"Claude CLI exit {proc.returncode}: {msg[:250]}")
        text = parse_cli_output(proc.stdout)
        call.ok = True
        return text
    except subprocess.TimeoutExpired as exc:
        call.ok, call.error = False, f"Timed out after {timeout}s"
        raise ClaudeError(call.error) from exc
    except ClaudeError as exc:
        call.ok, call.error = False, str(exc)[:300]
        raise
    finally:
        call.duration_ms = int((time.monotonic() - started) * 1000)
        call.save()
