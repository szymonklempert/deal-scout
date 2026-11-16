import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts import check_dist


class ForbiddenTests(unittest.TestCase):
    def test_private_files_are_caught(self):
        for name in (
            "deal_scout/_domains/campervan/criteria.md",
            "x-0.1/domains/a/criteria.md",
            ".env",
            "x-0.1/.env",
            "x/.env.local",
            ".state.json",
            "x/inbox/001/photo_01.jpg",
            "x/listings/001.md",
            "logs/bot.log",
            "x/.venv/bin/python",
            "x/_site/index.html",
            "IMG_1036.PNG",
        ):
            self.assertTrue(check_dist.forbidden(name), name)

    def test_normal_files_pass(self):
        for name in (
            "deal_scout/cli.py",
            "x-0.1/.env.example",
            "deal_scout/_env.example",
            "domains/campervan/criteria.example.md",
            "deal_scout/process.py",
        ):
            self.assertFalse(check_dist.forbidden(name), name)


class MainTests(unittest.TestCase):
    def build(self, folder, wheel_names):
        folder = Path(folder)
        with zipfile.ZipFile(folder / "p-0.1-py3-none-any.whl", "w") as z:
            for n in wheel_names:
                z.writestr(n, "x")
        with tarfile.open(folder / "p-0.1.tar.gz", "w:gz") as t:
            t.add(__file__, arcname="p-0.1/ok.py")

    def test_good_and_bad_builds(self):
        good = list(check_dist.WHEEL_MUST_HAVE)
        with tempfile.TemporaryDirectory() as d:
            self.build(d, good)
            self.assertEqual(check_dist.main(["x", d]), 0)
        with tempfile.TemporaryDirectory() as d:
            self.build(d, good + ["deal_scout/_domains/campervan/criteria.md"])
            self.assertEqual(check_dist.main(["x", d]), 1)
        with tempfile.TemporaryDirectory() as d:
            self.build(d, good[:1])
            self.assertEqual(check_dist.main(["x", d]), 1)
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(check_dist.main(["x", d]), 1)
