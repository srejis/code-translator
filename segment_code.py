#!/usr/bin/env python3
"""Split a source repository into one-to-one LLM translation units.

Initial target: srejis/previbemap.
Implementation language: Python.
Parsed source languages: Python, TypeScript, TSX, JavaScript, JSX, MJS, CJS.

The segmenter does not translate code. It only determines the exact source unit
that will later receive exactly one natural-language sentence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Sequence

try:
    from tree_sitter import Language, Node, Parser
except ImportError as exc:  # pragma: no cover - dependency check
    raise SystemExit(
        "tree-sitter is not installed. Run: pip install -r requirements.txt"
    ) from exc


SUPPORTED_EXTENSIONS: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
}

SKIP_DIRECTORIES = {
    ".git",
    ".next",
    ".turbo",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "dist",
    "build",
    "coverage",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}

COMMENT_TYPES = {"comment"}

# Nodes whose named children are independent code units when the node is
# detached as a body/data container.
SCOPE_TYPES = {
    # roots / executable bodies
    "module",
    "program",
    "block",  # Python
    "statement_block",  # JS / TS
    # definition/data bodies
    "class_body",
    "object_type",
    "interface_body",
    "enum_body",
    "switch_body",
    "object",
    "array",
    "dictionary",
    "list",
    "set",
    "tuple",
}

# Expressions that remain part of the surrounding statement unless an owner
# explicitly marks them as a detachable data container.
ATOMIC_CONTAINER_TYPES = {
    "object",
    "array",
    "dictionary",
    "list",
    "set",
    "tuple",
}

TS_STRUCTURAL_TYPES = {
    "function_declaration",
    "generator_function_declaration",
    "function_expression",
    "generator_function",
    "arrow_function",
    "method_definition",
    "class_declaration",
    "class",
    "abstract_class_declaration",
    "if_statement",
    "else_clause",
    "for_statement",
    "for_in_statement",
    "while_statement",
    "do_statement",
    "try_statement",
    "catch_clause",
    "finally_clause",
    "switch_statement",
    "with_statement",
    "labeled_statement",
    "interface_declaration",
    "enum_declaration",
    "type_alias_declaration",
    "namespace_declaration",
    "internal_module",
    "ambient_declaration",
}

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


@dataclass(frozen=True)
class Detachment:
    node: Node
    role: str
    placeholder: str


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


@dataclass
class FileResult:
    file: str
    language: str
    parse_has_error: bool
    units: list[CodeUnit]
    diagnostics: list[dict[str, object]]


class GrammarRegistry:
    """Lazily loads prebuilt Tree-sitter language wheels."""

    def __init__(self) -> None:
        self._parsers: dict[str, Parser] = {}

    def parser_for(self, language: str) -> Parser:
        if language in self._parsers:
            return self._parsers[language]

        if language == "python":
            try:
                import tree_sitter_python as grammar
            except ImportError as exc:
                self._missing("tree-sitter-python", exc)
            language_object = Language(grammar.language())
        elif language == "typescript":
            try:
                import tree_sitter_typescript as grammar
            except ImportError as exc:
                self._missing("tree-sitter-typescript", exc)
            language_object = Language(grammar.language_typescript())
        elif language == "tsx":
            try:
                import tree_sitter_typescript as grammar
            except ImportError as exc:
                self._missing("tree-sitter-typescript", exc)
            language_object = Language(grammar.language_tsx())
        elif language == "javascript":
            try:
                import tree_sitter_javascript as grammar
            except ImportError as exc:
                self._missing("tree-sitter-javascript", exc)
            language_object = Language(grammar.language())
        else:  # pragma: no cover - guarded by extension map
            raise ValueError(f"Unsupported language: {language}")

        parser = Parser(language_object)
        self._parsers[language] = parser
        return parser

    @staticmethod
    def _missing(package: str, exc: ImportError) -> None:
        raise SystemExit(
            f"{package} is not installed. Run: pip install -r requirements.txt"
        ) from exc


class CodeSegmenter:
    """AST-based source splitter with hierarchical parent/child units."""

    def __init__(self, *, max_unit_chars: int = 4000) -> None:
        self.registry = GrammarRegistry()
        self.max_unit_chars = max_unit_chars
        self._source = b""
        self._language = ""
        self._file = ""
        self._units: list[CodeUnit] = []
        self._diagnostics: list[dict[str, object]] = []
        self._sequence = 0

    def segment_file(self, path: Path, *, relative_to: Path) -> FileResult:
        self._source = path.read_bytes()
        self._language = SUPPORTED_EXTENSIONS[path.suffix.lower()]
        self._file = path.relative_to(relative_to).as_posix()
        self._units = []
        self._diagnostics = []
        self._sequence = 0

        parser = self.registry.parser_for(self._language)
        tree = parser.parse(self._source)
        self._collect_parse_diagnostics(tree.root_node)
        self._walk_scope(tree.root_node, parent_id=None, depth=0, scope_role="file")

        return FileResult(
            file=self._file,
            language=self._language,
            parse_has_error=tree.root_node.has_error,
            units=self._units,
            diagnostics=self._diagnostics,
        )

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

    def _find_detachments(self, root: Node) -> list[Detachment]:
        found: list[Detachment] = []
        occupied: list[tuple[int, int]] = []

        def add(node: Node, role: str) -> bool:
            span = (node.start_byte, node.end_byte)
            if node.start_byte < root.start_byte or node.end_byte > root.end_byte:
                return False
            if any(self._overlaps(span, other) for other in occupied):
                return False
            occupied.append(span)
            found.append(
                Detachment(
                    node=node,
                    role=role,
                    placeholder=self._placeholder(node, role),
                )
            )
            return True

        def visit(current: Node) -> None:
            direct = self._direct_detachments(current)
            detached_ids: set[tuple[int, int]] = set()
            for body, role in direct:
                if add(body, role):
                    detached_ids.add((body.start_byte, body.end_byte))

            for child in current.named_children:
                child_span = (child.start_byte, child.end_byte)
                if child_span in detached_ids:
                    continue

                # An object/array inside a call argument or expression remains
                # part of that one statement. It is traversed only when its
                # owner explicitly detached it as a data body.
                if child.type in ATOMIC_CONTAINER_TYPES:
                    continue

                visit(child)

        visit(root)
        return found

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

    @staticmethod
    def _role_for_owner(owner: Node, body: Node, default: str) -> str:
        if body.type in {"class_body", "object_type", "interface_body", "enum_body"}:
            return "members"
        if body.type == "switch_body":
            return "cases"
        if owner.type in {"else_clause"}:
            return "alternative"
        if owner.type in {"catch_clause", "except_clause"}:
            return "catch"
        if owner.type == "finally_clause":
            return "finally"
        return default

    @staticmethod
    def _deduplicate_nodes(
        items: Sequence[tuple[Node, str]],
    ) -> list[tuple[Node, str]]:
        seen: set[tuple[int, int]] = set()
        output: list[tuple[Node, str]] = []
        for node, role in items:
            key = (node.start_byte, node.end_byte)
            if key in seen:
                continue
            seen.add(key)
            output.append((node, role))
        return output

    def _replace_detachments(
        self, root: Node, detachments: Sequence[Detachment]
    ) -> str:
        chunk = bytearray(self._source[root.start_byte : root.end_byte])
        for detachment in sorted(
            detachments, key=lambda item: item.node.start_byte, reverse=True
        ):
            start = detachment.node.start_byte - root.start_byte
            end = detachment.node.end_byte - root.start_byte
            chunk[start:end] = detachment.placeholder.encode("utf-8")
        return bytes(chunk).decode("utf-8", errors="replace")

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

    @staticmethod
    def _is_detachable_body(node: Node) -> bool:
        if node.type in SCOPE_TYPES:
            return True
        # Single-statement control bodies are also detached and recursively
        # emitted as one child unit.
        return node.is_named and node.type not in {
            "identifier",
            "property_identifier",
            "type_identifier",
        }

    @staticmethod
    def _scope_role(node: Node) -> str:
        return {
            "class_body": "members",
            "object_type": "members",
            "interface_body": "members",
            "enum_body": "members",
            "switch_body": "cases",
            "object": "data",
            "dictionary": "data",
            "array": "items",
            "list": "items",
            "set": "items",
            "tuple": "items",
        }.get(node.type, "body")

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

    def _classify_kind(self, node: Node) -> str:
        if node.type in COMMENT_TYPES:
            return "comment"
        if "import" in node.type:
            return "import"
        if "export" in node.type:
            return "export"
        if node.type == "decorator":
            return "decorator"
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
        if node.type in {"pair", "property_signature", "method_signature"}:
            return "member"
        if node.type in {"object", "dictionary", "array", "list", "set", "tuple"}:
            return "data"
        if node.type in {
            "if_statement",
            "elif_clause",
            "else_clause",
            "for_statement",
            "for_in_statement",
            "while_statement",
            "do_statement",
            "try_statement",
            "except_clause",
            "catch_clause",
            "finally_clause",
            "switch_statement",
            "match_statement",
            "case_clause",
        }:
            return "control"
        return "statement"

    def _next_id(self, node: Node) -> str:
        self._sequence += 1
        fingerprint = hashlib.sha1(
            f"{self._file}:{node.start_byte}:{node.end_byte}:{node.type}".encode(
                "utf-8"
            )
        ).hexdigest()[:8]
        return f"unit_{self._sequence:06d}_{fingerprint}"

    def _text(self, node: Node) -> str:
        return self._source[node.start_byte : node.end_byte].decode(
            "utf-8", errors="replace"
        )

    @staticmethod
    def _trim_outer_blank_lines(text: str) -> str:
        lines = text.splitlines()
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        return "\n".join(lines)

    @staticmethod
    def _only_symbols(text: str) -> bool:
        meaningful = [char for char in text if char.isalnum() or char == "_"]
        return not meaningful

    @staticmethod
    def _is_multiline(node: Node) -> bool:
        return node.start_point[0] != node.end_point[0]

    @staticmethod
    def _overlaps(left: tuple[int, int], right: tuple[int, int]) -> bool:
        return left[0] < right[1] and right[0] < left[1]

    def _collect_parse_diagnostics(self, root: Node) -> None:
        stack = [root]
        while stack:
            node = stack.pop()
            if node.type == "ERROR" or node.is_missing:
                self._diagnostics.append(
                    {
                        "type": "parse_error" if node.type == "ERROR" else "missing_node",
                        "node_type": node.type,
                        "start_line": node.start_point[0] + 1,
                        "end_line": node.end_point[0] + 1,
                        "source": self._text(node)[:500],
                    }
                )
            stack.extend(reversed(node.children))


def discover_source_files(root: Path) -> Iterator[Path]:
    if root.is_file():
        if root.suffix.lower() in SUPPORTED_EXTENSIONS:
            yield root
        return

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRECTORIES for part in path.parts):
            continue
        if path.suffix.lower() in SUPPORTED_EXTENSIONS:
            yield path


def build_llm_batches(
    file_results: Sequence[FileResult], *, max_items: int
) -> list[dict[str, object]]:
    """Create same-file batches without changing the one-unit/one-output rule."""
    batches: list[dict[str, object]] = []
    batch_number = 0
    for file_result in file_results:
        units = file_result.units
        for start in range(0, len(units), max_items):
            batch_number += 1
            chunk = units[start : start + max_items]
            batches.append(
                {
                    "batch_id": f"batch_{batch_number:06d}",
                    "file": file_result.file,
                    "language": file_result.language,
                    "items": [
                        {
                            "id": unit.id,
                            "parent_id": unit.parent_id,
                            "scope_role": unit.scope_role,
                            "code": unit.code,
                        }
                        for unit in chunk
                    ],
                }
            )
    return batches


def serialize_result(
    root: Path,
    file_results: Sequence[FileResult],
    *,
    batch_size: int,
) -> dict[str, object]:
    unit_count = sum(len(result.units) for result in file_results)
    parse_error_files = sum(result.parse_has_error for result in file_results)
    warning_count = sum(
        len(unit.warnings) for result in file_results for unit in result.units
    )
    return {
        "schema_version": 2,
        "root": root.resolve().as_posix(),
        "summary": {
            "file_count": len(file_results),
            "unit_count": unit_count,
            "parse_error_file_count": parse_error_files,
            "warning_count": warning_count,
            "llm_batch_size": batch_size,
        },
        "files": [
            {
                **asdict(result),
                "units": [asdict(unit) for unit in result.units],
            }
            for result in file_results
        ],
        "llm_batches": build_llm_batches(file_results, max_items=batch_size),
    }


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Split a repository into hierarchical one-to-one LLM translation units."
    )
    parser.add_argument("input", type=Path, help="Repository directory or source file")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("code_units.json"),
        help="Output JSON path (default: code_units.json)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Maximum units per later LLM call (default: 10)",
    )
    parser.add_argument(
        "--max-unit-chars",
        type=int,
        default=4000,
        help="Warn when one normalized unit exceeds this size",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    root = args.input.resolve()
    if not root.exists():
        raise SystemExit(f"Input does not exist: {root}")
    if args.batch_size < 1 or args.batch_size > 10:
        raise SystemExit("--batch-size must be between 1 and 10")

    relative_to = root if root.is_dir() else root.parent
    segmenter = CodeSegmenter(max_unit_chars=args.max_unit_chars)
    files = sorted(discover_source_files(root))
    file_results = [
        segmenter.segment_file(path, relative_to=relative_to) for path in files
    ]

    payload = serialize_result(root, file_results, batch_size=args.batch_size)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    summary = payload["summary"]
    print(
        f"files={summary['file_count']} units={summary['unit_count']} "
        f"parse_error_files={summary['parse_error_file_count']} "
        f"warnings={summary['warning_count']} output={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
