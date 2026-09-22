from pathlib import Path

import pytest

from portfolio_migration.generate.core import simulate
from portfolio_migration.generate.landing import write_landing
from portfolio_migration.legacy.evaluate import published_kpis
from portfolio_migration.legacy.workbook import build_workbook


@pytest.fixture(scope="session")
def core():
    return simulate()


@pytest.fixture(scope="session")
def landing(core, tmp_path_factory):
    root = tmp_path_factory.mktemp("landing")
    manifest = write_landing(core, root)
    return root, manifest


@pytest.fixture(scope="session")
def workbook_path(core, tmp_path_factory) -> Path:
    return build_workbook(core, tmp_path_factory.mktemp("legacy") / "Monthly_Portfolio_Pack.xlsx")


@pytest.fixture(scope="session")
def legacy_kpis(workbook_path):
    return published_kpis(workbook_path)
