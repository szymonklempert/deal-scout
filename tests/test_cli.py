import contextlib
import io
import pkgutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from deal_scout import __main__ as bot_main
from deal_scout import cli, config, domain, market, process, scaffold, sheet, usage


class DispatchTests(unittest.TestCase):
    def run_cli(self, *argv):
        calls = []
        table = {name: (lambda args, name=name: calls.append((name, args))) for name in cli.COMMANDS}
        cli.main(list(argv), table)
        return calls

    def test_no_arguments_runs_the_bot(self):
        self.assertEqual([c[0] for c in self.run_cli()], ["run"])
        self.assertEqual([c[0] for c in self.run_cli("run")], ["run"])

    def test_each_subcommand(self):
        for name in ("sheet", "market", "usage", "mcp"):
            self.assertEqual([c[0] for c in self.run_cli(name)], [name])
        name, args = self.run_cli("process", "7")[0]
        self.assertEqual((name, args.number), ("process", 7))
        name, args = self.run_cli("init", "--home", "/tmp/x")[0]
        self.assertEqual((name, args.home), ("init", Path("/tmp/x")))

    def test_bad_arguments_exit(self):
        for argv in (["process"], ["process", "x"], ["nope"]):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                cli.main(argv, {})

    def test_commands_delegate_to_the_module_mains(self):
        for module, argv, expected in (
            (bot_main, [], ()),
            (sheet, ["sheet"], ()),
            (market, ["market"], ()),
            (usage, ["usage"], ()),
            (process, ["process", "12"], (["deal-scout process", "12"],)),
        ):
            with mock.patch.object(module, "main") as fake:
                cli.main(argv)
            fake.assert_called_once_with(*expected)


class McpCommandTests(unittest.TestCase):
    def test_without_the_extra_it_says_how_to_install_it(self):
        code = (
            "import sys; sys.modules['mcp'] = None; sys.modules['mcp_types'] = None\n"
            "from deal_scout import cli\ncli.main(['mcp'])"
        )
        done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, check=False)
        self.assertEqual(done.returncode, 1)
        self.assertIn("uv sync --extra mcp", done.stderr)

    def test_it_serves_with_the_server_module(self):
        from deal_scout import mcp_server

        with mock.patch.object(mcp_server, "main") as fake:
            cli.main(["mcp"])
        fake.assert_called_once_with()

    def test_the_core_imports_without_mcp(self):
        import deal_scout

        names = [m.name for m in pkgutil.iter_modules(deal_scout.__path__, "deal_scout.") if "mcp_server" not in m.name]
        code = "import sys; sys.modules['mcp'] = None; sys.modules['mcp_types'] = None\n" + "\n".join(
            f"import {name}" for name in names
        )
        done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)


class InitTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.home = self.tmp / "home"
        self.builtin = self.tmp / "builtin"
        (self.builtin / "tv").mkdir(parents=True)
        (self.builtin / "tv" / "domain.json").write_text("{}")
        (self.builtin / "tv" / "criteria.example.md").write_text("example wishes")
        (self.builtin / "tv" / "criteria.md").write_text("PRIVATE")  # a checkout's own file must not be copied
        (self.builtin / "README.md").write_text("not a domain")
        self.template = self.tmp / "env.example"
        self.template.write_text("TELEGRAM_BOT_TOKEN=\n")

    def init(self):
        return scaffold.init_home(self.home, self.builtin, self.template)

    def test_creates_everything(self):
        made = self.init()
        for name in ("inbox", "listings", "logs"):
            self.assertTrue((self.home / name).is_dir())
        env = self.home / ".env"
        self.assertEqual(env.read_text(), "TELEGRAM_BOT_TOKEN=\n")
        self.assertEqual(stat.S_IMODE(env.stat().st_mode), 0o600)
        self.assertTrue((self.home / "domains" / "tv" / "domain.json").exists())
        self.assertEqual((self.home / "domains" / "tv" / "criteria.md").read_text(), "example wishes")
        self.assertFalse((self.home / "domains" / "README.md").exists())
        self.assertIn(".env", made)
        self.assertIn("domains/tv/criteria.md", made)

    def test_second_run_changes_nothing_and_never_overwrites(self):
        self.init()
        (self.home / ".env").write_text("MINE=1\n")
        (self.home / "domains" / "tv" / "criteria.md").write_text("my wishes")
        (self.home / "domains" / "tv" / "domain.json").write_text('{"mine": true}')
        self.assertEqual(self.init(), [])
        self.assertEqual((self.home / ".env").read_text(), "MINE=1\n")
        self.assertEqual((self.home / "domains" / "tv" / "criteria.md").read_text(), "my wishes")
        self.assertEqual((self.home / "domains" / "tv" / "domain.json").read_text(), '{"mine": true}')

    def test_a_criteria_file_already_there_is_kept_in_a_fresh_domain_copy(self):
        (self.home / "domains" / "tv").mkdir(parents=True)
        (self.home / "domains" / "tv" / "criteria.md").write_text("mine")
        self.init()
        self.assertEqual((self.home / "domains" / "tv" / "criteria.md").read_text(), "mine")
        self.assertFalse((self.home / "domains" / "tv" / "domain.json").exists())  # an existing folder is left alone

    def test_cli_init_prints_next_steps(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(["init", "--home", str(self.home)])
        text = out.getvalue()
        self.assertIn("Next:", text)
        self.assertIn("criteria.md", text)
        self.assertTrue((self.home / ".env").exists())
        self.assertTrue((self.home / "domains" / "campervan" / "domain.json").exists())
        # the real campervan criteria.md of the checkout is never copied, only made from the example
        self.assertEqual(
            (self.home / "domains" / "campervan" / "criteria.md").read_bytes(),
            (domain.builtin_root() / "campervan" / "criteria.example.md").read_bytes(),
        )

    def test_env_example_from_checkout_or_package(self):
        self.assertEqual(scaffold.env_example(), config.PACKAGE_DIR.parent / ".env.example")
        pkg = self.tmp / "site" / "deal_scout"
        pkg.mkdir(parents=True)
        self.assertEqual(scaffold.env_example(pkg), pkg / "_env.example")
