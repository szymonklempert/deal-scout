"""The prompt and the JSON schema."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bot"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fakes import CAMPERVAN  # noqa: E402

import prompts  # noqa: E402


class SchemaTests(unittest.TestCase):
    def test_strict_mode_rules_hold_everywhere(self):
        """OpenAI strict mode and Anthropic structured outputs: every object closed with all keys required, enums
        typed, no numeric limits."""
        def walk(node, path="$"):
            if isinstance(node, dict):
                if node.get("type") == "object":
                    self.assertIs(node.get("additionalProperties"), False, path)
                    self.assertEqual(sorted(node["required"]), sorted(node["properties"]), path)
                if "enum" in node:
                    self.assertEqual(node.get("type"), "string", path)
                for bad in ("minimum", "maximum", "multipleOf"):
                    self.assertNotIn(bad, node, path)
                for k, v in node.items():
                    walk(v, f"{path}.{k}")
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    walk(v, f"{path}[{i}]")
        walk(prompts.schema(CAMPERVAN, card=True))


if __name__ == "__main__":
    unittest.main()
