# code-translator 코드 번역 단위 수정 지시문

## 작업 기준

- 저장소: `srejis/code-translator`
- 기준 커밋: `80bc77ebe9986bed45619b9e649833cd1862db01`
- 수정 대상:
  - `segment_code.py`
  - `tests/test_segment_code.py`
  - `README.md`
- 수정하지 않는 파일:
  - `code_unit_viewer.py`
  - `requirements.txt`
  - `run_viewer.bat`
  - `install_and_run.bat`

이 작업의 목적은 코드를 이해시키거나 기능 단위로 추상화하는 것이 아니다.

프로그램이 수행해야 하는 역할은 다음과 같다.

> 독립된 코드 문법 단위를 원본 순서대로 분리하고, 각 단위를 자연어 한 문장으로 번역할 수 있는 입력으로 만든다.

---

# 1. 최종 분리 원칙

다음 원칙을 구현한다.

```text
데코레이터 1개 = 번역 단위 1개
함수 정의 헤더 = 번역 단위 1개
클래스 정의 헤더 = 번역 단위 1개
함수·클래스 본문의 각 독립 문장 = 각각 번역 단위 1개
if·elif·else·for·while·try·except·finally 헤더 = 각각 번역 단위 1개
조건식 = 해당 제어문 헤더에 포함
객체·딕셔너리·배열·리스트·튜플·세트 = 상위 문장에 포함
부모·자식 관계 = 원본 중첩 구조를 보존하는 메타데이터
```

다음 작업은 수행하지 않는다.

```text
데코레이터와 정의 결합
조건식의 의미 그룹 분리
긴 조건식을 글자 수로 분할
데이터 내부 항목을 개별 번역 단위로 분리
함수 정의와 본문을 종합한 기능 설명 생성
부모와 자식 번역문 결합
```

`code` 필드에는 LLM이 실제로 번역할 코드만 들어가야 한다.

다음과 같은 합성 자리표시자를 넣지 않는다.

```text
...
{ /* BODY */ }
# <EXCEPT_CLAUSE>
(CONDITION)
(CONDITION_GROUP_1)
```

`raw_code`에는 기존처럼 원본 AST 범위 전체를 보존한다.

---

# 2. `segment_code.py` 수정

## 2.1 Python 구조 상수 교체

`PY_STRUCTURAL_TYPES` 선언부터 조건식 복잡도 상수 선언까지를 모두 교체한다.

교체 범위는 현재 다음 항목을 포함한다.

```text
PY_STRUCTURAL_TYPES
PY_CLAUSE_ROLES
PY_CLAUSE_PARENT_TYPES
PY_CONDITION_OWNER_TYPES
CONDITION_CHAR_THRESHOLD
CONDITION_LINE_THRESHOLD
CONDITION_LEAF_THRESHOLD
CONDITION_DEPTH_THRESHOLD
CONDITION_GROUP_MAX
CONDITION_GROUP_MIN_LEAVES
CONDITION_GROUP_MIN_CHARS
```

해당 범위의 전체 내용을 아래 코드로 교체한다.

```python
PY_STRUCTURAL_TYPES = {
    "function_definition",
    "class_definition",
    "if_statement",
    "elif_clause",
    "else_clause",
    "for_statement",
    "while_statement",
    "try_statement",
    "except_clause",
    "finally_clause",
    "with_statement",
    "match_statement",
    "case_clause",
}

PY_CLAUSE_ROLES: dict[str, str] = {
    "elif_clause": "elif_clause",
    "else_clause": "else_clause",
    "except_clause": "except_clause",
    "finally_clause": "finally_clause",
}

PY_CLAUSE_PARENT_TYPES = {
    "if_statement",
    "for_statement",
    "while_statement",
    "try_statement",
}
```

`decorated_definition`은 일반 구조 단위에서 제외한다.

`PY_CONDITION_OWNER_TYPES`와 모든 `CONDITION_*` 상수는 남기지 않는다.

---

## 2.2 `_walk_scope()` 교체 및 데코레이터 처리 함수 추가

기존 `_walk_scope()` 함수 전체를 아래 코드로 교체한다.

그 바로 아래에 `_emit_decorated_definition()`을 함께 추가한다.

```python
    def _walk_scope(
        self,
        scope: Node,
        *,
        parent_id: str | None,
        depth: int,
        scope_role: str,
    ) -> None:
        for child in scope.named_children:
            if child.type == "ERROR" or child.is_missing:
                continue

            if (
                self._language == "python"
                and child.type == "decorated_definition"
            ):
                self._emit_decorated_definition(
                    child,
                    parent_id=parent_id,
                    depth=depth,
                    scope_role=scope_role,
                )
                continue

            self._emit_unit(
                child,
                parent_id=parent_id,
                depth=depth,
                scope_role=scope_role,
            )

    def _emit_decorated_definition(
        self,
        node: Node,
        *,
        parent_id: str | None,
        depth: int,
        scope_role: str,
    ) -> None:
        decorators = [
            child
            for child in node.named_children
            if child.type == "decorator"
        ]
        definition = node.child_by_field_name("definition")

        for decorator in decorators:
            self._emit_unit(
                decorator,
                parent_id=parent_id,
                depth=depth,
                scope_role="decorator",
            )

        if definition is None:
            definition = next(
                (
                    child
                    for child in reversed(node.named_children)
                    if child.type in {
                        "function_definition",
                        "class_definition",
                    }
                ),
                None,
            )

        if definition is None:
            self._diagnostics.append(
                {
                    "type": "missing_decorated_definition",
                    "node_type": node.type,
                    "start_line": node.start_point[0] + 1,
                    "end_line": node.end_point[0] + 1,
                    "source": self._text(node)[:500],
                }
            )
            return

        self._emit_unit(
            definition,
            parent_id=parent_id,
            depth=depth,
            scope_role=scope_role,
        )
```

결과 순서는 반드시 다음과 같아야 한다.

```text
첫 번째 데코레이터
두 번째 데코레이터
함수 또는 클래스 정의
정의 본문의 자식 문장
```

각 데코레이터와 정의는 같은 바깥쪽 `parent_id`와 `depth`를 가진다.

함수 또는 클래스 본문의 문장만 정의 단위의 자식이 된다.

---

## 2.3 `_emit_unit()`의 종류 결정 부분 수정

`_emit_unit()` 안에서 다음 코드를 찾는다.

```python
        kind = self._classify_kind(node)
        if scope_role == "condition" or scope_role.startswith("condition_group_"):
            kind = "condition"
```

아래 코드로 교체한다.

```python
        kind = self._classify_kind(node)
```

조건식은 별도 번역 단위로 생성하지 않으므로 `condition` 종류를 강제로 만들지 않는다.

그 외 `CodeUnit` 생성 로직과 `display_start_line`, `display_end_line`은 유지한다.

---

## 2.4 `_direct_detachments()` 교체

현재 `_direct_detachments()` 함수 전체와 그 아래 `_callable_body()` 함수 전체를 삭제한다.

두 함수가 있던 위치에 아래 `_direct_detachments()` 하나만 넣는다.

```python
    def _direct_detachments(
        self,
        node: Node,
    ) -> list[tuple[Node, str]]:
        result: list[tuple[Node, str]] = []
        structural_types = (
            PY_STRUCTURAL_TYPES
            if self._language == "python"
            else TS_STRUCTURAL_TYPES
        )

        if node.type in structural_types:
            callable_types = {
                "function_definition",
                "class_definition",
                "function_declaration",
                "generator_function_declaration",
                "function_expression",
                "generator_function",
                "arrow_function",
                "method_definition",
                "class_declaration",
                "class",
                "abstract_class_declaration",
            }

            if node.type in callable_types:
                candidate = node.child_by_field_name("body")
                if candidate is not None and candidate.type in {
                    "block",
                    "statement_block",
                    "class_body",
                }:
                    result.append(
                        (
                            candidate,
                            self._role_for_owner(
                                node,
                                candidate,
                                "body",
                            ),
                        )
                    )
            else:
                for field_name, role in (
                    ("body", "body"),
                    ("consequence", "consequence"),
                ):
                    candidate = node.child_by_field_name(field_name)
                    if (
                        candidate is not None
                        and self._is_detachable_body(candidate)
                    ):
                        result.append(
                            (
                                candidate,
                                self._role_for_owner(
                                    node,
                                    candidate,
                                    role,
                                ),
                            )
                        )

            if not result and node.type not in callable_types:
                for child in node.named_children:
                    if child.type in {
                        "block",
                        "statement_block",
                        "class_body",
                        "object_type",
                        "interface_body",
                        "enum_body",
                        "switch_body",
                    }:
                        result.append(
                            (
                                child,
                                self._role_for_owner(
                                    node,
                                    child,
                                    self._scope_role(child),
                                ),
                            )
                        )

        if self._language != "python" and node.type in {
            "type_alias_declaration",
            "interface_declaration",
            "enum_declaration",
        }:
            for field_name in ("value", "body"):
                candidate = node.child_by_field_name(field_name)
                if candidate is not None and candidate.type in {
                    "object_type",
                    "interface_body",
                    "enum_body",
                }:
                    result.append((candidate, "members"))

        result.extend(self._direct_clause_detachments(node))

        return self._deduplicate_nodes(result)
```

이 함수에는 다음 호출이 존재하면 안 된다.

```python
result.extend(self._direct_condition_detachments(node))
```

데이터 컨테이너를 분리하는 호출도 추가하지 않는다.

---

## 2.5 조건식 분리 함수 전부 삭제

`segment_code.py`에서 다음 함수들을 전부 삭제한다.

```text
_direct_condition_detachments
_condition_is_complex
_top_level_condition_groups
_flatten_boolean_operator
_boolean_operator_name
_boolean_leaf_count
_boolean_depth
_unwrap_parenthesized
```

삭제 범위는 다음 함수 직전에서 끝나야 한다.

```python
    @staticmethod
    def _role_for_owner(
```

조건식은 `if`, `elif`, `while`의 `code` 안에 원문 그대로 유지한다.

---

## 2.6 `_placeholder()` 교체

기존 `_placeholder()` 함수 전체를 아래 코드로 교체한다.

```python
    def _placeholder(
        self,
        node: Node,
        role: str,
    ) -> str:
        if self._language == "python":
            return ""

        return {
            "statement_block": "{}",
            "class_body": "{}",
            "object_type": "{}",
            "interface_body": "{}",
            "enum_body": "{}",
            "switch_body": "{}",
        }.get(node.type, "")
```

Python의 함수·클래스·제어문은 본문과 절을 제거한 헤더만 `code`에 남긴다.

예:

```python
def work(item):
```

```python
if enabled and ready:
```

```python
except Exception as exc:
```

```python
finally:
```

JavaScript와 TypeScript는 본문을 제거한 뒤 빈 블록 `{}`만 남겨 외부 문장 구조를 보존한다.

예:

```javascript
function work(item) {}
```

```javascript
const result = items.map((item) => {});
```

`BODY`, `MEMBERS`, `CONDITION`, `CLAUSE` 같은 합성 문자열은 생성하지 않는다.

---

## 2.7 `_classify_kind()` 수정

`_classify_kind()`에서 import와 export 검사 다음에 아래 코드를 추가한다.

```python
        if node.type == "decorator":
            return "decorator"
```

정의 종류 집합은 아래 내용으로 맞춘다.

```python
        if node.type in {
            "function_definition",
            "function_declaration",
            "generator_function_declaration",
            "method_definition",
            "class_definition",
            "class_declaration",
            "interface_declaration",
            "type_alias_declaration",
            "enum_declaration",
        }:
            return "definition"
```

정의 종류 집합에 다음 항목을 넣지 않는다.

```python
"decorated_definition"
```

---

## 2.8 유지할 코드

다음 구현은 그대로 유지한다.

```text
CodeUnit.display_start_line
CodeUnit.display_end_line
_display_line_range()
_direct_clause_detachments()
여러 줄 데이터 대입을 전체 문장으로 유지하는 처리
schema_version = 2
build_llm_batches()
```

`code_unit_viewer.py`도 수정하지 않는다.

현재 뷰어는 `display_start_line`과 `display_end_line`을 이미 사용하므로 새 분리 결과를 그대로 표시할 수 있다.

---

# 3. `tests/test_segment_code.py` 전체 교체

현재 `tests/test_segment_code.py`의 전체 내용을 아래 코드로 교체한다.

```python
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
```

테스트 수는 총 7개다.

---

# 4. `README.md`의 분리 규칙 수정

`README.md`에서 `## 2차 분리 규칙`부터 `## 코드 단위 시각화` 직전까지를 아래 내용으로 교체한다.

```markdown
## 코드 번역 단위 규칙

- 독립된 코드 문법 단위 하나를 자연어 문장 하나와 대응한다.
- 코드의 목적, 기능, 역할을 추론하거나 요약하지 않는다.
- 데코레이터는 각각 독립된 번역 단위로 만든다.
- 함수와 클래스 정의는 데코레이터와 분리한다.
- 함수·클래스 정의 단위의 `code`에는 정의 헤더만 넣는다.
- 함수와 클래스 본문의 독립 문장은 정의 단위의 자식으로 저장한다.
- `if`, `elif`, `else`, `for`, `while`, `try`, `except`, `finally`는 각각 독립된 제어문 헤더 단위로 만든다.
- 제어문 조건식은 해당 제어문 단위에 포함하고 별도로 분리하지 않는다.
- 조건이 길어도 글자 수나 의미 그룹을 기준으로 나누지 않는다.
- 변수에 대입되는 리스트·딕셔너리·튜플·세트는 여러 줄이어도 전체 대입문 하나로 유지한다.
- 객체와 배열 리터럴 내부 항목은 독립 번역 단위로 자동 분해하지 않는다.
- `parent_id`와 `depth`는 원본 중첩 위치를 보존하기 위한 메타데이터다.
- 부모와 자식 번역문을 결합해 상위 기능을 만들지 않는다.
- `code`에는 실제 번역할 코드만 저장하고 합성 자리표시자를 넣지 않는다.
- `raw_code`에는 원본 AST 범위 전체를 보존한다.
- `display_start_line`과 `display_end_line`은 시각화 범위다.
- `start_line`과 `end_line`은 원본 AST 전체 범위다.
```

---

# 5. 문법 및 회귀 테스트

저장소 루트에서 다음 명령을 순서대로 실행한다.

```bat
python -m py_compile segment_code.py
python -m py_compile code_unit_viewer.py
python -m unittest discover -s tests -v
```

다음 7개 테스트가 모두 `ok`여야 한다.

```text
test_decorators_are_independent_units
test_decorated_class_is_split
test_except_else_and_finally_are_independent_units
test_multiline_data_assignment_remains_one_unit
test_complex_condition_remains_in_if_unit
test_simple_condition_remains_in_if_unit
test_translation_code_has_no_synthetic_placeholders
```

---

# 6. 실제 프로젝트 재분석

기존 결과를 덮어쓰지 말고 새 JSON을 생성한다.

```bat
python segment_code.py D:\project\12_Gen_Code\codegraph-mvp ^
  --output outputs\backend_unit_v3.json ^
  --batch-size 10
```

출력 로그에서 다음 값이 정상적으로 표시돼야 한다.

```text
files=...
units=...
parse_error_files=...
warnings=...
output=outputs\backend_unit_v3.json
```

단위 수는 고정된 목표값으로 판정하지 않는다.

조건 그룹 단위가 사라지고 데코레이터 단위가 추가되므로 이전 결과와 단위 수가 달라질 수 있다.

---

# 7. 뷰어 실행

다음 명령으로 새 결과를 연다.

```bat
python code_unit_viewer.py outputs\backend_unit_v3.json ^
  --rebuild-index
```

`code_unit_viewer.py` 자체는 수정하지 않는다.

---

# 8. 수동 검수

## 8.1 데코레이터

원본:

```python
@router.post("/users")
@require_auth
async def create_user(payload):
    save(payload)
```

기대 단위:

```text
@router.post("/users")
@require_auth
async def create_user(payload):
save(payload)
```

각 데코레이터는 `kind=decorator`여야 한다.

함수 정의의 `code`에는 데코레이터와 `save(payload)`가 없어야 한다.

---

## 8.2 함수 정의

원본:

```python
def work(item):
    first(item)
    return item
```

기대 단위:

```text
def work(item):
first(item)
return item
```

정의 단위의 `code`는 정확히 다음 형태여야 한다.

```python
def work(item):
```

다음 문자열이 들어가면 실패다.

```text
...
first(item)
return item
```

`raw_code`에는 원본 함수 전체가 있어도 된다.

---

## 8.3 예외 처리

원본:

```python
try:
    run()
except Exception as exc:
    handle(exc)
else:
    save()
finally:
    close()
```

기대 단위:

```text
try:
run()
except Exception as exc:
handle(exc)
else:
save()
finally:
close()
```

`try` 단위에 `except`, `else`, `finally`의 합성 자리표시자가 들어가면 실패다.

---

## 8.4 여러 줄 데이터

원본:

```python
SUBJECT_CLAUSE_MARKERS = [
    "reviews",
    "review",
    "checks",
]
```

기대 단위는 전체 대입문 하나다.

내부 문자열이 각각 별도 단위로 나오면 실패다.

다음도 전체 대입문 하나다.

```python
payload = {
    "original_language": original_language,
    "original_prompt": original_prompt,
    "previous_contexts": previous_contexts,
}
```

---

## 8.5 긴 조건식

원본:

```python
if (
    ("phone" in flow_lower or "call" in flow_lower)
    and (
        normalized_cleaned_lower == "customer"
        or normalized_cleaned_lower == "guest"
    )
):
    continue
```

기대 단위:

```text
if 전체 조건식:
continue
```

조건식의 각 비교식, 괄호 그룹, `and`와 `or` 그룹을 별도 단위로 만들지 않는다.

다음 값이 `scope_role`에 존재하면 실패다.

```text
condition
condition_group_1
condition_group_2
```

---

# 9. LLM 배치 검수

생성한 `outputs\backend_unit_v3.json`에서 임의의 데코레이터 함수가 포함된 `llm_batches`를 확인한다.

배치 항목은 다음 순서여야 한다.

```json
{
  "items": [
    {
      "code": "@router.post(\"/users\")"
    },
    {
      "code": "@require_auth"
    },
    {
      "code": "async def create_user(payload):"
    },
    {
      "code": "save(payload)"
    }
  ]
}
```

실제 항목에는 기존처럼 `id`, `parent_id`, `scope_role`도 포함된다.

데코레이터와 함수 정의가 하나의 `code`에 합쳐져 있으면 실패다.

---

# 10. 완료 조건

다음 조건을 모두 만족해야 한다.

1. 데코레이터 하나마다 독립 단위가 생성된다.
2. 함수와 클래스 정의는 데코레이터를 포함하지 않는다.
3. 함수와 클래스 정의의 `code`에는 본문이 없다.
4. 정의 본문의 문장은 각각 별도 단위다.
5. `except`, `elif`, `else`, `finally`가 각각 독립 단위다.
6. 각 절의 본문은 해당 절 단위의 자식이다.
7. 조건식은 제어문 헤더 안에 전체가 유지된다.
8. 조건식 그룹 단위가 생성되지 않는다.
9. 여러 줄 데이터 대입은 전체 문장 하나다.
10. 데이터 내부 항목이 개별 LLM 입력으로 생성되지 않는다.
11. `code`에 합성 자리표시자가 없다.
12. `raw_code`에는 원본 범위가 보존된다.
13. 부모·자식 정보는 구조 메타데이터로만 유지된다.
14. 회귀 테스트 7개가 모두 통과한다.
15. 새 JSON을 시각화 도구에서 정상적으로 열 수 있다.
