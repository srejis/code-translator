from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).with_name("segment_code.py")

TSX = '''
import ReactFlow, {
  Background,
  Controls,
} from "reactflow";

type Result = {
  status: "ok" | "fail";
  detail?: string;
};

const TEXT = {
  ko: {
    title: "제목",
    body: "본문",
  },
};

function run(
  id: string,
  options = { active: true, retry: count + 1 },
) {
  const rows = items.map((item) => {
    const transformed = transform(item);
    return transformed;
  });
  return rows.map((row) => <span key={row.id}>{row.label}</span>);
}
'''

PYTHON = '''
from pathlib import Path

CONFIG = {
    "ko": {
        "title": "제목",
        "body": "본문",
    },
}

@router.get(
    "/items/{item_id}",
    response_model=Item,
)
async def read_item(
    item_id: str,
    options=make_options({"mode": primary if active else fallback}),
):
    try:
        return await send(
            item_id,
            {"mode": options.mode},
        )
    except ValueError as error:
        raise error
'''


def run_case(root: Path) -> dict:
    output = root / "units.json"
    subprocess.run(
        [sys.executable, str(SCRIPT), str(root), "--output", str(output)],
        check=True,
    )
    return json.loads(output.read_text(encoding="utf-8"))


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "sample.tsx").write_text(TSX, encoding="utf-8")
        (root / "sample.py").write_text(PYTHON, encoding="utf-8")
        payload = run_case(root)

        assert payload["summary"]["parse_error_file_count"] == 0
        units = [unit for file in payload["files"] for unit in file["units"]]
        codes = [unit["code"] for unit in units]

        # Multiline imports and function parameters stay one unit.
        assert any("import ReactFlow" in code and "Controls" in code for code in codes)
        assert any("options = { active: true" in code and "/* BODY */" in code for code in codes)

        # Assigned multiline data stays one unit instead of becoming entries.
        assert any("const TEXT = {" in code and 'title: "제목"' in code for code in codes)
        assert any("CONFIG = {" in code and '"body": "본문"' in code for code in codes)
        assert not any(code.strip().rstrip(",") == 'title: "제목"' for code in codes)

        # Block callback is split; expression callback is not split.
        assert any("items.map((item) => { /* BODY */ })" in code for code in codes)
        assert any("<span key={row.id}>" in code for code in codes)
        assert not any(code.strip() == "<span key={row.id}>{row.label}</span>" for code in codes)

        # Decorator + definition header stay together, body becomes children.
        assert any("@router.get" in code and "async def read_item" in code and "..." in code for code in codes)
        assert any(unit["scope_role"] == "catch" for unit in units)

        print(f"smoke test passed: {len(units)} units")


if __name__ == "__main__":
    main()
