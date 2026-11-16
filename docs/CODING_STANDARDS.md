# Coding standards

These rules bind every change to `deal_scout/` and `tests/`, by people or agents. `ruff`, `mypy` and the tests enforce
most of them (see "Development" in the README). Where a tool and this page disagree, fix the tool config.

## Style

- **PEP 8**, with a line length of 120. Code is formatted with `ruff format` (pre-commit and CI enforce it). Names: `snake_case` for functions and variables, `CapWords` for classes,
  `UPPER_CASE` for module constants.
- **PEP 257 docstrings** on every module, public class and public function. The first line is one sentence ending
  in a period. Say what and why; the signature already says the types.
- **Strings:** f-strings, not `%` or `.format()`. The exception is logging, which keeps lazy `%s` arguments:
  `log.info("#%s done in %.0f s", nnn, secs)`, never `log.info(f"...")`. Long templates defined once as constants
  (the analysis prompt) keep `str.format()` with named fields.
- **Imports:** at the top of the module, grouped stdlib / third-party / local (ruff's isort sorts them). An import
  goes inside a function only for an optional SDK (see below) or to break a real import cycle, with a comment that
  says which.
- **No lambdas assigned to names.** Write a `def`, or a small helper at module level.
- Comments explain why, not what. Delete commented-out code.

## Typing

- **Type hints on every public function and method**, in Python 3.12+ syntax: `str | None`, `list[str]`,
  `dict[str, int]`. `from __future__ import annotations` is not needed.
- **Structured data gets a type.** Use a `@dataclass` (frozen where it never changes) or a `TypedDict` instead of
  a loose `dict` when the same keys travel between modules: an outgoing Telegram message, usage stats, a CSV row.
  JSON the model returns stays a `dict` at the boundary and is read through one function.
- `Any` only at real boundaries: parsed JSON and SDK responses.

## Design

- **Small, single-purpose units.** One module does one job (prompting, scoring, storage, rendering, contact,
  transport). A function aims for under ~40 lines; a longer one needs a reason.
- **No import-time side effects** except defining constants. Importing a module never reads `.env`, loads a domain,
  touches the disk or the network.
- **Configuration in one place.** `deal_scout/config.py` reads the environment once into a typed settings object, and
  everything else gets settings passed in. No `os.environ` reads scattered across modules, and no code writes to
  `os.environ` to pass a value along.
- **No global mutable state.** The active domain, the model and the settings are parameters, not module globals
  that callers switch.
- **Pure functions for the logic.** Scoring, formatting and grouping take values and return values, with no I/O,
  so tests can call them directly. I/O sits at the edges (the bot loop, the runner, the storage functions).
- **Dependency injection for the outside world.** Telegram, `subprocess.run`, the SDK clients, the sheet opener and
  the clock are passed in, so tests can fake them.

## Hard project rules

These are design decisions, not style. Don't change them without the owner's say-so.

| Rule | Why |
|---|---|
| The bot core (the `deal_scout` package) is **standard library only**. | It runs on a small always-on Linux box with no build step, and nothing extra needs to be audited or updated. |
| The `anthropic` and `openai` SDKs are **imported lazily**, only inside their provider's function. | Someone using the default `claude-code` provider doesn't need them installed. |
| **Strangers get no answer.** Every update and button tap from a user other than `TELEGRAM_ALLOWED_USER_ID` is ignored silently. | The bot is private. Any reply would confirm it exists and invite probing. |
| The **Claude Code run keeps its restrictions**: only the Read, Write, Edit and Glob tools, writes only to `listings/`, no shell, no web, no MCP, and no `ANTHROPIC_*` key or `TELEGRAM_*` value in its environment. | A listing is untrusted text from a stranger. A prompt injection in it must not reach the network, other files or the bot token, and a stray API key must not move the run off the subscription. |
| **Phone numbers are never written to disk.** They live only in button data and are scrubbed from cards. | Sellers' personal data. The repo is public, and logs and cards travel. |
| **Nothing domain-specific in `deal_scout/`.** Item names, specs, categories, languages and prices live in `domains/<name>/`. | The repo is a blueprint for other kinds of purchase. A new domain must need no code changes. |
| **Every provider returns the same schema.** The schema stays strict-mode compatible: every object closed, every key required, typed enums, no numeric limits. | Scoring, storage, the sheet and Telegram rendering don't care which model ran. |

## Errors and logging

- **One exception type per module boundary** (`ProcessError`, `ProviderError`, `DomainError`, ...), raised with a
  message the owner can act on. Re-raise lower-level errors as that type with `raise ... from exc`.
- **Never swallow an error silently.** A broad `except Exception` is allowed only at a boundary that must survive
  (the poll loop, the worker, an optional step like the market or the sheet), and it always logs with
  `log.exception` or `log.error`.
- **Log metadata only:** listing numbers, durations, counts, model names, exit codes. Never message text, seller
  names, phone numbers, tokens or the content of cards.
- One logger, `logging.getLogger("dealscout")`; only `__main__.py` configures handlers.

## Tests

- Tests are `unittest.TestCase` classes, run with pytest (`uv run pytest`). Coverage must not drop below the floor
  in `pyproject.toml` (`make cov`).
- **No network and no real model calls.** Use the fakes in `tests/fakes.py`: `FakeRunner` for Claude Code,
  `FakeAnthropic` and `FakeOpenAI` for the SDKs; fake Telegram APIs and openers live next to their tests. Each test
  works in a temporary directory.
- **Golden outputs.** `tests/test_golden.py` freezes the prompt, the schema, the Telegram messages and the generated
  files for a made-up domain (`tests/fixtures/`). A refactor must leave `tests/golden/` untouched; an intended output
  change regenerates it with `GOLDEN_UPDATE=1` in its own commit, with the diff reviewed.
- One test file per module (`tests/test_<module>.py`).
- **Every bug fix and feature comes with a test** that fails without it.
- Tests that change the environment restore it (`addCleanup`).

## Git

- **Small, focused commits** with an imperative subject line under ~70 characters ("Split scoring out of
  process.py"), and a body when the why isn't obvious. Behaviour changes and refactors go in separate commits.
- `make check` (ruff, format check, mypy, tests) passes before every commit; the pre-commit hooks run most of it.
- **Never commit** `.env`, `.state.json`, `inbox/`, `listings/`, `logs/`, `.venv/` or `domains/*/criteria.md`. The
  repo is public: check `git diff --cached` for names, numbers and tokens before committing.
- Commit as `Szymon Klempert <56302798+szymonklempert@users.noreply.github.com>`.
