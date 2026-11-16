"""The bot's slash commands: /help, /new, /done, /phone, /list, /status, /top and the rest."""

import functools
import logging
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from deal_scout import reports as reports_mod
from deal_scout import usage
from deal_scout.attach import ANALYSIS_OFF
from deal_scout.render import Message

if TYPE_CHECKING:  # the bot owns its commands; importing it here at run time would be a cycle
    from deal_scout.bot import Bot

log = logging.getLogger("dealscout")

HELP = (
    "Send me screenshots, links and notes about a listing. Everything sent close together lands in one listing.\n"
    "/new  start a new listing now\n"
    "/done  analyse the listing you just sent (/done 7 re-analyses #7)\n"
    "/phone 7 600 123 456  WhatsApp button for #7's seller message (the number is not saved)\n"
    "/list  show the latest listings\n"
    "/status  is the bot up to date, is anything being analysed\n"
    "/market  asking prices from your own listings\n"
    "/top  the best open listings (/top 10 shows more)\n"
    "/compare 7 12  listings side by side (2 to 4 numbers)\n"
    "/show 7  the summary of #7 again\n"
    "/digest  the morning digest now\n"
    "/sheet  open the Google Sheet\n"
    "/usage  how much Claude the analysis used (7 days, 30 days, all time)\n"
    "/help  this message"
)


class Commands:
    """Answers the owner's slash commands for one bot (its state, inbox and callbacks)."""

    def __init__(self, bot: "Bot") -> None:
        """Bind the bot whose state and senders the commands use."""
        self.bot = bot

    def handle(self, chat: int | None, cmd: str, args: Sequence[str] = ()) -> None:
        """Answer a slash command from the owner."""
        self.bot.attach.flush_ack(force=True)  # confirm what was saved before answering (e.g. before /done starts)
        cmd, args = reports_mod.split_show_link(cmd, args)  # a tapped "/show_12" is "/show 12"
        viewing = (
            self.bot.attach.pending if self.bot.attach.pending and self.bot.attach.pending.kind == "viewing" else None
        )
        if viewing and cmd == "/done" and not args:
            self.bot.callbacks.handle_viewing(None, "finish", viewing.n, [])  # /done in a viewing finishes it
            return
        if self.bot.attach.leave_for_command(cmd, args):
            return
        handlers: dict[str, Callable[[int | None, Sequence[str]], None]] = {
            "/start": self.cmd_help,
            "/help": self.cmd_help,
            "/new": self.cmd_new,
            "/done": self.done,
            "/phone": self.phone,
            "/status": self.cmd_status,
            "/usage": self.cmd_usage,
            "/sheet": self.cmd_sheet,
            "/market": self.cmd_market,
            "/list": self.cmd_list,
            "/top": functools.partial(self.run_reports, "/top"),
            "/compare": functools.partial(self.run_reports, "/compare"),
            "/show": functools.partial(self.run_reports, "/show"),
            "/digest": self.cmd_digest,
        }
        handler = handlers.get(cmd)
        if handler is None:
            self.bot.reply(chat, "Unknown command. /help")
            return
        handler(chat, args)

    def cmd_help(self, chat: int | None, args: Sequence[str]) -> None:
        """Send the list of commands."""
        self.bot.reply(chat, HELP)

    def cmd_new(self, chat: int | None, args: Sequence[str]) -> None:
        """Make the next message start a new listing."""
        self.bot.state["current"] = None
        self.bot.reply(chat, "Next message starts a new listing.")

    def cmd_status(self, chat: int | None, args: Sequence[str]) -> None:
        """Report the bot's version and queue."""
        self.bot.reply(chat, self.bot.status())

    def cmd_usage(self, chat: int | None, args: Sequence[str]) -> None:
        """Report how much Claude the analysis used."""
        self.bot.reply(chat, usage.summary(self.bot.root / "logs" / "usage.jsonl"))

    def cmd_sheet(self, chat: int | None, args: Sequence[str]) -> None:
        """Send the link to the Google Sheet."""
        if self.bot.sheet_link:
            self.bot.send(Message("Your listings sheet:", buttons=(("📊 Open sheet", self.bot.sheet_link),)))
        else:
            self.bot.reply(
                chat,
                "No sheet link yet: put the spreadsheet's address in .env as GOOGLE_SHEET_LINK and restart the bot.",
            )

    def cmd_market(self, chat: int | None, args: Sequence[str]) -> None:
        """Send market.md, cut to Telegram's size limit."""
        path = self.bot.root / "listings" / "market.md"
        self.bot.reply(
            chat,
            path.read_text(encoding="utf-8")[:4000]
            if path.exists()
            else "No market data yet: it is built after the first analysis.",
        )

    def cmd_list(self, chat: int | None, args: Sequence[str]) -> None:
        """List the latest inbox folders with their photo and link counts."""
        nums = self.bot.inbox.latest()
        if not nums:
            self.bot.reply(chat, "Nothing saved yet.")
            return
        lines = []
        for n in nums:
            photos, links = self.bot.inbox.summary(n)
            lines.append(f"#{n}  {photos} photo(s)  {links[0] if links else 'no link'}")
        self.bot.reply(chat, "\n".join(lines))

    def done(self, chat: int | None, args: Sequence[str]) -> None:
        """Queue the analysis of the open listing, or of the listing number given."""
        if self.bot.processor is None:
            self.bot.reply(chat, ANALYSIS_OFF)
            return
        if args and args[0].lstrip("#").isdigit():
            n = int(args[0].lstrip("#"))
            if not (self.bot.inbox.dir / f"{n:03d}").is_dir():
                self.bot.reply(chat, f"There is no #{n}.")
                return
        elif cur := self.bot.state.get("current"):
            n = cur["n"]
            self.bot.state["current"] = None  # whatever comes next is a new listing
        else:
            self.bot.reply(chat, "No open listing. Send screenshots first, or /done 7 to re-analyse #7.")
            return
        self.bot.queue_analysis(n)

    def phone(self, chat: int | None, args: Sequence[str]) -> None:
        """Build the seller message with a WhatsApp button for a listing from a phone number given in chat."""
        if len(args) < 2 or not args[0].lstrip("#").isdigit():
            self.bot.reply(chat, "Usage: /phone 7 600 123 456")
            return
        n = int(args[0].lstrip("#"))
        if self.bot.contact is None:
            self.bot.reply(chat, "Not available.")
            return
        try:
            items = self.bot.contact(n, " ".join(args[1:]))
        except Exception as exc:
            self.bot.reply(chat, f"#{n}: {exc}")
            return
        for item in items:
            self.bot.send(item)

    def cmd_digest(self, chat: int | None, args: Sequence[str]) -> None:
        """Send the morning digest now, without counting it as today's."""
        if self.bot.digest is None:
            self.bot.reply(chat, "Not available.")
            return
        try:
            items = self.bot.digest(save=False)
        except Exception as exc:
            log.exception("digest failed")
            self.bot.reply(chat, f"The digest failed: {exc}")
            return
        if not items:
            self.bot.reply(chat, "Nothing to report today.")
        for item in items:
            self.bot.send(item)

    def run_reports(self, cmd: str, chat: int | None, args: Sequence[str]) -> None:
        """Send what /top, /compare or /show (or a button's sh:N) answer; a problem comes back as one line."""
        if self.bot.reports is None:
            self.bot.reply(chat, "Not available.")
            return
        try:
            items = self.bot.reports(cmd, args)
        except Exception as exc:
            self.bot.reply(chat, str(exc))
            return
        for item in items:
            self.bot.send(item)
