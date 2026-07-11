from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from segment_code import CodeSegmenter


class SegmentCodeRegressionTests(unittest.TestCase):
    def segment_python(self, source: str):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "sample.py"
            path.write_text(source, encoding="utf-8")
            result = CodeSegmenter().segment_file(
                path,
                relative_to=root,
            )
            return result.units

    def test_decorators_stay_with_definition(self):
        units = self.segment_python(
            """@first
@second(value=1)
def work(item):
    run(item)
"""
        )

        definition = next(
            unit
            for unit in units
            if "def work" in unit.code
        )

        self.assertIn("@first", definition.code)
        self.assertIn("@second(value=1)", definition.code)
        self.assertNotIn("run(item)", definition.code)
        self.assertEqual(definition.kind, "definition")
        self.assertEqual(definition.display_start_line, 1)
        self.assertEqual(definition.display_end_line, 3)

        self.assertFalse(
            any(unit.node_type == "decorator" for unit in units)
        )

        body = next(
            unit
            for unit in units
            if unit.code.strip() == "run(item)"
        )
        self.assertEqual(body.parent_id, definition.id)

    def test_except_and_finally_are_independent_units(self):
        units = self.segment_python(
            """try:
    run()
except Exception as exc:
    handle(exc)
finally:
    close()
"""
        )

        except_unit = next(
            unit
            for unit in units
            if unit.code.lstrip().startswith(
                "except Exception as exc:"
            )
        )
        finally_unit = next(
            unit
            for unit in units
            if unit.code.lstrip().startswith("finally:")
        )

        self.assertNotIn("handle(exc)", except_unit.code)
        self.assertNotIn("close()", finally_unit.code)

        handle_unit = next(
            unit
            for unit in units
            if unit.code.strip() == "handle(exc)"
        )
        close_unit = next(
            unit
            for unit in units
            if unit.code.strip() == "close()"
        )

        self.assertEqual(handle_unit.parent_id, except_unit.id)
        self.assertEqual(close_unit.parent_id, finally_unit.id)

    def test_multiline_data_assignment_remains_one_unit(self):
        units = self.segment_python(
            """SUBJECT_CLAUSE_MARKERS = [
    " reviews ",
    " review ",
    " marks ",
]

payload = {
    "original_language": original_language,
    "original_prompt": original_prompt,
    "previous_contexts": previous_contexts,
}
"""
        )

        marker_unit = next(
            unit
            for unit in units
            if "SUBJECT_CLAUSE_MARKERS =" in unit.code
        )
        payload_unit = next(
            unit
            for unit in units
            if "payload =" in unit.code
        )

        self.assertIn('" reviews "', marker_unit.code)
        self.assertIn('" marks "', marker_unit.code)
        self.assertIn(
            '"original_language": original_language',
            payload_unit.code,
        )
        self.assertIn(
            '"previous_contexts": previous_contexts',
            payload_unit.code,
        )

        isolated_values = {
            '" reviews "',
            '" review "',
            '" marks "',
            '"original_language": original_language',
            '"original_prompt": original_prompt',
            '"previous_contexts": previous_contexts',
        }

        self.assertFalse(
            any(
                unit.code.strip().rstrip(",") in isolated_values
                for unit in units
            )
        )

    def test_complex_condition_splits_into_major_groups(self):
        units = self.segment_python(
            """for item in items:
    if (
        (
            "by phone" in flow_lower
            or "phone" in flow_lower
            or "phone reservation" in flow_lower
            or "phone reservations" in flow_lower
            or "phone call" in flow_lower
            or "phone calls" in flow_lower
            or "customer call" in flow_lower
            or "customer calls" in flow_lower
        )
        and (
            normalized_cleaned_lower in {
                "customer",
                "customers",
                "guest",
                "guests",
            }
            or normalized_cleaned_lower.startswith("customer ")
            or normalized_cleaned_lower.startswith("customers ")
            or normalized_cleaned_lower.startswith("guest ")
            or normalized_cleaned_lower.startswith("guests ")
        )
    ):
        continue
"""
        )

        condition_groups = [
            unit
            for unit in units
            if unit.scope_role.startswith("condition_group_")
        ]

        self.assertEqual(len(condition_groups), 2)
        self.assertTrue(
            all(unit.kind == "condition" for unit in condition_groups)
        )

        if_unit = next(
            unit
            for unit in units
            if unit.node_type == "if_statement"
        )
        self.assertIn("CONDITION_GROUP_1", if_unit.code)
        self.assertIn("CONDITION_GROUP_2", if_unit.code)
        self.assertNotIn('"by phone"', if_unit.code)

    def test_simple_condition_is_not_split(self):
        units = self.segment_python(
            """if enabled and ready:
    run()
"""
        )

        self.assertFalse(
            any(
                unit.scope_role == "condition"
                or unit.scope_role.startswith("condition_group_")
                for unit in units
            )
        )


if __name__ == "__main__":
    unittest.main()
