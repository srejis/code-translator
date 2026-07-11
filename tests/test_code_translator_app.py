from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from code_translator_app import (
    find_analysis_record,
    project_cache_dir,
    project_json_path,
    refresh_analysis_record,
    validate_record,
)
from code_unit_viewer import UnitIndex, ViewerConfig, make_handler
from segment_code import analyze_repository


REPO_ROOT = Path(__file__).resolve().parents[1]


def write_record(path: Path, root: Path, *, unit_count: int = 0) -> None:
    payload = {
        "schema_version": 2,
        "root": root.resolve().as_posix(),
        "summary": {
            "file_count": 0,
            "unit_count": unit_count,
            "parse_error_file_count": 0,
            "warning_count": 0,
            "llm_batch_size": 10,
        },
        "files": [],
        "llm_batches": [],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class CacheTests(unittest.TestCase):
    def test_same_project_always_uses_same_cache_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "project"
            root.mkdir()
            output = Path(temporary) / "cache"
            self.assertEqual(
                project_cache_dir(root, output_root=output),
                project_cache_dir(root / ".", output_root=output),
            )

    def test_same_folder_name_in_different_locations_does_not_collide(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            first = base / "one" / "project"
            second = base / "two" / "project"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            self.assertNotEqual(project_cache_dir(first), project_cache_dir(second))

    def test_korean_and_space_path_is_supported(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "한글 프로젝트"
            root.mkdir()
            record_path = Path(temporary) / "기록 공간" / "units.json"
            write_record(record_path, root)
            self.assertIsNotNone(validate_record(record_path, root))
            self.assertIn("한글 프로젝트", project_cache_dir(root).name)

    def test_existing_record_is_loaded_without_analysis(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = base / "project"
            project.mkdir()
            output = base / "cache"
            record_path = project_json_path(project, output_root=output)
            write_record(record_path, project, unit_count=7)
            record = find_analysis_record(project, output_root=output, legacy_root=base / "none")
            self.assertIsNotNone(record)
            self.assertEqual(record.summary["unit_count"], 7)

    def test_wrong_root_and_damaged_json_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = base / "project"
            other = base / "other"
            project.mkdir()
            other.mkdir()
            wrong = base / "wrong.json"
            broken = base / "broken.json"
            write_record(wrong, other)
            broken.write_text("{broken", encoding="utf-8")
            self.assertIsNone(validate_record(wrong, project))
            self.assertIsNone(validate_record(broken, project))

    def test_legacy_record_is_copied_to_project_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = base / "source"
            project.mkdir()
            output = base / "cache"
            legacy = base / "legacy"
            write_record(legacy / "backend_unit.json", project, unit_count=12)
            record = find_analysis_record(project, output_root=output, legacy_root=legacy)
            self.assertEqual(record.path, project_json_path(project, output_root=output).resolve())
            self.assertEqual(record.summary["unit_count"], 12)

    def test_force_refresh_replaces_json_and_removes_indexes(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = base / "project"
            project.mkdir()
            output = base / "cache"
            destination = project_json_path(project, output_root=output)
            write_record(destination, project, unit_count=1)
            index_paths = [
                Path(str(destination) + suffix)
                for suffix in (".viewer.sqlite3", ".viewer.sqlite3-shm", ".viewer.sqlite3-wal", ".viewer.sqlite3.tmp")
            ]
            for path in index_paths:
                path.write_text("old", encoding="utf-8")

            def analyzer(root: Path, output_path: Path, **_kwargs):
                write_record(output_path, root, unit_count=2)
                return {}

            record = refresh_analysis_record(project, output_root=output, analyzer=analyzer)
            self.assertEqual(record.summary["unit_count"], 2)
            self.assertTrue(all(not path.exists() for path in index_paths))
            self.assertFalse(destination.with_suffix(destination.suffix + ".analyzing").exists())

    def test_failed_refresh_preserves_existing_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = base / "project"
            project.mkdir()
            output = base / "cache"
            destination = project_json_path(project, output_root=output)
            write_record(destination, project, unit_count=3)
            original = destination.read_bytes()

            def analyzer(_root: Path, output_path: Path, **_kwargs):
                output_path.write_text("{broken", encoding="utf-8")
                raise RuntimeError("analysis failed")

            with self.assertRaises(RuntimeError):
                refresh_analysis_record(project, output_root=output, analyzer=analyzer)
            self.assertEqual(destination.read_bytes(), original)
            self.assertFalse(destination.with_suffix(destination.suffix + ".analyzing").exists())


class CompatibilityAndFlowTests(unittest.TestCase):
    def test_existing_cli_help_commands_still_work(self):
        for script in ("segment_code.py", "code_unit_viewer.py"):
            result = subprocess.run(
                [sys.executable, str(REPO_ROOT / script), "--help"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_existing_segment_cli_generates_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "sample.py").write_text("value = 1\n", encoding="utf-8")
            output = root / "result.json"
            result = subprocess.run(
                [sys.executable, str(REPO_ROOT / "segment_code.py"), str(source), "--output", str(output)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["summary"]["file_count"], 1)

    def test_analysis_index_and_metadata_api_flow(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "작은 프로젝트"
            source.mkdir()
            (source / "sample.py").write_text("def hello():\n    return '안녕'\n", encoding="utf-8")
            (source / "sample.ts").write_text("export const value = 1;\n", encoding="utf-8")
            json_path = root / "outputs" / "code_units.json"
            payload = analyze_repository(source, json_path)
            self.assertEqual(payload["summary"]["file_count"], 2)

            db_path = Path(str(json_path) + ".viewer.sqlite3")
            config = ViewerConfig(json_path, db_path, source, "127.0.0.1", 0, False)
            index = UnitIndex(config)
            index.rebuild()
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(index))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_address[1]}/api/meta"
                with urllib.request.urlopen(url, timeout=5) as response:
                    metadata = json.loads(response.read().decode("utf-8"))
                self.assertEqual(metadata["summary"]["file_count"], 2)
                self.assertGreater(metadata["indexed_unit_count"], 0)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
