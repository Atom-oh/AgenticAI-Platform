import json
import subprocess
from pathlib import Path

from workspace.component_catalog import read_catalog


def test_python_and_actual_node_builder_lock_the_same_component_bytes():
    root = Path(__file__).resolve().parents[1] / "react-kit"
    node = json.loads(subprocess.check_output(
        ["node", "-e", "process.stdout.write(JSON.stringify(require('./manifest.cjs').catalog()))"], cwd=root))
    actual = read_catalog(root)
    assert actual["hash"] == node["hash"]
    assert actual["files"] == node["files"]
    assert actual["version"] == "1.0.0"
    assert "Button" in {item["name"] for item in actual["components"]}


def test_code_change_changes_lock_even_if_description_is_unchanged(tmp_path):
    (tmp_path / "ui").mkdir()
    (tmp_path / "catalog.json").write_text('{"id":"test-ui","version":"1.0.0","label":"Test","components":[]}')
    component = tmp_path / "ui" / "index.tsx"
    component.write_text('export const Button = () => <button>first</button>;')
    first = read_catalog(tmp_path)
    component.write_text('export const Button = () => <button>changed</button>;')
    assert read_catalog(tmp_path)["hash"] != first["hash"]
