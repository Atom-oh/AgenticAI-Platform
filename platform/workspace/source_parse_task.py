"""Resource-bounded PDF extraction with no inherited credential environment."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def isolated_pdf_pages(data, timeout=50):
    if timeout <= 0:
        raise ValueError("PDF 해석 시간 한도를 초과했습니다.")
    with tempfile.TemporaryDirectory(prefix="source-parse-") as directory:
        root = Path(directory)
        source, output = root / "input.pdf", root / "pages.json"
        source.write_bytes(data)
        env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "LD_LIBRARY_PATH") if key in os.environ}
        env.update(PYTHONPATH=str(Path(__file__).resolve().parents[1]), PYTHONDONTWRITEBYTECODE="1", TMPDIR=directory)
        try:
            result = subprocess.run([sys.executable, "-m", "workspace.source_parse_task", str(source), str(output)],
                                    env=env, cwd=directory, timeout=min(50, timeout), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise ValueError("PDF 해석 시간 한도를 초과했습니다.") from None
        if result.returncode or not output.is_file() or output.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("PDF를 해석하지 못했거나 처리 한도를 초과했습니다.")
        return json.loads(output.read_text())


def main():
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (1536 * 1024 * 1024, 1536 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (45, 45))
    resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024, 16 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    from workspace.prepare_guidelines import pdf_pages
    pages = pdf_pages(Path(sys.argv[1]).read_bytes())
    Path(sys.argv[2]).write_text(json.dumps(pages, ensure_ascii=False))


if __name__ == "__main__":
    main()
