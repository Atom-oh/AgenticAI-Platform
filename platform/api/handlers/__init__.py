"""액션 → 핸들러 라우팅 테이블. 각 모듈은 ROUTES: dict[str, Callable[[Ctx, dict], None]] 를 노출한다."""
from __future__ import annotations

import importlib

ROUTES: dict = {}
MODULES = ["handlers.core", "handlers.s1", "handlers.s2",
           # 아래는 각 기능 모듈이 추가된다 (없으면 건너뜀)
           "handlers.registry", "handlers.screengen", "handlers.report"]

for _m in MODULES:
    try:
        ROUTES.update(importlib.import_module(_m).ROUTES)
    except ModuleNotFoundError as e:
        if e.name and not e.name.startswith("handlers."):
            raise
