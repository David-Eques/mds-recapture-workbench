"""Fail CI when private/demo-era material leaks into the public workbench tree."""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_TOP_LEVEL = {"infra", "artifacts", "var"}
FORBIDDEN_APP_PACKAGES = {"db", "eval", "extraction", "ingest", "linkage", "synthetic"}
FORBIDDEN_SUFFIXES = {".jar", ".class", ".webm", ".mp4", ".mov"}
FORBIDDEN_IMPORT_PARTS = {
    "app.fixtures",
    "app.db",
    "app.eval",
    "app.extraction",
    "app.ingest",
    "app.linkage",
    "app.synthetic",
}


def _tracked_files() -> list[Path]:
    try:
        output = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT, stderr=subprocess.DEVNULL)
    except (FileNotFoundError, subprocess.CalledProcessError):
        ignored_roots = {".venv", ".cache", "node_modules", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
        return [
            path.relative_to(ROOT)
            for path in ROOT.rglob("*")
            if path.is_file() and not (set(path.relative_to(ROOT).parts) & ignored_roots)
        ]
    return [Path(raw.decode()) for raw in output.split(b"\0") if raw]


def _absolute_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imports.add(node.module)
    return imports


def main() -> None:
    files = _tracked_files()
    failures: list[str] = []
    for relative in files:
        if relative.parts and relative.parts[0] in FORBIDDEN_TOP_LEVEL:
            failures.append(f"forbidden top-level path: {relative}")
        if (
            len(relative.parts) > 1
            and relative.parts[0] == "app"
            and relative.parts[1] in FORBIDDEN_APP_PACKAGES
        ):
            failures.append(f"forbidden runtime package: {relative}")
        if relative.suffix.lower() in FORBIDDEN_SUFFIXES:
            failures.append(f"generated or redistributed binary: {relative}")
        path = ROOT / relative
        if path.suffix == ".py" and relative.parts and relative.parts[0] == "app":
            for imported in _absolute_imports(path):
                if any(
                    imported == root or imported.startswith(f"{root}.") for root in FORBIDDEN_IMPORT_PARTS
                ):
                    failures.append(f"private subsystem import in {relative}: {imported}")
        if path.suffix.lower() in {".py", ".md", ".json", ".yaml", ".yml", ".toml", ".js", ".css", ".html"}:
            text = path.read_text(encoding="utf-8")
            if ("/" + "Users/") in text or ("C:\\" + "Users\\") in text:
                failures.append(f"absolute developer path in {relative}")

    from app.api.main import app

    expected_routes = {"/", "/healthz", "/readyz", "/api/capabilities", "/api/analyze"}
    actual_routes = set(app.openapi()["paths"])
    if actual_routes != expected_routes:
        failures.append(f"route inventory differs: {sorted(actual_routes)}")

    if (ROOT / ".git").exists():
        status = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()
        if status:
            failures.append("verification left the git worktree dirty")

    if failures:
        raise SystemExit("\n".join(failures))


if __name__ == "__main__":
    main()
