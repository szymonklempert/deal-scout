"""The deal-scout command: start the bot, set up a data folder, or run one of the helper tools.

    deal-scout [run]          start the Telegram bot (what `python3 -m deal_scout` does)
    deal-scout init [--home PATH]   create the data folder, .env and domain files (never overwrites anything)
    deal-scout process N      analyse inbox/N
    deal-scout sheet          copy every listing to the Google Sheet
    deal-scout market         rebuild listings/market.md
    deal-scout usage          print the Claude usage report
    deal-scout mcp            serve the MCP server over stdio (needs the mcp extra)

Each command calls the main() of the module that does the work. Standard library only.
"""

import argparse
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from deal_scout import __main__ as bot_main
from deal_scout import config, domain, market, process, scaffold, sheet, usage

Command = Callable[[argparse.Namespace], None]


def parser() -> argparse.ArgumentParser:
    """Build the argument parser; no subcommand means `run`."""
    p = argparse.ArgumentParser(prog="deal-scout", description="A private Telegram assistant that reviews listings.")
    sub = p.add_subparsers(dest="command", metavar="command")
    sub.add_parser("run", help="start the bot (the default)")
    init = sub.add_parser("init", help="create the data folder, .env and the domain files; never overwrites")
    init.add_argument(
        "--home", type=Path, help="the data folder (default: DEAL_SCOUT_HOME, else the checkout, else ~/.deal-scout)"
    )
    proc = sub.add_parser("process", help="analyse one saved listing")
    proc.add_argument("number", type=int, help="the listing number (inbox/NNN)")
    sub.add_parser("sheet", help="copy every listing to the Google Sheet")
    sub.add_parser("market", help="rebuild listings/market.md")
    sub.add_parser("usage", help="print the Claude usage report")
    sub.add_parser("mcp", help="serve the MCP server over stdio (needs: uv sync --extra mcp)")
    return p


def init(args: argparse.Namespace) -> None:
    """Set up the data folder and print what to do next."""
    home = (args.home or config.data_home()).expanduser().resolve()
    made = scaffold.init_home(home, domain.builtin_root(), scaffold.env_example())
    print(f"Data folder: {home}")
    print("Created: " + (", ".join(made) if made else "nothing, it was all there already"))
    print("Next:")
    print(f"  1. Fill in {home / '.env'} (Telegram token, ANALYSIS, ...)")
    print(f"  2. Write your wishes in {home / 'domains' / config.DEFAULT_DOMAIN / 'criteria.md'}")
    print("  3. Start the bot: deal-scout")
    if home != config.data_home():
        print(f"Use this folder later with: export DEAL_SCOUT_HOME={home}")


def run(args: argparse.Namespace) -> None:
    """Start the bot."""
    bot_main.main()


def run_process(args: argparse.Namespace) -> None:
    """Analyse one listing."""
    process.main(["deal-scout process", str(args.number)])


def run_sheet(args: argparse.Namespace) -> None:
    """Copy every listing to the Google Sheet."""
    sheet.main()


def run_market(args: argparse.Namespace) -> None:
    """Rebuild market.md."""
    market.main()


def run_usage(args: argparse.Namespace) -> None:
    """Print the usage report."""
    usage.main()


def run_mcp(args: argparse.Namespace) -> None:
    """Serve the MCP server over stdio."""
    try:
        from deal_scout import mcp_server  # here only: the mcp extra is optional and nothing else imports it
    except ModuleNotFoundError as exc:
        if (exc.name or "").split(".")[0] not in ("mcp", "mcp_types"):
            raise
        sys.exit("The MCP server needs the extra: uv sync --extra mcp (or pip install 'deal-scout[mcp]')")
    mcp_server.main()


COMMANDS: Mapping[str, Command] = {
    "run": run,
    "init": init,
    "process": run_process,
    "sheet": run_sheet,
    "market": run_market,
    "usage": run_usage,
    "mcp": run_mcp,
}


def main(argv: Sequence[str] | None = None, table: Mapping[str, Command] | None = None) -> None:
    """Parse the command line and run the chosen command; `table` replaces the real commands (tests)."""
    args = parser().parse_args(sys.argv[1:] if argv is None else argv)
    (table or COMMANDS)[args.command or "run"](args)


if __name__ == "__main__":
    main()
