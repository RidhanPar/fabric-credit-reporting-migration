"""The Fabric items must stay in step with the package, and in the format Fabric reads.

Everything under fabric/workspace/ is what Git integration syncs into a workspace:
one folder per item, each with a .platform file. The .ipynb copies under
fabric/notebooks/ are for importing a notebook by hand.
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1] / "fabric"
WORKSPACE = ROOT / "workspace"
NOTEBOOKS = ["nb_01_bronze", "nb_02_silver", "nb_03_gold", "nb_04_reconciliation", "nb_05_monitoring"]
PIPELINE = "pl_portfolio_medallion"


def _code_cells_from_content_py(text: str) -> list[str]:
    """Split Fabric's notebook-content.py back into its code cells."""
    cells: list[str] = []
    current: list[str] = []
    in_code = False
    for line in text.split("\n"):
        if line.startswith("# CELL **") or line.startswith("# PARAMETERS CELL **"):
            if current:
                cells.append("\n".join(current))
            current, in_code = [], True
            continue
        if line.startswith("# METADATA **") or line.startswith("# MARKDOWN **"):
            if current:
                cells.append("\n".join(current))
            current, in_code = [], False
            continue
        if in_code and not line.startswith("# META "):
            current.append(line)
    if current:
        cells.append("\n".join(current))
    return [c for c in cells if c.strip()]


def test_every_notebook_exists_in_both_formats():
    assert sorted(p.stem for p in (ROOT / "notebooks").glob("*.ipynb")) == NOTEBOOKS
    assert sorted(p.name.removesuffix(".Notebook") for p in WORKSPACE.glob("*.Notebook")) == NOTEBOOKS


def test_workspace_items_carry_a_platform_file_fabric_can_read():
    expected = {f"{name}.Notebook": "Notebook" for name in NOTEBOOKS}
    expected[f"{PIPELINE}.DataPipeline"] = "DataPipeline"
    expected["LendCoPortfolio.SemanticModel"] = "SemanticModel"
    found = {p.name: p for p in WORKSPACE.iterdir() if p.is_dir()}
    assert set(found) == set(expected)
    for folder, item_type in expected.items():
        platform = json.loads((found[folder] / ".platform").read_text(encoding="utf-8"))
        assert platform["metadata"]["type"] == item_type, folder
        assert platform["metadata"]["displayName"] == folder.rsplit(".", 1)[0], folder
        assert platform["config"]["logicalId"], folder


def test_notebooks_parse_and_have_a_parameter_cell():
    for name in NOTEBOOKS:
        notebook = json.loads((ROOT / "notebooks" / f"{name}.ipynb").read_text(encoding="utf-8"))
        code = [c for c in notebook["cells"] if c["cell_type"] == "code"]
        assert code[0]["metadata"].get("tags") == ["parameters"], name
        for cell in code:
            compile("".join(cell["source"]), name, "exec")

        content = (WORKSPACE / f"{name}.Notebook" / "notebook-content.py").read_text(encoding="utf-8")
        assert content.startswith("# Fabric notebook source"), name
        assert "# PARAMETERS CELL ********************" in content, name
        cells = _code_cells_from_content_py(content)
        assert len(cells) == len(code), name
        for cell in cells:
            compile(cell, name, "exec")


def test_the_two_notebook_formats_hold_the_same_code():
    for name in NOTEBOOKS:
        notebook = json.loads((ROOT / "notebooks" / f"{name}.ipynb").read_text(encoding="utf-8"))
        ipynb_code = ["".join(c["source"]).strip() for c in notebook["cells"] if c["cell_type"] == "code"]
        content = (WORKSPACE / f"{name}.Notebook" / "notebook-content.py").read_text(encoding="utf-8")
        assert [c.strip() for c in _code_cells_from_content_py(content)] == ipynb_code, name


def test_every_notebook_logs_itself_to_the_run_log():
    for name in NOTEBOOKS:
        content = (WORKSPACE / f"{name}.Notebook" / "notebook-content.py").read_text(encoding="utf-8")
        assert "ops.logged_step" in content or "ops.monitor" in content, name


def test_the_pipeline_runs_the_layers_in_order():
    content = json.loads((WORKSPACE / f"{PIPELINE}.DataPipeline" / "pipeline-content.json")
                         .read_text(encoding="utf-8"))
    activities = content["properties"]["activities"]
    assert [a["name"] for a in activities] == ["Bronze", "Silver", "Gold", "Monitoring"]
    assert activities[0]["dependsOn"] == []
    assert activities[1]["dependsOn"] == [{"activity": "Bronze", "dependencyConditions": ["Succeeded"]}]
    assert activities[2]["dependsOn"] == [{"activity": "Silver", "dependencyConditions": ["Succeeded"]}]
    for activity in activities:
        assert activity["typeProperties"]["parameters"]["batch_id"]["value"]["value"] == "@pipeline().RunId"
        assert activity["policy"]["retry"] == 1


def test_monitoring_runs_even_when_an_earlier_step_fails():
    """A failed run still has to raise the alarm, so monitoring is not on the success path only."""
    content = json.loads((WORKSPACE / f"{PIPELINE}.DataPipeline" / "pipeline-content.json")
                         .read_text(encoding="utf-8"))
    monitoring = [a for a in content["properties"]["activities"] if a["name"] == "Monitoring"][0]
    assert set(monitoring["dependsOn"][0]["dependencyConditions"]) == {"Succeeded", "Failed", "Skipped"}


def test_gold_table_list_is_complete():
    pytest.importorskip("pyspark")
    from portfolio_migration.lakehouse import gold

    assert len(gold.GOLD_TABLES) == len(set(gold.GOLD_TABLES)) == 10


def test_every_promoted_item_has_a_deployment_rule():
    """A new notebook must not reach production still pointing at the dev lakehouse."""
    rules = json.loads((ROOT / "deployment" / "deployment-rules.json").read_text(encoding="utf-8"))
    covered = {rule["item"] for rule in rules["rules"]}
    for name in NOTEBOOKS:
        item = f"{name}.Notebook"
        assert item in covered, f"{item} has no deployment rule"
        rule = [r for r in rules["rules"] if r["item"] == item][0]
        assert rule["rule"] == "Default lakehouse"
        assert "portfolio-reporting-prod" in rule["production"]
    assert "LendCoPortfolio.SemanticModel" in covered
    assert f"{PIPELINE}.DataPipeline" in covered
    for rule in rules["rules"]:
        assert rule["why"], rule["item"]


def test_only_development_is_connected_to_git():
    """One path into production: promotion, not a second Git connection."""
    rules = json.loads((ROOT / "deployment" / "deployment-rules.json").read_text(encoding="utf-8"))
    connected = [s["name"] for s in rules["stages"] if s["git_connected"]]
    assert connected == ["Development"]
    development = rules["stages"][0]
    assert development["git_directory"] == "fabric/workspace"
