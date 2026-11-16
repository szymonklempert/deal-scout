import copy
import unittest

from fakes import CAMPERVAN
from test_golden import FIXTURES, result

from deal_scout import domain, prompts, validation

GADGET = domain.Domain.from_dir(FIXTURES / "domains" / "gadget")


class ValidateTests(unittest.TestCase):
    def test_types_and_unions(self):
        sch = {"type": ["integer", "null"]}
        self.assertEqual(validation.validate(3, sch), [])
        self.assertEqual(validation.validate(None, sch), [])
        self.assertEqual(validation.validate("3", sch), ["$: expected integer or null, got str"])
        self.assertEqual(validation.validate(3.5, sch), ["$: expected integer or null, got float"])

    def test_bool_is_not_a_number(self):
        self.assertEqual(validation.validate(True, {"type": "integer"}), ["$: expected integer, got bool"])
        self.assertEqual(validation.validate(True, {"type": "number"}), ["$: expected number, got bool"])
        self.assertEqual(validation.validate(1, {"type": "boolean"}), ["$: expected boolean, got int"])
        self.assertEqual(validation.validate(2, {"type": "number"}), [])
        self.assertEqual(validation.validate(2.5, {"type": "number"}), [])

    def test_enum_array_and_object(self):
        sch = {
            "type": "object",
            "additionalProperties": False,
            "required": ["a", "b"],
            "properties": {
                "a": {"type": "string", "enum": ["x", "y"]},
                "b": {"type": "array", "items": {"type": "string"}},
            },
        }
        self.assertEqual(validation.validate({"a": "x", "b": ["q"]}, sch), [])
        self.assertEqual(
            validation.validate({"a": "z", "b": ["q", 1], "c": 1}, sch),
            ["$.c: unexpected key", "$.a: 'z' is not one of ['x', 'y']", "$.b[1]: expected string, got int"],
        )
        self.assertEqual(validation.validate({"a": "x"}, sch), ["$.b: missing"])
        self.assertEqual(validation.validate([], sch), ["$: expected object, got list"])
        self.assertEqual(validation.validate({"a": "x", "b": None}, sch), ["$.b: expected array, got null"])

    def test_errors_are_capped(self):
        errors = validation.validate(list(range(50)), {"type": "array", "items": {"type": "string"}})
        self.assertEqual(len(errors), validation.MAX_ERRORS)

    def test_unknown_type_name_never_matches(self):
        self.assertEqual(validation.validate(1, {"type": "weird"}), ["$: expected weird, got int"])


class ValidateReviewTests(unittest.TestCase):
    def test_golden_result_passes(self):
        self.assertEqual(validation.validate_review(result(), GADGET), [])

    def test_optional_keys_may_be_present_or_absent(self):
        data = dict(result(), card_markdown="# card", viewing_checks=["Start the engine"])
        self.assertEqual(validation.validate_review(data, GADGET), [])
        self.assertIn(
            "$.card_markdown: expected string, got int", validation.validate_review(dict(data, card_markdown=1), GADGET)
        )

    def test_wrong_shapes(self):
        base = result()

        def errors(**over):
            return validation.validate_review(dict(copy.deepcopy(base), **over), GADGET)

        self.assertEqual(validation.validate_review([], GADGET), ["$: expected object, got list"])
        self.assertIn(
            "$.verdict: 'maybe' is not one of ['worth a visit', 'ask first', 'skip']", errors(verdict="maybe")
        )
        self.assertIn("$.price_pln: expected number or null, got str", errors(price_pln="1350"))
        self.assertIn("$.year: expected integer or null, got bool", errors(year=True))
        self.assertIn("$.price_net: expected boolean, got str", errors(price_net="no"))
        self.assertTrue(any(e.startswith("$.kind: 'bogus'") for e in errors(kind="bogus")))
        self.assertIn("$.missing[0]: expected string, got int", errors(missing=[1]))
        specs = copy.deepcopy(base["specs"])
        specs[2]["score"] = "high"
        self.assertIn("$.specs[2].score: expected integer or null, got str", errors(specs=specs))
        specs[1]["name"] = "Colour"
        self.assertTrue(any(e.startswith("$.specs[1].name") for e in errors(specs=specs)))
        specs[0].pop("value")
        self.assertIn("$.specs[0].value: missing", errors(specs=specs))
        self.assertIn("$.extra: unexpected key", errors(extra=1))
        checks = copy.deepcopy(base["criteria_checks"])
        checks[0]["result"] = "yes"
        self.assertTrue(any(e.startswith("$.criteria_checks[0].result") for e in errors(criteria_checks=checks)))
        missing = {k: v for k, v in base.items() if k != "title"}
        self.assertEqual(validation.validate_review(missing, GADGET), ["$.title: missing"])

    def test_the_real_domain_schema_is_usable(self):
        self.assertTrue(validation.validate_review({}, CAMPERVAN))
        full = prompts.schema(CAMPERVAN, card=True)
        self.assertIn("card_markdown", full["required"])
