"""The semantic model must match the tables it actually reads.

The TMDL is hand maintained in a generator script, so the real guard is this:
compare it against the gold schema produced by the pipeline. A renamed or retyped
gold column fails here rather than in the report.
"""
import pytest

from portfolio_migration import semantic_model as sm

# recon_attribution is written by the reconciliation notebook, which needs the legacy
# workbook in OneLake, so it is not part of the local pipeline run.
NOT_BUILT_LOCALLY = {"recon_attribution"}

SPARK_TO_TMDL = {
    "string": "string", "long": "int64", "int": "int64", "integer": "int64",
    "date": "dateTime", "timestamp": "dateTime", "timestamp_ntz": "dateTime",
    "boolean": "boolean", "double": "double", "float": "double",
}


def _tmdl_type(spark_type: str) -> str:
    if spark_type.startswith("decimal"):
        return "decimal"
    return SPARK_TO_TMDL.get(spark_type, spark_type)


@pytest.fixture(scope="module")
def gold_schemas(lake_run):
    lake, _, _ = lake_run
    schemas = {}
    for name in sm.tables():
        if name in NOT_BUILT_LOCALLY:
            continue
        df = lake.read(name)
        schemas[name] = {f.name: _tmdl_type(f.dataType.simpleString()) for f in df.schema.fields}
    return schemas


def test_every_modelled_table_exists_in_gold(gold_schemas):
    assert set(gold_schemas) == set(sm.tables()) - NOT_BUILT_LOCALLY


def test_every_modelled_column_exists_with_a_matching_type(gold_schemas):
    for name, table in sm.tables().items():
        if name in NOT_BUILT_LOCALLY:
            continue
        schema = gold_schemas[name]
        for column in table.columns.values():
            assert column.name in schema, f"{name}[{column.name}] is not in gold"
            assert column.data_type == schema[column.name], (
                f"{name}[{column.name}] is {column.data_type} in the model and "
                f"{schema[column.name]} in gold")


def test_no_gold_column_is_silently_missing_from_the_model(gold_schemas):
    """A new gold column should be a deliberate modelling decision, not an oversight."""
    for name, schema in gold_schemas.items():
        modelled = set(sm.tables()[name].columns)
        assert set(schema) == modelled, (
            f"{name}: gold has {sorted(set(schema) - modelled)} that the model does not")


def test_measures_aggregate_columns_that_are_in_gold(gold_schemas):
    for name, measure in sm.measures().items():
        for table, column in sm.dax_column_references(measure.dax):
            if table in NOT_BUILT_LOCALLY:
                continue
            assert column in gold_schemas[table], f"{name} reads {table}[{column}], not in gold"
