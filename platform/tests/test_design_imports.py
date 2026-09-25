# platform/tests/test_design_imports.py
"""The design_loop import allowlist (engine plan, Global Constraints; PR #30 review 1, #9).

`design_loop` imports only the standard library and `workspace.ontology_schema` / `workspace.ontology_ux`, both pure;
B2 packages exactly those into the Runtime image. Every other verifier (contract hashing, the React report predicate)
is injected through `deps`. Lazy imports inside functions count too: they still need the excluded dependency closure
the moment the code runs.
"""
import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "design_loop"
ALLOWED_WORKSPACE = {"ontology_schema", "ontology_ux"}


def _violations(path):
    out = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top not in sys.stdlib_module_names and alias.name not in {f"workspace.{m}" for m in ALLOWED_WORKSPACE}:
                    out.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            module = node.module or ""
            if module == "workspace":
                out += [f"workspace.{a.name}" for a in node.names if a.name not in ALLOWED_WORKSPACE]
            elif module.startswith("workspace."):
                if module.split(".", 1)[1] not in ALLOWED_WORKSPACE:
                    out.append(module)
            elif module.split(".")[0] not in sys.stdlib_module_names and module != "__future__":
                out.append(module)
    return out


def test_every_engine_module_imports_only_the_allowlist():
    found = {p.name: v for p in sorted(ENGINE.glob("*.py")) if (v := _violations(p))}
    assert found == {}, found


def test_the_allowed_workspace_modules_are_pure():
    for name in sorted(ALLOWED_WORKSPACE):
        assert _violations(ROOT / "workspace" / f"{name}.py") == [], name
        tree = ast.parse((ROOT / "workspace" / f"{name}.py").read_text(encoding="utf-8"))
        relative = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.level]
        assert relative == [], name


def test_engine_imports_in_a_schema_only_package(tmp_path):
    """The Runtime layout: design_loop plus only the two pure workspace modules."""
    shutil.copytree(ENGINE, tmp_path / "design_loop", ignore=shutil.ignore_patterns("__pycache__"))
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "__init__.py").write_text("", encoding="utf-8")
    for name in ALLOWED_WORKSPACE:
        shutil.copy(ROOT / "workspace" / f"{name}.py", tmp_path / "workspace" / f"{name}.py")
    modules = sorted(p.stem for p in ENGINE.glob("*.py") if p.stem != "__init__")
    code = "import importlib\n" + "".join(f"importlib.import_module('design_loop.{m}')\n" for m in modules)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    r = subprocess.run([sys.executable, "-I", "-c", f"import sys; sys.path[:0] = [{str(tmp_path)!r}]\n" + code],
                       cwd=tmp_path, capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr[-2000:]
