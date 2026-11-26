"""The rules package stays pure: stdlib only, no I/O, no system clock, no float math or round()."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

import app.rules

RULES_DIR = Path(app.rules.__file__).resolve().parent
MODULES = sorted(p for p in RULES_DIR.glob("*.py"))
ALLOWED_STDLIB = {
    "__future__",
    "collections",
    "dataclasses",
    "datetime",
    "decimal",
    "typing",
    "zoneinfo",
}
FORBIDDEN_CALL_NAMES = {"round", "float", "open", "print", "input", "eval", "exec"}
FORBIDDEN_CALL_ATTRS = {"now", "utcnow", "today", "time", "monotonic", "perf_counter"}


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_the_rules_package_has_the_expected_modules() -> None:
    names = {p.stem for p in MODULES}
    assert {"__init__", "timeutil", "measurables", "todos", "rocks", "issues", "meetings"} <= names


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_modules_import_only_the_stdlib_subset_and_each_other(path: Path) -> None:
    for node in ast.walk(parse(path)):
        if isinstance(node, ast.Import):
            roots = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            roots = [(node.module or "").split(".")[0]]
            if node.module and node.module.startswith("app.rules"):
                roots = []
        else:
            continue
        assert set(roots) <= ALLOWED_STDLIB, f"{path.name} imports {roots}"


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_modules_never_read_the_system_clock_or_use_float_math_or_round(path: Path) -> None:
    for node in ast.walk(parse(path)):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            assert node.func.id not in FORBIDDEN_CALL_NAMES, (
                f"{path.name}:{node.lineno} calls {node.func.id}()"
            )
        if isinstance(node.func, ast.Attribute):
            assert node.func.attr not in FORBIDDEN_CALL_ATTRS, (
                f"{path.name}:{node.lineno} calls .{node.func.attr}()"
            )


def test_every_name_in_all_is_importable() -> None:
    package = importlib.import_module("app.rules")
    for name in package.__all__:
        assert hasattr(package, name), name
