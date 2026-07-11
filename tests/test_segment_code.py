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

    def test_decorators_are_independent_units(self):
        units = self.segment_python(
            """@first
@second(value=1)
def work(item):
    run(item)
"""
        )

        decorators = [
            unit
            for unit in units
            if unit.kind == "decorator"
        ]
        definition = next(
            unit
            for unit in units
            if unit.node_type == "function_definition"
        )
        body = next(
            unit
            for unit in units
            if unit.code.strip() == "run(item)"
        )

        self.assertEqual(
            [unit.code.strip() for unit in decorators],
            [
                "@first",
                "@second(value=1)",
            ],
        )
        self.assertEqual(
            definition.code.strip(),
            "def work(item):",
        )
        self.assertNotIn("@first", definition.code)
        self.assertNotIn("@second", definition.code)
        self.assertNotIn("run(item)", definition.code)

        self.assertEqual(definition.display_start_line, 3)
        self.assertEqual(definition.display_end_line, 3)

        self.assertEqual(
            [unit.parent_id for unit in decorators],
            [definition.parent_id, definition.parent_id],
        )
        self.assertEqual(
            [unit.depth for unit in decorators],
            [definition.depth, definition.depth],
        )
        self.assertEqual(body.parent_id, definition.id)

    def test_decorated_class_is_split(self):
        units = self.segment_python(
            """@frozen
class Item:
    value = 1
"""
        )

        decorator = next(
            unit
            for unit in units
            if unit.kind == "decorator"
        )
        definition = next(
            unit
            for unit in units
            if unit.node_type == "class_definition"
        )
        member = next(
            unit
            for unit in units
            if unit.code.strip() == "value = 1"
        )

        self.assertEqual(decorator.code.strip(), "@frozen")
        self.assertEqual(definition.code.strip(), "class Item:")
        self.assertNotIn("@frozen", definition.code)
        self.assertNotIn("value = 1", definition.code)
        self.assertEqual(member.parent_id, definition.id)

    def test_except_else_and_finally_are_independent_units(self):
        units = self.segment_python(
            """try:
    run()
except Exception as exc:
    handle(exc)
else:
    save()
finally:
    close()
"""
        )

        try_unit = next(
            unit
            for unit in units
            if unit.node_type == "try_statement"
        )
        except_unit = next(
            unit
            for unit in units
            if unit.node_type == "except_clause"
        )
        else_unit = next(
            unit
            for unit in units
            if unit.node_type == "else_clause"
        )
        finally_unit = next(
            unit
            for unit in units
            if unit.node_type == "finally_clause"
        )

        self.assertEqual(try_unit.code.strip(), "try:")
        self.assertEqual(
            except_unit.code.strip(),
            "except Exception as exc:",
        )
        self.assertEqual(else_unit.code.strip(), "else:")
        self.assertEqual(finally_unit.code.strip(), "finally:")

        for unit in (
            try_unit,
            except_unit,
            else_unit,
            finally_unit,
        ):
            self.assertNotIn("...", unit.code)
            self.assertNotIn("CLAUSE", unit.code)

        run_unit = next(
            unit
            for unit in units
            if unit.code.strip() == "run()"
        )
        handle_unit = next(
            unit
            for unit in units
            if unit.code.strip() == "handle(exc)"
        )
        save_unit = next(
            unit
            for unit in units
            if unit.code.strip() == "save()"
        )
        close_unit = next(
            unit
            for unit in units
            if unit.code.strip() == "close()"
        )

        self.assertEqual(run_unit.parent_id, try_unit.id)
        self.assertEqual(handle_unit.parent_id, except_unit.id)
        self.assertEqual(save_unit.parent_id, else_unit.id)
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
        self.assertIn('" review "', marker_unit.code)
        self.assertIn('" marks "', marker_unit.code)

        self.assertIn(
            '"original_language": original_language',
            payload_unit.code,
        )
        self.assertIn(
            '"original_prompt": original_prompt',
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
                unit.code.strip().rstrip(",")
                in isolated_values
                for unit in units
            )
        )

    def test_complex_condition_remains_in_if_unit(self):
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

        if_unit = next(
            unit
            for unit in units
            if unit.node_type == "if_statement"
        )
        continue_unit = next(
            unit
            for unit in units
            if unit.code.strip() == "continue"
        )

        self.assertIn('"by phone" in flow_lower', if_unit.code)
        self.assertIn(
            'normalized_cleaned_lower.startswith("guests ")',
            if_unit.code,
        )
        self.assertNotIn("CONDITION", if_unit.code)
        self.assertNotIn("GROUP", if_unit.code)
        self.assertNotIn("continue", if_unit.code)

        self.assertFalse(
            any(
                unit.scope_role == "condition"
                or unit.scope_role.startswith(
                    "condition_group_"
                )
                for unit in units
            )
        )
        self.assertEqual(continue_unit.parent_id, if_unit.id)

    def test_simple_condition_remains_in_if_unit(self):
        units = self.segment_python(
            """if enabled and ready:
    run()
"""
        )

        if_unit = next(
            unit
            for unit in units
            if unit.node_type == "if_statement"
        )
        run_unit = next(
            unit
            for unit in units
            if unit.code.strip() == "run()"
        )

        self.assertEqual(
            if_unit.code.strip(),
            "if enabled and ready:",
        )
        self.assertEqual(run_unit.parent_id, if_unit.id)

    def test_translation_code_has_no_synthetic_placeholders(self):
        units = self.segment_python(
            """@decorator
def work(value):
    if value:
        return value
"""
        )

        forbidden = (
            "...",
            "BODY",
            "MEMBERS",
            "CONDITION",
            "CLAUSE",
        )

        for unit in units:
            for token in forbidden:
                self.assertNotIn(token, unit.code)


if __name__ == "__main__":
    unittest.main()
