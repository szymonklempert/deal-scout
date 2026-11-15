"""Start the bot: python3 -m deal_scout (settings, domain, logging and the callbacks wired together)."""
import functools
import logging
import logging.handlers
import sys
from pathlib import Path

from deal_scout import contact, corrections, domain, process, providers, sheet
from deal_scout.bot import Bot, log
from deal_scout.config import ROOT, Settings, load_env
from deal_scout.telegram import TelegramAPI


def setup_logging(root: Path) -> None:
    """Log to the terminal (or systemd journal) and to logs/bot.log (rotated, 3 x 1 MB). Never message contents."""
    (root / "logs").mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    for handler in (logging.StreamHandler(sys.stdout),
                    logging.handlers.RotatingFileHandler(root / "logs" / "bot.log", maxBytes=1_000_000, backupCount=3,
                                                         encoding="utf-8")):
        handler.setFormatter(fmt)
        log.addHandler(handler)
    log.setLevel(logging.INFO)


def main() -> None:
    """Start the bot: read the settings once, wire the analysis and contact helpers in, and poll Telegram."""
    setup_logging(ROOT)
    load_env(ROOT / ".env")
    settings = Settings.from_env()
    if not settings.telegram_token:
        sys.exit("TELEGRAM_BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")
    mode = settings.analysis
    try:
        d = domain.load(settings.domain)
    except domain.DomainError as exc:
        sys.exit(str(exc))

    processor = functools.partial(process.process_listing, ROOT, settings=settings, domain=d)
    analyse = mode in ("manual", "auto")
    bot = Bot(TelegramAPI(settings.telegram_token), ROOT, settings.allowed_user_id, settings.group_window_seconds,
              processor=processor if analyse else None, auto_analyse=(mode == "auto"),
              contact=functools.partial(contact.contact_for, ROOT), sheet_link=sheet.link(settings),
              seller_text=functools.partial(contact.seller_text, ROOT),
              fixer=functools.partial(corrections.fix, ROOT, settings=settings, domain=d))
    try:
        provider = providers.name(settings)
        engine = f"{provider}, model {providers.model_for(provider, settings)}"
    except providers.ProviderError as exc:
        engine = f"misconfigured: {exc}"
    log.info("Domain: %s. Analysis: %s (%s). Google Sheet %s", d.name, mode if analyse else "off",
             engine, "on" if settings.sheet_url else "off")
    bot.run()


if __name__ == "__main__":
    main()
