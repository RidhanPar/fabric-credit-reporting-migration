"""The Fabric notebooks and pipeline definition must stay in step with the package."""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1] / "fabric"


def test_notebooks_are_generated_parse_and_have_a_parameter_cell():
    names = sorted(p.stem for p in (ROOT / "notebooks").glob("*.ipynb"))
    assert names == ["nb_01_bronze", "nb_02_silver", "nb_03_gold", "nb_04_reconciliation"]
    for path in (ROOT / "notebooks").glob("*.ipynb"):
        nb = json.loads(path.read_text(encoding="utf-8"))
        code = [c for c in nb["cells"] if c["cell_type"] == "code"]
        assert code[0]["metadata"].get("tags") == ["parameters"], path.name
        for cell in code:
            compile("".join(cell["source"]), path.name, "exec")


def test_pipeline_runs_layers_in_order_and_passes_the_run_id():
    p = json.loads((ROOT / "pipelines" / "pl_portfolio_medallion.json").read_text(encoding="utf-8"))
    acts = p["properties"]["activities"]
    assert [a["name"] for a in acts] == ["Bronze", "Silver", "Gold"]
    assert acts[1]["dependsOn"] == [{"activity": "Bronze", "dependencyConditions": ["Succeeded"]}]
    assert acts[2]["dependsOn"] == [{"activity": "Silver", "dependencyConditions": ["Succeeded"]}]
    for a in acts:
        assert a["typeProperties"]["parameters"]["batch_id"]["value"]["value"] == "@pipeline().RunId"


def test_gold_table_list_is_complete():
    pytest.importorskip("pyspark")
    from portfolio_migration.lakehouse import gold

    assert len(gold.GOLD_TABLES) == len(set(gold.GOLD_TABLES)) == 10
