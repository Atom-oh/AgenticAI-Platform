"""Lambda and Worker packaging include the intake modules (Task I8a; review rounds 3/4, F14)."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLATFORM = Path(__file__).resolve().parents[1]


def _isolated(directory, code):
    """Import from `directory` only (plus the interpreter's own site-packages)."""
    environment = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(directory), "HOME": str(directory),
                   "AWS_DEFAULT_REGION": "ap-northeast-2", "AWS_EC2_METADATA_DISABLED": "true"}
    return subprocess.run([sys.executable, "-c", code], cwd=directory, env=environment,
                          capture_output=True, text=True, timeout=120)


@pytest.fixture(scope="module")
def api_dist(tmp_path_factory):
    target = tmp_path_factory.mktemp("api-dist") / "dist"
    result = subprocess.run(["bash", str(PLATFORM / "deploy.sh"), "--assemble-only", str(target)],
                            cwd=PLATFORM, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]
    return target


def test_assemble_only_builds_the_api_artifact_with_intake(api_dist):
    for module in ("intake", "workspace", "documents", "workbench", "common"):
        assert (api_dist / module).is_dir(), module
    assert (api_dist / "intake" / "admin_handler.py").is_file()
    assert not (api_dist / "tests").exists()


def test_handlers_import_from_the_assembled_artifact_only(api_dist):
    code = (
        "import sys, json\n"
        "assert not any(p.endswith('platform') for p in sys.path if p), sys.path\n"
        "import workspace.http, intake.admin_handler, intake.admission, intake.review\n"
        "assert intake.admin_handler.__file__.startswith(%r)\n"
        "print(json.dumps(intake.admin_handler.handler({'op': 'put_policy'}, None)))\n"
        "response = workspace.http.handler({'rawPath': '/studio-api/intake/reviews', 'requestContext': {}}, None)\n"
        "print(response['statusCode'])\n" % str(api_dist))
    result = _isolated(api_dist, code)
    assert result.returncode == 0, result.stderr[-2000:]
    lines = result.stdout.strip().splitlines()
    assert lines == ['{"error": "forbidden-transport"}', "401"]


def _dockerfile_copies():
    copies = []
    for line in (PLATFORM / "workspace" / "Dockerfile").read_text().splitlines():
        match = re.fullmatch(r"COPY (?!--from)(\S+(?: \S+)*) (\S+)", line.strip())
        if match:
            *sources, destination = line.strip().split()[1:]
            copies.append((sources, destination))
    return copies


def test_worker_image_copies_intake_and_imports_it(tmp_path):
    copies = _dockerfile_copies()
    assert (["intake"], "./intake") in copies
    # Reproduce the image's /var/task layout from the Dockerfile COPY lines.
    task = tmp_path / "var-task"
    task.mkdir()
    for sources, destination in copies:
        if destination.startswith("/"):
            continue
        for source in sources:
            origin = PLATFORM / source
            target = task / destination
            if origin.is_dir():
                shutil.copytree(origin, target, dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns("node_modules", "__pycache__"))
            elif origin.is_file():
                (target if destination.endswith("/") else target.parent).mkdir(parents=True, exist_ok=True)
                shutil.copy2(origin, target / origin.name if destination.endswith("/") else target)
    result = _isolated(task, "import intake.images, intake.admission, intake.worker, workspace.worker\n"
                             "assert intake.images.__file__.startswith(%r)\nprint('ok')" % str(task))
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip() == "ok"


@pytest.mark.docker
@pytest.mark.skipif(os.environ.get("RUN_DOCKER_TESTS") != "1" or not shutil.which("docker"),
                    reason="Docker image build is opt-in (RUN_DOCKER_TESTS=1)")
def test_worker_docker_image_imports_intake():
    tag = "bank-workspace-worker:packaging-test"
    build = subprocess.run(["docker", "build", "-f", "workspace/Dockerfile", "-t", tag, "."], cwd=PLATFORM,
                           capture_output=True, text=True, timeout=3600)
    assert build.returncode == 0, build.stderr[-2000:]
    result = subprocess.run(["docker", "run", "--rm", "--entrypoint", "python", tag, "-c",
                             "import intake.images, intake.admission"], capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stderr[-2000:]
