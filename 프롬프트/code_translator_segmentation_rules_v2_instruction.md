# code-translator 분리 규칙 2차 수정 지시문

## 작업 대상

- 저장소: `srejis/code-translator`
- 수정 대상:
  - `segment_code.py`
  - `code_unit_viewer.py`
  - `README.md`
- 새로 생성:
  - `tests/test_segment_code.py`

이 지시문은 현재 구현을 다시 작성하는 것이 아니라, 확인된 과분리·누락 문제만 수정한다.

---

# 1. 수정 목적

현재 시각화 결과에서 다음 문제가 확인됐다.

1. 함수 정의 단위가 함수 본문과 시각적으로 겹쳐 본문이 두 번 처리되는 것처럼 보인다.
2. `except Exception as exc:`, `elif`, `else`, `finally` 같은 절 헤더가 독립 LLM 입력 단위로 나오지 않는다.
3. 함수와 클래스 위의 데코레이터가 정의와 분리될 가능성이 있다.
4. 여러 줄 리스트·딕셔너리 대입이 내부 항목마다 `member` 또는 `data` 단위로 과분리된다.
5. 복잡한 조건식은 한 덩어리로 너무 길게 남지만, 각 비교식을 한 줄씩 분리하는 것도 적절하지 않다.

수정 후 기본 원칙은 다음과 같다.

```text
데코레이터 + 함수/클래스 정의부 = 정의 단위 1개
정의 본문 = 자식 실행 단위
except/elif/else/finally 헤더 = 각각 독립 제어 단위
리스트·딕셔너리·튜플·세트 대입 = 전체 대입문 1개
복잡 조건식 = 주요 논리 그룹 단위
단순 조건식 = 기존 if/while 단위에 유지
```

글자 수는 조건식 분리의 보조 기준으로만 사용한다. 데이터 대입은 글자 수만으로 쪼개지 않는다.

---

# 2. `segment_code.py` 수정

## 2.1 데이터 분리 상수 제거 및 새 상수 추가

`TS_DATA_OWNER_FIELDS`와 `PY_DATA_OWNER_FIELDS` 선언 전체를 삭제한다.

삭제한 위치에 아래 코드를 넣는다.

```python
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

PY_CONDITION_OWNER_TYPES = {
    "if_statement",
    "elif_clause",
    "while_statement",
}

CONDITION_CHAR_THRESHOLD = 500
CONDITION_LINE_THRESHOLD = 10
CONDITION_LEAF_THRESHOLD = 8
CONDITION_DEPTH_THRESHOLD = 3
CONDITION_GROUP_MAX = 4
CONDITION_GROUP_MIN_LEAVES = 3
CONDITION_GROUP_MIN_CHARS = 180
```

이 변경으로 여러 줄 데이터 컨테이너를 자동으로 자식 항목으로 분해하던 전역 규칙을 제거한다.

---

## 2.2 `CodeUnit` 필드 추가

`CodeUnit` 데이터 클래스에서 `end_line` 바로 다음에 아래 두 필드를 추가한다.

```python
    display_start_line: int
    display_end_line: int
```

수정 후 관련 부분은 다음 구조여야 한다.

```python
@dataclass
class CodeUnit:
    id: str
    parent_id: str | None
    file: str
    language: str
    node_type: str
    kind: str
    scope_role: str
    depth: int
    start_line: int
    end_line: int
    display_start_line: int
    display_end_line: int
    start_column: int
    end_column: int
    code: str
    raw_code: str
    source_hash: str
    warnings: list[str] = field(default_factory=list)
```

`start_line`과 `end_line`은 원본 AST 노드 전체 범위다.

`display_start_line`과 `display_end_line`은 시각화에서 부모 정의·제어문 헤더를 표시할 범위다.

---

## 2.3 `_walk_scope()` 교체

기존 `_walk_scope()` 함수 전체를 아래 코드로 교체한다.

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

            # 정상적인 Python AST에서는 decorator가 decorated_definition
            # 내부에 포함된다. 독립 decorator 단위가 생성되는 것을 방지한다.
            if self._language == "python" and child.type == "decorator":
                continue

            self._emit_unit(
                child,
                parent_id=parent_id,
                depth=depth,
                scope_role=scope_role,
            )
```

---

## 2.4 `_emit_unit()` 교체

기존 `_emit_unit()` 함수 전체를 아래 코드로 교체한다.

```python
    def _emit_unit(
        self,
        node: Node,
        *,
        parent_id: str | None,
        depth: int,
        scope_role: str,
    ) -> None:
        raw = self._text(node)
        if not raw.strip():
            return

        detachments = self._find_detachments(node)
        normalized = self._replace_detachments(node, detachments)
        normalized = self._trim_outer_blank_lines(normalized)

        if not normalized.strip() or self._only_symbols(normalized):
            return

        unit_id = self._next_id(node)
        warnings: list[str] = []

        if len(normalized) > self.max_unit_chars:
            warnings.append(
                f"large_unit:{len(normalized)}chars; inspect before LLM batching"
            )

        if "ERROR" in {child.type for child in node.named_children}:
            warnings.append("contains_parse_error")

        display_start_line, display_end_line = self._display_line_range(
            node,
            detachments,
        )

        kind = self._classify_kind(node)
        if scope_role == "condition" or scope_role.startswith("condition_group_"):
            kind = "condition"

        unit = CodeUnit(
            id=unit_id,
            parent_id=parent_id,
            file=self._file,
            language=self._language,
            node_type=node.type,
            kind=kind,
            scope_role=scope_role,
            depth=depth,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            display_start_line=display_start_line,
            display_end_line=display_end_line,
            start_column=node.start_point[1],
            end_column=node.end_point[1],
            code=normalized,
            raw_code=raw,
            source_hash=hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12],
            warnings=warnings,
        )
        self._units.append(unit)

        for detachment in sorted(
            detachments,
            key=lambda item: (item.node.start_byte, item.node.end_byte),
        ):
            body = detachment.node
            if body.type in SCOPE_TYPES:
                self._walk_scope(
                    body,
                    parent_id=unit_id,
                    depth=depth + 1,
                    scope_role=detachment.role,
                )
            else:
                self._emit_unit(
                    body,
                    parent_id=unit_id,
                    depth=depth + 1,
                    scope_role=detachment.role,
                )
```

---

## 2.5 `_direct_detachments()`부터 `_direct_data_detachments()`까지 교체

기존 `_direct_detachments()` 함수와 `_direct_data_detachments()` 함수 전체를 삭제한다.

그 자리에 아래 코드를 넣는다.

```python
    def _direct_detachments(self, node: Node) -> list[tuple[Node, str]]:
        result: list[tuple[Node, str]] = []
        structural_types = (
            PY_STRUCTURAL_TYPES
            if self._language == "python"
            else TS_STRUCTURAL_TYPES
        )

        if node.type in structural_types:
            callable_types = {
                "function_definition",
                "function_declaration",
                "generator_function_declaration",
                "function_expression",
                "generator_function",
                "arrow_function",
                "method_definition",
                "decorated_definition",
            }

            if node.type in callable_types:
                candidate = self._callable_body(node)
                if candidate is not None and candidate.type in {
                    "block",
                    "statement_block",
                    "class_body",
                }:
                    result.append(
                        (
                            candidate,
                            self._role_for_owner(node, candidate, "body"),
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
        result.extend(self._direct_condition_detachments(node))

        # 리스트·딕셔너리·객체·배열 대입은 더 이상 내부 항목으로
        # 자동 분해하지 않는다. 전체 대입문이 LLM 입력 단위가 된다.
        return self._deduplicate_nodes(result)

    def _callable_body(self, node: Node) -> Node | None:
        target = node

        if node.type == "decorated_definition":
            target = (
                node.child_by_field_name("definition")
                or node.child_by_field_name("body")
            )

            definition_types = {
                "function_definition",
                "class_definition",
            }

            if target is None or target.type not in definition_types:
                target = next(
                    (
                        child
                        for child in reversed(node.named_children)
                        if child.type in definition_types
                    ),
                    None,
                )

        if target is None:
            return None

        return target.child_by_field_name("body")

    def _direct_clause_detachments(
        self,
        node: Node,
    ) -> list[tuple[Node, str]]:
        if self._language != "python":
            return []

        if node.type not in PY_CLAUSE_PARENT_TYPES:
            return []

        result: list[tuple[Node, str]] = []
        for child in node.named_children:
            role = PY_CLAUSE_ROLES.get(child.type)
            if role is not None:
                result.append((child, role))

        return result

    def _direct_condition_detachments(
        self,
        node: Node,
    ) -> list[tuple[Node, str]]:
        if self._language != "python":
            return []

        if node.type not in PY_CONDITION_OWNER_TYPES:
            return []

        condition = node.child_by_field_name("condition")
        if condition is None:
            return []

        if not self._condition_is_complex(condition):
            return []

        groups = self._top_level_condition_groups(condition)
        if groups:
            return [
                (group, f"condition_group_{index}")
                for index, group in enumerate(groups, start=1)
            ]

        # 긴 OR 체인처럼 의미 있는 괄호 그룹이 없는 조건은
        # 각 비교식으로 쪼개지 않고 조건 전체를 자식 단위 하나로 둔다.
        return [(condition, "condition")]

    def _condition_is_complex(self, node: Node) -> bool:
        text = self._text(node)
        return any(
            (
                len(text) >= CONDITION_CHAR_THRESHOLD,
                text.count("\n") + 1 >= CONDITION_LINE_THRESHOLD,
                self._boolean_leaf_count(node) >= CONDITION_LEAF_THRESHOLD,
                self._boolean_depth(node) >= CONDITION_DEPTH_THRESHOLD,
            )
        )

    def _top_level_condition_groups(self, node: Node) -> list[Node]:
        root = self._unwrap_parenthesized(node)
        if root.type != "boolean_operator":
            return []

        operator = self._boolean_operator_name(root)
        if operator is None:
            return []

        groups = self._flatten_boolean_operator(root, operator)
        if not 2 <= len(groups) <= CONDITION_GROUP_MAX:
            return []

        significant_count = sum(
            1
            for group in groups
            if (
                self._boolean_leaf_count(group)
                >= CONDITION_GROUP_MIN_LEAVES
                or len(self._text(group))
                >= CONDITION_GROUP_MIN_CHARS
            )
        )

        if significant_count < 2:
            return []

        return groups

    def _flatten_boolean_operator(
        self,
        node: Node,
        operator: str,
    ) -> list[Node]:
        # 명시적으로 괄호로 묶인 표현식은 하나의 의미 그룹으로 유지한다.
        if node.type == "parenthesized_expression":
            return [node]

        if (
            node.type != "boolean_operator"
            or self._boolean_operator_name(node) != operator
        ):
            return [node]

        output: list[Node] = []
        for child in node.named_children:
            output.extend(
                self._flatten_boolean_operator(child, operator)
            )
        return output

    def _boolean_operator_name(self, node: Node) -> str | None:
        if node.type != "boolean_operator":
            return None

        for child in node.children:
            token = self._text(child).strip()
            if token in {"and", "or"}:
                return token

        return None

    def _boolean_leaf_count(self, node: Node) -> int:
        current = self._unwrap_parenthesized(node)
        if current.type != "boolean_operator":
            return 1

        return sum(
            self._boolean_leaf_count(child)
            for child in current.named_children
        )

    def _boolean_depth(self, node: Node) -> int:
        current = self._unwrap_parenthesized(node)
        if current.type != "boolean_operator":
            return 0

        child_depths = [
            self._boolean_depth(child)
            for child in current.named_children
        ]
        return 1 + max(child_depths, default=0)

    @staticmethod
    def _unwrap_parenthesized(node: Node) -> Node:
        current = node
        while (
            current.type == "parenthesized_expression"
            and len(current.named_children) == 1
        ):
            current = current.named_children[0]
        return current
```

중요: 기존의 다음 호출은 남아 있으면 안 된다.

```python
result.extend(self._direct_data_detachments(node))
```

또한 `_direct_data_detachments()` 함수도 남기지 않는다.

---

## 2.6 `_placeholder()` 교체

기존 `_placeholder()` 함수 전체를 아래 코드로 교체한다.

```python
    def _placeholder(self, node: Node, role: str) -> str:
        if self._language == "python":
            if role == "condition":
                return "(CONDITION)"

            if role.startswith("condition_group_"):
                return f"({role.upper()})"

            if role.endswith("_clause"):
                return f"# <{role.upper()}>"

            return {
                "block": "...",
                "dictionary": "{...}",
                "list": "[...]",
                "set": "{...}",
                "tuple": "(...)",
            }.get(node.type, "...")

        if role == "condition":
            return "(CONDITION)"

        if role.startswith("condition_group_"):
            return f"({role.upper()})"

        return {
            "statement_block": "{ /* BODY */ }",
            "class_body": "{ /* MEMBERS */ }",
            "object_type": "{ /* MEMBERS */ }",
            "interface_body": "{ /* MEMBERS */ }",
            "enum_body": "{ /* MEMBERS */ }",
            "switch_body": "{ /* CASES */ }",
            "object": "{ /* DATA */ }",
            "array": "[ /* ITEMS */ ]",
        }.get(node.type, f"/* {role.upper()} */")
```

---

## 2.7 `_display_line_range()` 추가

`_scope_role()`과 `_classify_kind()` 사이에 아래 함수를 추가한다.

```python
    def _display_line_range(
        self,
        node: Node,
        detachments: Sequence[Detachment],
    ) -> tuple[int, int]:
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1

        body_types = {
            "block",
            "statement_block",
            "class_body",
            "switch_body",
        }

        body_detachments = [
            item
            for item in detachments
            if item.node.type in body_types
        ]

        if not body_detachments:
            return start_line, end_line

        body = min(
            body_detachments,
            key=lambda item: item.node.start_byte,
        ).node

        if self._language == "python":
            if body.start_point[0] > node.start_point[0]:
                header_end_line = body.start_point[0]
            else:
                header_end_line = start_line
        else:
            # JS/TS statement_block는 여는 중괄호 위치부터 시작하므로
            # 해당 줄까지 정의 헤더로 표시한다.
            header_end_line = body.start_point[0] + 1

        return start_line, max(start_line, header_end_line)
```

이 필드는 LLM 입력을 바꾸지 않는다.

시각화에서 함수 전체를 부모 범위로 강조하지 않고 데코레이터와 정의 헤더만 강조하기 위해 사용한다.

---

## 2.8 `_classify_kind()` 정의 종류 수정

기존 `_classify_kind()`의 정의 노드 집합에 다음 항목을 추가한다.

```python
"decorated_definition",
```

수정된 관련 부분은 다음과 같아야 한다.

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
            "decorated_definition",
        }:
            return "definition"
```

---

## 2.9 출력 스키마 버전 변경

`serialize_result()`에서 다음 값을 찾는다.

```python
"schema_version": 1,
```

아래처럼 바꾼다.

```python
"schema_version": 2,
```

---

# 3. `code_unit_viewer.py` 수정

새 JSON의 `display_start_line`과 `display_end_line`을 사용해 부모 정의 범위를 표시한다.

이 필드가 없는 이전 JSON도 계속 열 수 있어야 한다.

---

## 3.1 뷰어 버전 변경

다음을 찾는다.

```python
VIEWER_VERSION = 1
```

아래처럼 바꾼다.

```python
VIEWER_VERSION = 2
```

이 변경으로 기존 SQLite 캐시가 자동 재생성된다.

---

## 3.2 SQLite 스키마 수정

`units` 테이블에서 다음 부분을 찾는다.

```sql
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    start_column INTEGER NOT NULL,
```

아래처럼 교체한다.

```sql
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    display_start_line INTEGER NOT NULL,
    display_end_line INTEGER NOT NULL,
    start_column INTEGER NOT NULL,
```

---

## 3.3 `unit_insert` 교체

기존 `unit_insert` 문자열 전체를 아래 코드로 교체한다.

```python
        unit_insert = """
            INSERT INTO units(
                id, parent_id, file_path, ordinal, language, node_type, kind,
                scope_role, depth, start_line, end_line,
                display_start_line, display_end_line,
                start_column, end_column, code, raw_code, source_hash,
                warnings_json, warning_count
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
        """
```

---

## 3.4 SQLite 단위 행 생성 부분 수정

`unit_rows.append(( ... ))` 내부에서 기존 줄 범위 입력 부분을 아래 구조로 교체한다.

```python
                    start_line = int(unit.get("start_line", 1) or 1)
                    end_line = int(unit.get("end_line", 1) or 1)
                    display_start_line = int(
                        unit.get("display_start_line", start_line)
                        or start_line
                    )
                    display_end_line = int(
                        unit.get("display_end_line", end_line)
                        or end_line
                    )

                    unit_rows.append(
                        (
                            str(unit.get("id", "")),
                            unit.get("parent_id"),
                            file_path,
                            ordinal,
                            str(unit.get("language", language)),
                            str(unit.get("node_type", "")),
                            str(unit.get("kind", "statement")),
                            str(unit.get("scope_role", "")),
                            int(unit.get("depth", 0) or 0),
                            start_line,
                            end_line,
                            display_start_line,
                            display_end_line,
                            int(unit.get("start_column", 0) or 0),
                            int(unit.get("end_column", 0) or 0),
                            str(unit.get("code", "")),
                            str(unit.get("raw_code", "")),
                            str(unit.get("source_hash", "")),
                            json.dumps(warnings, ensure_ascii=False),
                            len(warnings),
                        )
                    )
```

기존 `unit_rows.append()` 전체를 이 코드로 교체한다.

---

## 3.5 파일 마커 조회 쿼리 수정

`file_payload()`의 마커 조회에서 다음 필드를 추가한다.

```sql
display_start_line, display_end_line,
```

수정 후 쿼리는 다음과 같아야 한다.

```python
        marker_rows = conn.execute(
            """
            SELECT id, parent_id, ordinal, node_type, kind, scope_role, depth,
                   start_line, end_line,
                   display_start_line, display_end_line,
                   start_column, end_column, warning_count
            FROM units
            WHERE file_path = ?
            ORDER BY ordinal
            """,
            (file_path,),
        )
```

---

## 3.6 자식 단위 조회 쿼리 수정

`unit_payload()`의 자식 조회를 아래처럼 수정한다.

```python
        children = conn.execute(
            """
            SELECT id, ordinal, node_type, kind,
                   start_line, end_line,
                   display_start_line, display_end_line,
                   depth, warning_count
            FROM units
            WHERE parent_id = ?
            ORDER BY ordinal
            """,
            (unit_id,),
        )
```

---

## 3.7 프런트엔드 표시 범위 수정

`selectFile()`에서 다음 두 줄을 찾는다.

```javascript
state.starts = groupMarkers(state.markers, 'start_line');
state.ends = groupMarkers(state.markers, 'end_line');
```

아래처럼 교체한다.

```javascript
state.starts = groupMarkers(
  state.markers,
  'display_start_line',
);
state.ends = groupMarkers(
  state.markers,
  'display_end_line',
);
```

---

## 3.8 코드 줄 클릭 판정 수정

`renderVisibleLines()` 안에서 다음 코드를 찾는다.

```javascript
const candidates = starts.length ? starts : state.markers.filter((marker) => marker.start_line <= lineNumber && marker.end_line >= lineNumber);
```

아래처럼 교체한다.

```javascript
const candidates = starts.length
  ? starts
  : state.markers.filter(
      (marker) =>
        marker.display_start_line <= lineNumber
        && marker.display_end_line >= lineNumber,
    );
```

바로 아래 정렬 코드도 다음처럼 교체한다.

```javascript
const best = [...candidates].sort(
  (a, b) =>
    b.depth - a.depth
    || (
      a.display_end_line - a.display_start_line
    ) - (
      b.display_end_line - b.display_start_line
    ),
)[0];
```

---

## 3.9 마커 제목 수정

마커 버튼의 `title` 설정을 아래처럼 교체한다.

```javascript
button.title = `${marker.id}
${marker.kind} · display ${marker.display_start_line}-${marker.display_end_line}
source ${marker.start_line}-${marker.end_line}`;
```

---

## 3.10 단위 목록 줄 범위 수정

`renderVisibleUnits()` 안의 다음 부분을 찾는다.

```javascript
<span>${marker.start_line}-${marker.end_line}</span>
```

아래처럼 교체한다.

```javascript
<span>${marker.display_start_line}-${marker.display_end_line}</span>
```

---

## 3.11 단위 선택 범위 수정

`selectUnit()` 안의 다음 코드를 찾는다.

```javascript
state.selectedRange = [marker.start_line, marker.end_line];
```

아래처럼 교체한다.

```javascript
state.selectedRange = [
  marker.display_start_line,
  marker.display_end_line,
];
```

다음 코드도 찾는다.

```javascript
codeViewport.scrollTop = Math.max(0, (marker.start_line - 2) * state.lineHeight);
```

아래처럼 교체한다.

```javascript
codeViewport.scrollTop = Math.max(
  0,
  (marker.display_start_line - 2) * state.lineHeight,
);
```

---

## 3.12 상세 패널 범위 표시 수정

`renderInspector()`의 `unitLines` 설정을 아래처럼 교체한다.

```javascript
const sourceRange = `${unit.start_line}:${unit.start_column} → ${unit.end_line}:${unit.end_column}`;
const displayRange = `${unit.display_start_line} → ${unit.display_end_line}`;

$('unitLines').textContent =
  unit.start_line === unit.display_start_line
  && unit.end_line === unit.display_end_line
    ? sourceRange
    : `${displayRange} · source ${sourceRange}`;
```

자식 버튼 텍스트도 아래처럼 교체한다.

```javascript
button.textContent =
  `${child.display_start_line}-${child.display_end_line}`
  + ` · ${child.kind} · ${child.id}`;
```

---

# 4. 회귀 테스트 생성

저장소 루트에 `tests` 폴더를 만들고 `tests/test_segment_code.py`를 생성한다.

아래 코드를 파일 전체 내용으로 넣는다.

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
```

---

# 5. README 수정

기존 `README.md`의 분리 원칙 아래에 다음 내용을 추가한다.

```markdown
## 2차 분리 규칙

- 데코레이터는 바로 뒤 함수 또는 클래스 정의와 같은 단위에 포함한다.
- 함수·클래스 정의 단위의 `code`에는 본문을 넣지 않는다.
- `except`, `elif`, `else`, `finally`는 각각 독립 제어 단위로 만든다.
- 각 절의 본문은 해당 절 단위의 자식으로 둔다.
- 변수에 대입되는 리스트·딕셔너리·튜플·세트는 여러 줄이어도 전체 대입문 하나로 유지한다.
- 데이터 내부 항목은 `member` 단위로 자동 분해하지 않는다.
- 단순 조건은 제어문 헤더에 유지한다.
- 복잡 조건은 AST의 최상위 논리 그룹을 기준으로 분리한다.
- 긴 OR 체인처럼 명확한 상위 그룹이 없으면 각 비교식이 아니라 조건 전체를 자식 단위 하나로 둔다.
- 글자 수는 조건식 복잡도 판단의 보조 기준이며 데이터 분리 기준으로 사용하지 않는다.
- `display_start_line`과 `display_end_line`은 시각화 범위이고, `start_line`과 `end_line`은 원본 AST 전체 범위다.
```

---

# 6. 테스트 실행

저장소 루트에서 다음 명령을 실행한다.

```bat
python -m py_compile segment_code.py
python -m py_compile code_unit_viewer.py
python -m unittest discover -s tests -v
```

다섯 테스트가 모두 통과해야 한다.

---

# 7. 실제 프로젝트 재분석

기존 JSON을 덮어쓰기 전에 새 파일로 결과를 생성한다.

```bat
python segment_code.py D:\project\12_Gen_Code\codegraph-mvp ^
  --output outputs\backend_unit_v2.json ^
  --batch-size 10
```

기존 `backend_unit.json`과 단위 개수를 비교한다.

데이터 `member` 과분리를 제거했으므로 전체 단위 수는 기존 `99,673`개보다 크게 줄어드는 것이 정상이다.

정확한 목표 개수는 고정하지 않는다.

---

# 8. 뷰어 실행

새 JSON을 열고 SQLite 인덱스를 강제로 다시 만든다.

```bat
python code_unit_viewer.py outputs\backend_unit_v2.json ^
  --rebuild-index
```

---

# 9. 수동 검수 항목

## 함수 정의

다음 구조를 확인한다.

```python
@decorator
def example(value):
    first()
    second()
```

기대 결과:

```text
정의 단위:
@decorator
def example(value):
    ...

자식 단위:
first()

자식 단위:
second()
```

- 정의 단위의 `code`에는 `first()`와 `second()`가 없어야 한다.
- `raw_code`에는 원본 전체가 있어도 된다.
- 코드 화면에서 정의 단위 강조는 데코레이터와 `def` 헤더까지만 표시돼야 한다.

## 예외 처리

다음 코드에서 각각 별도 단위가 보여야 한다.

```text
try:
except Exception as exc:
finally:
```

`handle(exc)`는 `except` 단위의 자식이어야 한다.

## 데이터 대입

다음은 각각 단위 하나여야 한다.

```text
SUBJECT_CLAUSE_MARKERS = [...]
payload = {...}
```

내부 문자열과 키·값 쌍이 독립 LLM 입력으로 나오면 실패다.

## 복잡 조건

예시의 긴 `if` 조건은 다음 구조여야 한다.

```text
if 부모 단위
├─ condition_group_1
├─ condition_group_2
└─ continue
```

각 `"phone"` 비교식이 하나씩 별도 단위로 나오면 실패다.

---

# 10. 이번 수정에서 하지 않을 것

- 데이터 리스트를 글자 수로 임의 청크 분리하지 않는다.
- 딕셔너리 키·값을 개별 LLM 입력으로 만들지 않는다.
- 단순 조건식을 무조건 별도 단위로 만들지 않는다.
- 긴 OR 체인의 각 비교를 한 문장씩 분리하지 않는다.
- LLM 번역 규칙은 수정하지 않는다.
- 최대 배치 크기 10은 변경하지 않는다.
- 기존 `raw_code`는 제거하지 않는다.

---

# 완료 조건

다음 조건을 모두 만족해야 한다.

1. 데코레이터와 정의가 하나의 단위다.
2. 정의 단위의 `code`에서 본문이 제거된다.
3. 정의의 시각화 범위가 본문과 겹치지 않는다.
4. `except`, `elif`, `else`, `finally`가 독립 단위다.
5. 각 절의 실행문이 해당 절의 자식이다.
6. 여러 줄 데이터 대입이 하나의 단위다.
7. 데이터 내부 항목이 LLM 배치에 개별 입력되지 않는다.
8. 복잡 조건은 주요 논리 그룹으로 분리된다.
9. 단순 조건은 기존 제어문에 유지된다.
10. 회귀 테스트 5개가 모두 통과한다.
