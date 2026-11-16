"""Run the analysis with Claude Code or an API provider and return the facts as a dict."""

import json
import logging
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from deal_scout import providers, usage
from deal_scout.config import Settings
from deal_scout.domain import Domain
from deal_scout.errors import ProcessError
from deal_scout.prompts import Listing, api_request, prompt, schema

log = logging.getLogger("dealscout")


def claude_command(nnn: str, model: str, d: Domain, claude_bin: str = "claude") -> list[str]:
    """Build the headless Claude Code call: reads the project, writes only into listings/, no shell, no web."""
    return [
        claude_bin,
        "-p",
        prompt(nnn, d),
        "--model",
        model,
        "--tools",
        "Read,Write,Edit,Glob",
        "--allowedTools",
        "Read",
        "Glob",
        "Write(./listings/**)",
        "Edit(./listings/**)",
        "--permission-mode",
        "dontAsk",
        "--restricted",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(schema(d)),
    ]


def run_claude(
    root: Path | str,
    nnn: str,
    model: str,
    d: Domain,
    settings: Settings,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    timeout: int = 900,
) -> Listing:
    """Run the headless Claude Code command and return the structured card it produced."""
    try:
        env = dict(settings.claude_env)  # without the bot token and Anthropic keys, see config.child_env
        command = claude_command(nnn, model, d, claude_bin=settings.claude_bin)
        proc = runner(command, cwd=str(root), env=env, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise ProcessError("Claude Code (`claude`) is not installed or not on PATH; set CLAUDE_BIN in .env") from exc
    except subprocess.TimeoutExpired as exc:
        raise ProcessError(f"Claude took longer than {timeout} s") from exc
    try:
        out = json.loads(proc.stdout)
    except ValueError as exc:
        log.error("#%s claude exit %s, no JSON. stderr: %s", nnn, proc.returncode, (proc.stderr or "")[-2000:])
        usage.record(root, int(nnn), {}, ok=False)
        raise ProcessError(f"Claude failed (exit {proc.returncode}): {(proc.stderr or proc.stdout)[-300:]}") from exc
    # Only metadata goes to the log, never the listing content (names, phone numbers).
    log.info(
        "#%s claude: %s, %.0f s, %s turns, est. $%.2f, %d permission denial(s) %s",
        nnn,
        out.get("subtype"),
        (out.get("duration_ms") or 0) / 1000,
        out.get("num_turns"),
        out.get("total_cost_usd") or 0,
        len(out.get("permission_denials") or []),
        [d.get("tool_name") for d in out.get("permission_denials") or []],
    )
    ok = not out.get("is_error") and isinstance(out.get("structured_output"), dict)
    try:
        usage.record(root, int(nnn), out, ok)
    except OSError:
        log.exception("#%s usage not recorded", nnn)
    if not ok:
        log.error("#%s claude error: %s", nnn, str(out.get("result") or out.get("subtype"))[:2000])
        raise ProcessError(f"Claude failed: {str(out.get('result') or out.get('subtype'))[:300]}")
    return cast(Listing, out["structured_output"])


def analyse(
    root: Path | str,
    nnn: str,
    d: Domain,
    settings: Settings,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    client: Any = None,
) -> Listing:
    """Run the analysis with PROVIDER from .env and return the data (API providers add card_markdown)."""
    try:
        provider = providers.name(settings)
        model = providers.model_for(provider, settings)
    except providers.ProviderError as exc:
        raise ProcessError(str(exc)) from exc
    if provider == "claude-code":
        return run_claude(root, nnn, model, d, settings, runner=runner)
    system, user_text, photos = api_request(root, nnn, d)
    try:
        data, stats = providers.call(
            provider,
            model,
            system,
            user_text,
            providers.image_blocks(photos),
            schema(d, card=True),
            settings,
            client=client,
        )
    except Exception as exc:  # ProviderError or an SDK error (auth, rate limit, network)
        usage.record_api(root, int(nnn), False, provider, model, {})
        raise ProcessError(f"{provider}: {exc}") from exc
    if stats.get("cost_usd") is None:
        stats["cost_usd"] = providers.price(stats["input_tokens"], stats["output_tokens"], settings.price_per_mtok)
    usage.record_api(root, int(nnn), True, provider, model, stats)
    return data
