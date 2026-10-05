"""Read the generated TMDL back into Python, so the model can be tested.

TMDL is indented text. This is a deliberately small reader: enough to list tables,
columns, measures, relationships and security roles with their properties, which is
what the tests check. It is not a TMDL parser for general use.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

MODEL_DIR = Path(__file__).parents[2] / "fabric" / "workspace" / "LendCoPortfolio.SemanticModel"
DEFINITION = MODEL_DIR / "definition"


@dataclass
class Column:
    name: str
    data_type: str = ""
    description: str = ""
    hidden: bool = False
    format_string: str = ""
    sort_by: str = ""


@dataclass
class Measure:
    name: str
    dax: str
    description: str = ""
    format_string: str = ""
    folder: str = ""
    table: str = ""
    hidden: bool = False


@dataclass
class Table:
    name: str
    description: str = ""
    data_category: str = ""
    partition_mode: str = ""
    entity_name: str = ""
    columns: dict[str, Column] = field(default_factory=dict)
    measures: dict[str, Measure] = field(default_factory=dict)
    annotations: dict[str, str] = field(default_factory=dict)


@dataclass
class Relationship:
    name: str
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    properties: dict[str, str] = field(default_factory=dict)


@dataclass
class Role:
    name: str
    description: str = ""
    model_permission: str = ""
    table_permissions: dict[str, str] = field(default_factory=dict)


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def parse_table(path: Path) -> Table:
    table: Table | None = None
    pending_description = ""
    current_column: Column | None = None
    current_measure: Measure | None = None

    for raw in _lines(path):
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("///"):
            pending_description = stripped[3:].strip()
            continue
        indent = len(line) - len(line.lstrip("\t"))

        if stripped.startswith("table "):
            table = Table(stripped.split(" ", 1)[1].strip(), description=pending_description)
            pending_description = ""
            continue
        assert table is not None, f"{path.name} does not start with a table"

        if indent == 1:
            current_column = current_measure = None
            if stripped.startswith("column "):
                current_column = Column(stripped.split(" ", 1)[1].strip(), description=pending_description)
                table.columns[current_column.name] = current_column
            elif stripped.startswith("measure "):
                name, _, dax = stripped[len("measure "):].partition("=")
                current_measure = Measure(name.strip().strip("'"), dax.strip(),
                                          description=pending_description, table=table.name)
                table.measures[current_measure.name] = current_measure
            elif stripped.startswith("dataCategory:"):
                table.data_category = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("annotation "):
                key, _, value = stripped[len("annotation "):].partition("=")
                table.annotations[key.strip()] = value.strip()
            pending_description = ""
            continue

        key, _, value = stripped.partition(":")
        key, value = key.strip(), value.strip()
        target = current_column or current_measure
        if target is None:
            if key == "entityName":
                table.entity_name = value
            elif key == "mode":
                table.partition_mode = value
            continue
        if key == "dataType" and isinstance(target, Column):
            target.data_type = value
        elif key == "formatString":
            target.format_string = value
        elif key == "sortByColumn" and isinstance(target, Column):
            target.sort_by = value
        elif key == "displayFolder" and isinstance(target, Measure):
            target.folder = value
        elif stripped == "isHidden":
            target.hidden = True
    return table


def tables() -> dict[str, Table]:
    return {t.name: t for t in (parse_table(p) for p in sorted((DEFINITION / "tables").glob("*.tmdl")))}


def measures() -> dict[str, Measure]:
    out: dict[str, Measure] = {}
    for table in tables().values():
        out.update(table.measures)
    return out


def relationships() -> list[Relationship]:
    out: list[Relationship] = []
    current: Relationship | None = None
    for raw in _lines(DEFINITION / "relationships.tmdl"):
        stripped = raw.strip()
        if stripped.startswith("relationship "):
            current = Relationship(stripped.split(" ", 1)[1].strip(), "", "", "", "")
            out.append(current)
        elif current and stripped.startswith("fromColumn:"):
            current.from_table, current.from_column = stripped.split(":", 1)[1].strip().split(".")
        elif current and stripped.startswith("toColumn:"):
            current.to_table, current.to_column = stripped.split(":", 1)[1].strip().split(".")
        elif current and ":" in stripped:
            key, _, value = stripped.partition(":")
            current.properties[key.strip()] = value.strip()
    return out


def roles() -> dict[str, Role]:
    out: dict[str, Role] = {}
    for path in sorted((DEFINITION / "roles").glob("*.tmdl")):
        role: Role | None = None
        description = ""
        for raw in _lines(path):
            stripped = raw.strip()
            if stripped.startswith("///"):
                description = stripped[3:].strip()
            elif stripped.startswith("role "):
                role = Role(stripped.split(" ", 1)[1].strip().strip("'"), description=description)
                out[role.name] = role
            elif role and stripped.startswith("modelPermission:"):
                role.model_permission = stripped.split(":", 1)[1].strip()
            elif role and stripped.startswith("tablePermission "):
                body = stripped[len("tablePermission "):]
                table, _, expression = body.partition("=")
                role.table_permissions[table.strip()] = expression.strip()
    return out


def model_table_refs() -> list[str]:
    return [line.split(" ", 2)[2].strip()
            for line in _lines(DEFINITION / "model.tmdl") if line.startswith("ref table ")]


def model_role_refs() -> list[str]:
    return [line.split(" ", 2)[2].strip().strip("'")
            for line in _lines(DEFINITION / "model.tmdl") if line.startswith("ref role ")]


COLUMN_REFERENCE = re.compile(r"(\w+)\[(\w+)\]")
MEASURE_REFERENCE = re.compile(r"(?<![\w'])\[([^\[\]]+)\]")


def dax_column_references(dax: str) -> set[tuple[str, str]]:
    return {(table, column) for table, column in COLUMN_REFERENCE.findall(dax)}


def dax_measure_references(dax: str) -> set[str]:
    return set(MEASURE_REFERENCE.findall(dax)) - {column for _, column in COLUMN_REFERENCE.findall(dax)}
