from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from healdragg.db import Store  # noqa: E402


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    db = Store(tmp_path / "test.db")
    yield db
    db.close()
