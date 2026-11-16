"""The shape of the package: no module outgrows the limit in docs/CODING_STANDARDS.md."""

import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "deal_scout"
MAX_LINES = 400


class LayoutTests(unittest.TestCase):
    def test_no_module_is_longer_than_the_limit(self):
        too_long = [
            f"{path.relative_to(PACKAGE.parent)}: {n} lines"
            for path in sorted(PACKAGE.rglob("*.py"))
            if (n := len(path.read_text(encoding="utf-8").splitlines())) > MAX_LINES
        ]
        self.assertEqual(too_long, [], f"modules over {MAX_LINES} lines: split them by job")


if __name__ == "__main__":
    unittest.main()
