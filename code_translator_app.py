#!/usr/bin/env python3
"""Integrated launcher for repository analysis and the code-unit viewer."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import os
import re
import shutil
import sys
import threading
import traceback
from dataclasses import dataclass
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


def run_project(
    project_root: Path,
    *,
    force: bool = False,
    open_browser: bool = True,
) -> int:
    project_root = validate_project(project_root)
    record = None if force else find_analysis_record(project_root)
    if record is None:
        record = refresh_analysis_record(project_root)
    return serve_viewer(
        record.path,
        source_root=project_root,
        open_browser=open_browser,
    )


class LauncherWindow:
    def __init__(self, tk: Any, filedialog: Any, messagebox: Any) -> None:
        self.tk = tk
        self.filedialog = filedialog
        self.messagebox = messagebox
        self.root = tk.Tk()
        self.root.title("Code Translator")
        self.root.geometry("720x300")
        self.root.minsize(620, 280)
        self.project: Path | None = None
        self.record: AnalysisRecord | None = None
        self.viewer_request: tuple[Path, Path] | None = None

        frame = tk.Frame(self.root, padx=18, pady=18)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text="분석할 프로젝트", anchor="w").pack(fill="x")
        path_row = tk.Frame(frame)
        path_row.pack(fill="x", pady=(6, 12))
        self.path_var = tk.StringVar()
        tk.Entry(path_row, textvariable=self.path_var, state="readonly").pack(
            side="left", fill="x", expand=True
        )
        tk.Button(path_row, text="폴더 선택", command=self.choose_folder).pack(
            side="left", padx=(8, 0)
        )
        self.status_var = tk.StringVar(value="프로젝트 폴더를 선택하세요.")
        tk.Label(frame, textvariable=self.status_var, justify="left", anchor="w").pack(
            fill="x", pady=(0, 12)
        )
        button_row = tk.Frame(frame)
        button_row.pack(fill="x")
        self.open_button = tk.Button(
            button_row, text="분석 후 열기", state="disabled", command=self.open_project
        )
        self.open_button.pack(side="left")
        self.force_button = tk.Button(
            button_row,
            text="기록 삭제 후 새로 분석",
            state="disabled",
            command=self.confirm_force,
        )
        self.force_button.pack(side="left", padx=(8, 0))
        self.progress_var = tk.StringVar()
        tk.Label(
            frame,
            textvariable=self.progress_var,
            justify="left",
            anchor="w",
            fg="#555555",
            wraplength=660,
        ).pack(fill="x", pady=(18, 0))
        self.root.after(100, self._initial_folder_prompt)

    def _initial_folder_prompt(self) -> None:
        if not self.choose_folder():
            self.root.destroy()

    def choose_folder(self) -> bool:
        selected = self.filedialog.askdirectory(mustexist=True)
        if not selected:
            return False
        try:
            self.project = validate_project(Path(selected))
            cache_path = project_json_path(self.project)
            damaged = cache_path.exists() and validate_record(cache_path, self.project) is None
            self.record = find_analysis_record(self.project)
        except Exception as exc:
            self._show_error(exc)
            return False
        self.path_var.set(str(self.project))
        self.status_var.set(summary_text(self.record, damaged=damaged))
        self.open_button.configure(
            text="기존 기록 열기" if self.record else "분석 후 열기", state="normal"
        )
        self.force_button.configure(state="normal")
        self.progress_var.set("")
        return True

    def open_project(self) -> None:
        if self.project is None:
            return
        if self.record is not None:
            self._launch_viewer(self.record)
        else:
            self._start_analysis()

    def confirm_force(self) -> None:
        if self.project is None:
            return
        confirmed = self.messagebox.askyesno(
            "새로 분석",
            "기존 분석 기록을 무시하고 프로젝트를 처음부터 다시 분석합니다.\n계속하시겠습니까?",
        )
        if confirmed:
            self._start_analysis()

    def _start_analysis(self) -> None:
        assert self.project is not None
        self.open_button.configure(state="disabled")
        self.force_button.configure(state="disabled")
        self.progress_var.set("분석을 준비하고 있습니다...")
        project = self.project

        def progress(event: dict[str, object]) -> None:
            self.root.after(0, self._update_progress, event)

        def worker() -> None:
            try:
                record = refresh_analysis_record(project, progress_callback=progress)
            except Exception as exc:
                traceback.print_exc()
                self.root.after(0, self._analysis_failed, exc)
            else:
                self.root.after(0, self._launch_viewer, record)

        threading.Thread(target=worker, name="code-analysis", daemon=True).start()

    def _update_progress(self, event: dict[str, object]) -> None:
        current = int(event.get("current", 0))
        total = int(event.get("total", 0))
        file_name = str(event.get("file", ""))
        self.progress_var.set(f"분석 중 {current:,}/{total:,}\n{file_name}")

    def _analysis_failed(self, exc: Exception) -> None:
        self.open_button.configure(state="normal")
        self.force_button.configure(state="normal")
        self.progress_var.set("분석에 실패했습니다. 기존 정상 기록은 유지됩니다.")
        self._show_error(exc)

    def _launch_viewer(self, record: AnalysisRecord) -> None:
        assert self.project is not None
        self.viewer_request = (record.path, self.project)
        self.root.destroy()

    def _show_error(self, exc: Exception) -> None:
        print(f"[launcher] {type(exc).__name__}: {exc}", file=sys.stderr)
        self.messagebox.showerror(
            "Code Translator 오류",
            f"{exc}\n\n의존 패키지와 폴더 권한을 확인한 뒤 다시 시도하세요.",
        )

    def run(self) -> int:
        self.root.mainloop()
        if self.viewer_request is None:
            return 0
        json_path, project_root = self.viewer_request
        try:
            return serve_viewer(json_path, source_root=project_root, open_browser=True)
        except Exception as exc:
            traceback.print_exc()
            self.messagebox.showerror(
                "Code Translator 오류",
                f"뷰어를 시작하지 못했습니다.\n{exc}\n\nSQLite 파일과 로컬 포트를 확인하세요.",
            )
            return 1


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
    if args.project is not None:
        try:
            return run_project(args.project, force=args.force, open_browser=not args.no_browser)
        except Exception as exc:
            traceback.print_exc()
            return 1

    import tkinter as tk
    from tkinter import filedialog, messagebox

    return LauncherWindow(tk, filedialog, messagebox).run()


if __name__ == "__main__":
    raise SystemExit(main())
