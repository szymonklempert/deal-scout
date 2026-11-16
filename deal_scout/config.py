"""Settings from the environment and the .env file, read once at start-up and passed to whoever needs them.

Entry points call load_settings() (data_home(), load_env(), Settings.from_env()); everything else receives the Settings
object. Nothing else in deal_scout/ reads or writes os.environ (the active domain's name is Settings.domain).
Standard library only.

Two places matter: the code (PACKAGE_DIR, where the package is installed) and the data home (data_home(), where the
owner's .env, .state.json, inbox/, listings/, logs/ and domains/ live).
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent  # where the code is, wherever it is installed
DEFAULT_DOMAIN = "campervan"
SECRET_PREFIXES = ("TELEGRAM_",)
SECRET_KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def in_checkout(package_dir: Path = PACKAGE_DIR) -> bool:
    """Whether the package runs from a source checkout (deal_scout/ next to pyproject.toml)."""
    return package_dir.name == "deal_scout" and (package_dir.parent / "pyproject.toml").is_file()


def data_home(
    environ: Mapping[str, str] | None = None, *, package_dir: Path = PACKAGE_DIR, user_home: Path | None = None
) -> Path:
    """Return the folder with the owner's data.

    In order: DEAL_SCOUT_HOME, the legacy CAMPERVAN_ROOT, the source checkout, ~/.deal-scout.

    Both variables come from the real environment, not .env (the .env file lives inside this folder).
    """
    env = os.environ if environ is None else environ
    for key in ("DEAL_SCOUT_HOME", "CAMPERVAN_ROOT"):
        if value := (env.get(key) or "").strip():
            return Path(value).expanduser().resolve()
    if in_checkout(package_dir):
        return package_dir.parent
    return (user_home or Path.home()) / ".deal-scout"


def is_trial_home(environ: Mapping[str, str] | None = None) -> bool:
    """Whether the legacy CAMPERVAN_ROOT points a run at some other folder than the usual data home."""
    env = os.environ if environ is None else environ
    if (env.get("DEAL_SCOUT_HOME") or "").strip() or not (env.get("CAMPERVAN_ROOT") or "").strip():
        return False
    usual = data_home({k: v for k, v in env.items() if k != "CAMPERVAN_ROOT"})
    return data_home(env).resolve() != usual.resolve()


def load_settings() -> tuple[Path, "Settings"]:
    """Entry points: find the data home, read its .env into the environment, and return (home, settings)."""
    home = data_home()
    load_env(home / ".env")
    return home, Settings.from_env()


def load_env(path: Path) -> None:
    """Read KEY=VALUE lines from a .env file into os.environ (without overriding existing values)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def child_env(environ: Mapping[str, str]) -> dict[str, str]:
    """Environment for the Claude Code child process: everything except the bot's secrets and Anthropic keys.

    Claude never needs the bot token; and without an API key in its environment Claude Code always uses the
    Claude subscription login, so a key added somewhere later can't quietly switch it to pay-per-use.
    """
    return {k: v for k, v in environ.items() if not k.startswith(SECRET_PREFIXES) and k not in SECRET_KEYS}


@dataclass(frozen=True)
class Settings:
    """Every setting the bot reads from .env or the environment. Secrets are left out of repr()."""

    telegram_token: str = field(default="", repr=False)
    allowed_user_id: int | None = None
    group_window_seconds: int = 180
    analysis: str = "off"  # off | manual | auto
    provider: str = "claude-code"  # normalised here, validated by providers.name()
    model: str = ""
    claude_model: str = ""
    claude_bin: str = "claude"
    effort: str = "medium"
    price_per_mtok: str = ""
    openrouter_api_key: str = field(default="", repr=False)
    sheet_url: str = field(default="", repr=False)
    sheet_secret: str = field(default="", repr=False)
    sheet_link: str = ""
    data_root: str = ""  # CAMPERVAN_ROOT (legacy; data_home() decides where the data is)
    domain: str = DEFAULT_DOMAIN
    digest_time: str = "09:00"  # DIGEST_TIME: local "HH:MM" for the morning digest, "off" or empty to disable
    claude_env: Mapping[str, str] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        """Read every setting; environ defaults to os.environ."""
        env = os.environ if environ is None else environ
        allowed = env.get("TELEGRAM_ALLOWED_USER_ID")
        return cls(
            telegram_token=env.get("TELEGRAM_BOT_TOKEN") or "",
            allowed_user_id=int(allowed) if allowed else None,
            group_window_seconds=int(env.get("GROUP_WINDOW_SECONDS", "180")),
            analysis=env.get("ANALYSIS", "off").strip().lower(),
            provider=(env.get("PROVIDER") or "claude-code").strip().lower(),
            model=(env.get("MODEL") or "").strip(),
            claude_model=env.get("CLAUDE_MODEL") or "",
            claude_bin=env.get("CLAUDE_BIN") or "claude",
            effort=(env.get("EFFORT") or "medium").strip(),
            price_per_mtok=env.get("PRICE_PER_MTOK") or "",
            openrouter_api_key=env.get("OPENROUTER_API_KEY") or "",
            sheet_url=env.get("GOOGLE_SHEET_URL") or "",
            sheet_secret=env.get("GOOGLE_SHEET_SECRET", ""),
            sheet_link=(env.get("GOOGLE_SHEET_LINK") or "").strip(),
            data_root=env.get("CAMPERVAN_ROOT") or "",
            domain=env.get("DOMAIN") or DEFAULT_DOMAIN,
            digest_time=env.get("DIGEST_TIME", "09:00").strip().lower(),
            claude_env=child_env(env),
        )
