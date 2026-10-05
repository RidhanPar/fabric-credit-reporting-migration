"""Spark fixtures. Spark needs Hadoop winutils on Windows, so these tests run in
Docker locally (see Dockerfile.spark) and natively on Linux in CI."""
import os

import pytest

pytest.importorskip("pyspark")
pytest.importorskip("delta")


@pytest.fixture(scope="session")
def spark():
    if os.name == "nt" and not os.environ.get("HADOOP_HOME"):
        pytest.skip("Spark on Windows needs HADOOP_HOME/winutils; run the Spark tests in Docker")
    from portfolio_migration.lakehouse.io import local_spark

    s = local_spark("tests")
    s.sparkContext.setLogLevel("ERROR")
    yield s
    s.stop()


@pytest.fixture
def mutable_lake(lake_run, spark, tmp_path):
    """A copy of the good lake, so a test can break it without affecting other tests."""
    import shutil

    from portfolio_migration.lakehouse.io import LocalLake

    lake, _, _ = lake_run
    root = tmp_path / "lake"
    shutil.copytree(lake.root, root)
    return LocalLake(spark, root, lake.landing_root)


@pytest.fixture(scope="session")
def lake_run(spark, landing, tmp_path_factory):
    """Run the full medallion flow once over the generated landing files."""
    from portfolio_migration.lakehouse.io import LocalLake
    from portfolio_migration.lakehouse.runner import run_all

    root, manifest = landing
    lake = LocalLake(spark, tmp_path_factory.mktemp("lake"), root)
    summary = run_all(lake, batch_id="test-batch-1")
    return lake, summary, manifest
