#!/usr/bin/env python3
"""Integrated launcher for repository analysis and the code-unit viewer."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Sequence


APP_ROOT = Path(__file__).resolve().parent
PROJECTS_OUTPUT_ROOT = APP_ROOT / "outputs" / "projects"
SUPPORTED_SCHEMA_VERSIONS = {2}
INDEX_SUFFIXES = (
    ".viewer.sqlite3",
    ".viewer.sqlite3-shm",
    ".viewer.sqlite3-wal",
    ".viewer.sqlite3.tmp",
)


def analyze_repository(*args: Any, **kwargs: Any) -> dict[str, object]:
    try:
        from segment_code import analyze_repository as analyze
    except SystemExit as exc:
        raise RuntimeError(str(exc)) from exc
    return analyze(*args, **kwargs)


def serve_viewer(*args: Any, **kwargs: Any) -> int:
    try:
        from code_unit_viewer import serve_viewer as serve
    except SystemExit as exc:
        raise RuntimeError(str(exc)) from exc
    return serve(*args, **kwargs)


@dataclass(frozen=True)
class AnalysisRecord:
    path: Path
    root: Path
    schema_version: int
    summary: dict[str, Any]


def normalized_project_path(project_root: Path) -> str:
    return os.path.normcase(str(project_root.resolve()))


def project_cache_dir(
    project_root: Path, *, output_root: Path = PROJECTS_OUTPUT_ROOT
) -> Path:
    resolved = project_root.resolve()
    normalized = normalized_project_path(resolved)
    path_hash = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]
    folder_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", resolved.name).strip(" .")
    folder_name = folder_name or "project"
    return output_root / f"{folder_name}-{path_hash}"


def project_json_path(
    project_root: Path, *, output_root: Path = PROJECTS_OUTPUT_ROOT
) -> Path:
    return project_cache_dir(project_root, output_root=output_root) / "code_units.json"


def read_json_metadata(path: Path) -> tuple[Any, Any, Any, bool]:
    try:
        import ijson

        with path.open("rb") as stream:
            schema_version = next(ijson.items(stream, "schema_version"), None)
        with path.open("rb") as stream:
            root = next(ijson.items(stream, "root"), None)
        with path.open("rb") as stream:
            summary = next(ijson.items(stream, "summary"), None)
        with path.open("rb") as stream:
            files_present = any(
                prefix == "files" and event == "start_array"
                for prefix, event, _value in ijson.parse(stream)
            )
    except Exception:
        return None, None, None, False
    return schema_version, root, summary, files_present


def validate_record(path: Path, project_root: Path) -> AnalysisRecord | None:
    if not path.is_file():
        return None
    schema_version, root_value, summary, files_present = read_json_metadata(path)
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        return None
    if not isinstance(root_value, str) or not isinstance(summary, dict) or not files_present:
        return None
    try:
        recorded_root = Path(root_value).resolve()
    except (OSError, ValueError):
        return None
    if normalized_project_path(recorded_root) != normalized_project_path(project_root):
        return None
    return AnalysisRecord(path.resolve(), recorded_root, int(schema_version), summary)


def _copy_record_to_cache(source: Path, destination: Path, project_root: Path) -> AnalysisRecord:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".importing")
    with contextlib.suppress(FileNotFoundError):
        temporary.unlink()
    try:
        shutil.copy2(source, temporary)
        record = validate_record(temporary, project_root)
        if record is None:
            raise ValueError(f"Imported analysis record is invalid: {source}")
        os.replace(temporary, destination)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()
    installed = validate_record(destination, project_root)
    if installed is None:
        raise ValueError(f"Imported analysis record could not be reopened: {source}")
    return installed


def find_analysis_record(
    project_root: Path,
    *,
    output_root: Path = PROJECTS_OUTPUT_ROOT,
    legacy_root: Path | None = None,
) -> AnalysisRecord | None:
    cache_path = project_json_path(project_root, output_root=output_root)
    cached = validate_record(cache_path, project_root)
    if cached is not None:
        return cached

    search_root = legacy_root or (APP_ROOT / "outputs")
    if not search_root.exists():
        return None
    candidates: list[AnalysisRecord] = []
    for candidate in search_root.rglob("*.json"):
        if candidate.resolve() == cache_path.resolve():
            continue
        record = validate_record(candidate, project_root)
        if record is not None:
            candidates.append(record)
    if not candidates:
        return None
    newest = max(candidates, key=lambda item: item.path.stat().st_mtime_ns)
    return _copy_record_to_cache(newest.path, cache_path, project_root)


def remove_viewer_indexes(json_path: Path) -> None:
    for suffix in INDEX_SUFFIXES:
        candidate = Path(str(json_path) + suffix)
        with contextlib.suppress(FileNotFoundError):
            candidate.unlink()


def refresh_analysis_record(
    project_root: Path,
    *,
    output_root: Path = PROJECTS_OUTPUT_ROOT,
    progress_callback: Callable[[dict[str, object]], None] | None = None,
    analyzer: Callable[..., dict[str, object]] = analyze_repository,
    before_replace: Callable[[], None] | None = None,
) -> AnalysisRecord:
    project_root = project_root.resolve()
    destination = project_json_path(project_root, output_root=output_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".analyzing")
    with contextlib.suppress(FileNotFoundError):
        temporary.unlink()
    try:
        analyzer(
            project_root,
            temporary,
            batch_size=10,
            max_unit_chars=4000,
            progress_callback=progress_callback,
        )
        record = validate_record(temporary, project_root)
        if record is None:
            raise ValueError("The generated analysis JSON failed validation.")
        if before_replace is not None:
            before_replace()
        os.replace(temporary, destination)
        remove_viewer_indexes(destination)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()
    final_record = validate_record(destination, project_root)
    if final_record is None:
        raise ValueError("The installed analysis JSON failed validation.")
    return final_record


def validate_project(project_root: Path) -> Path:
    resolved = project_root.expanduser().resolve()
    if not resolved.exists() or not resolved.is_dir():
        raise ValueError(f"프로젝트 폴더가 존재하지 않습니다: {resolved}")
    try:
        next(resolved.iterdir(), None)
    except PermissionError as exc:
        raise PermissionError(f"프로젝트 폴더를 읽을 수 없습니다: {resolved}") from exc
    return resolved


def summary_text(record: AnalysisRecord | None, *, damaged: bool = False) -> str:
    if record is None:
        if damaged:
            return "기존 분석 기록이 손상되었습니다. 새 분석이 필요합니다."
        return "분석 기록이 없습니다. 새 분석이 필요합니다."
    summary = record.summary
    parts = [
        f"파일 {int(summary.get('file_count', 0)):,}개",
        f"코드 단위 {int(summary.get('unit_count', 0)):,}개",
    ]
    if "warning_count" in summary:
        parts.append(f"경고 {int(summary.get('warning_count', 0)):,}개")
    if "parse_error_file_count" in summary:
        parts.append(f"파싱 오류 파일 {int(summary.get('parse_error_file_count', 0)):,}개")
    return "기존 분석 기록이 있습니다.\n" + " · ".join(parts)


def native_directory_chooser() -> str | None:
    """Open only the operating system's folder picker, with no launcher window."""
    if os.name == "nt":
        script = (
            "$folder=(New-Object -ComObject Shell.Application).BrowseForFolder("
            "0,'분석할 프로젝트 폴더를 선택하세요.',0,0);"
            "if($folder){[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
            "$folder.Self.Path}"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", script],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        selected = result.stdout.strip()
        return selected or None

    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    try:
        selected = filedialog.askdirectory(mustexist=True)
        return selected or None
    finally:
        root.destroy()


def choose_project_directory(
    chooser: Callable[[], str | None] = native_directory_chooser,
) -> str | None:
    return chooser()


class ViewerApplicationState:
    def __init__(
        self,
        *,
        output_root: Path = PROJECTS_OUTPUT_ROOT,
        legacy_root: Path | None = None,
        chooser: Callable[[], str | None] = native_directory_chooser,
        analyzer: Callable[..., dict[str, object]] = analyze_repository,
    ) -> None:
        self.lock = threading.RLock()
        self.status = "empty"
        self.project_root: Path | None = None
        self.record: AnalysisRecord | None = None
        self.index: Any | None = None
        self.progress: dict[str, object] | None = None
        self.error: str | None = None
        self.analysis_thread: threading.Thread | None = None
        self.output_root = output_root
        self.legacy_root = legacy_root
        self.chooser = chooser
        self.analyzer = analyzer
        self._generation = 0

    def app_payload(self) -> dict[str, object]:
        with self.lock:
            project = self.project_root
            record = self.record
            return {
                "status": self.status,
                "project": (
                    {"name": project.name, "path": str(project)} if project else None
                ),
                "has_record": record is not None,
                "summary": dict(record.summary) if record else None,
                "record_version": (
                    f"{record.path}:{record.path.stat().st_mtime_ns}" if record else None
                ),
                "progress": dict(self.progress) if self.progress else None,
                "error": self.error,
                "dynamic": True,
            }

    def current_index(self) -> Any | None:
        with self.lock:
            return self.index if self.status == "ready" else None

    def pick_project(self) -> dict[str, object]:
        selected = choose_project_directory(self.chooser)
        return {"cancelled": selected is None, "path": selected}

    def select_project(
        self,
        path: str | Path,
        *,
        auto_analyze: bool = False,
        force: bool = False,
    ) -> dict[str, object]:
        project = validate_project(Path(path))
        with self.lock:
            if self.status == "analyzing":
                raise RuntimeError("프로젝트 분석이 진행 중입니다.")
            old_index = self.index
            self._generation += 1
            generation = self._generation
            self.status = "loading"
            self.project_root = project
            self.record = None
            self.index = None
            self.progress = None
            self.error = None
        if old_index is not None:
            old_index.close()

        thread = threading.Thread(
            target=self._load_project,
            args=(project, generation, auto_analyze, force),
            name="project-load",
            daemon=True,
        )
        with self.lock:
            self.analysis_thread = thread
        thread.start()
        return {"status": "loading", "path": str(project)}

    def _load_project(
        self, project: Path, generation: int, auto_analyze: bool, force: bool
    ) -> None:
        try:
            record = None if force else find_analysis_record(
                project, output_root=self.output_root, legacy_root=self.legacy_root
            )
            if record is not None:
                self._install_record(project, record, generation)
                return
            if auto_analyze:
                with self.lock:
                    if generation != self._generation:
                        return
                    self.status = "analyzing"
                self._analyze(project, generation)
                return
            with self.lock:
                if generation == self._generation:
                    self.status = "project_selected"
                    self.analysis_thread = None
        except Exception as exc:
            self._set_error(exc, generation)

    def start_analysis(self, *, force: bool = False) -> dict[str, object]:
        with self.lock:
            if self.status in {"loading", "analyzing"}:
                raise RuntimeError("프로젝트 작업이 이미 진행 중입니다.")
            if self.project_root is None:
                raise ValueError("분석할 프로젝트를 먼저 선택하세요.")
            if not force and self.record is not None and self.status == "ready":
                return {"status": "ready", "message": "기존 분석 기록을 사용 중입니다."}
            project = self.project_root
            generation = self._generation
            self.status = "analyzing"
            self.progress = {"phase": "starting", "current": 0, "total": 0, "file": ""}
            self.error = None
            thread = threading.Thread(
                target=self._analyze,
                args=(project, generation),
                name="code-analysis",
                daemon=True,
            )
            self.analysis_thread = thread
            thread.start()
        return {"status": "analyzing"}

    def _analyze(self, project: Path, generation: int) -> None:
        with self.lock:
            previous_record = self.record
            previous_index = self.index
        previous_index_closed = False

        def progress(event: dict[str, object]) -> None:
            with self.lock:
                if generation == self._generation:
                    self.progress = dict(event)

        def close_previous_index() -> None:
            nonlocal previous_index_closed
            if previous_index is not None:
                previous_index.close()
                previous_index_closed = True

        try:
            record = refresh_analysis_record(
                project,
                output_root=self.output_root,
                progress_callback=progress,
                analyzer=self.analyzer,
                before_replace=close_previous_index,
            )
            self._install_record(project, record, generation)
        except Exception as exc:
            print(f"[analysis] {type(exc).__name__}: {exc}", file=sys.stderr)
            if previous_record is not None:
                try:
                    index = (
                        self._build_index(project, previous_record)
                        if previous_index_closed
                        else previous_index
                    )
                except Exception:
                    self._set_error(exc, generation)
                    return
                with self.lock:
                    if generation == self._generation:
                        self.record = previous_record
                        self.index = index
                        self.status = "ready"
                        self.progress = None
                        self.error = f"재분석 실패: {exc} 기존 분석 기록을 유지합니다."
                        self.analysis_thread = None
                return
            self._set_error(exc, generation, keep_project=True)

    def _build_index(self, project: Path, record: AnalysisRecord) -> Any:
        from code_unit_viewer import UnitIndex, ViewerConfig

        db_path = Path(str(record.path) + ".viewer.sqlite3")
        config = ViewerConfig(record.path, db_path, project, "127.0.0.1", 0, False)
        index = UnitIndex(config)
        if not index.cache_is_current():
            print(f"[index] building SQLite index from {record.path}", flush=True)
            index.rebuild()
        return index

    def _install_record(
        self, project: Path, record: AnalysisRecord, generation: int
    ) -> None:
        index = self._build_index(project, record)
        with self.lock:
            if generation != self._generation:
                index.close()
                return
            old_index = self.index
            self.project_root = project
            self.record = record
            self.index = index
            self.status = "ready"
            self.progress = None
            self.error = None
            self.analysis_thread = None
        if old_index is not None and old_index is not index:
            old_index.close()

    def _set_error(
        self, exc: Exception, generation: int, *, keep_project: bool = True
    ) -> None:
        print(f"[app] {type(exc).__name__}: {exc}", file=sys.stderr)
        with self.lock:
            if generation != self._generation:
                return
            self.status = "error"
            self.error = str(exc)
            self.progress = None
            self.analysis_thread = None
            if not keep_project:
                self.project_root = None

    def close(self) -> None:
        with self.lock:
            index = self.index
            self.index = None
        if index is not None:
            index.close()


def serve_application(
    state: ViewerApplicationState,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
    project: Path | None = None,
    force: bool = False,
) -> int:
    from code_unit_viewer import choose_port, make_handler

    selected_port = choose_port(host, port)
    server = ThreadingHTTPServer((host, selected_port), make_handler(state))
    url = f"http://{host}:{selected_port}/"
    print(f"[viewer] open {url}", flush=True)
    if project is not None:
        state.select_project(project, auto_analyze=True, force=force)
    if open_browser:
        threading.Timer(0.35, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[viewer] stopped", flush=True)
    finally:
        state.close()
        server.server_close()
    return 0


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze a project and open Code Unit Viewer.")
    parser.add_argument("--project", type=Path, help="Project folder; skips the folder dialog")
    parser.add_argument("--force", action="store_true", help="Replace the existing analysis")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.force and args.project is None:
        raise SystemExit("--force requires --project")
    state = ViewerApplicationState()
    try:
        return serve_application(
            state,
            project=args.project,
            force=args.force,
            open_browser=not args.no_browser,
        )
    except Exception as exc:
        print(f"[app] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
