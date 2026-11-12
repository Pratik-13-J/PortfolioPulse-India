"""Shared pytest setup: tests write temporary databases under ``.tmp``."""

from __future__ import annotations

import pytest

from portfoliopulse.config import PROJECT_ROOT

TMP_ROOT = PROJECT_ROOT / ".tmp"
# Created at import as well, because unittest-style tests resolve
# ``TemporaryDirectory(dir=PROJECT_ROOT / ".tmp")`` during the test body.
TMP_ROOT.mkdir(exist_ok=True)


@pytest.fixture(scope="session", autouse=True)
def project_tmp_dir():
    TMP_ROOT.mkdir(exist_ok=True)
    return TMP_ROOT
