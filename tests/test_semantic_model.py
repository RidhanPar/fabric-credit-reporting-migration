"""Tests on the semantic model's TMDL.

A model saved as text can be reviewed, and it can also be tested. These checks
cover the things that would otherwise only be found by opening the report: an
undocumented measure, a measure pointing at a column that no longer exists, a
bidirectional relationship, a visible local currency column, a role that forgets
one of the tables carrying a country.
"""
from portfolio_migration import kpi_definitions as kpidef
from portfolio_migration import semantic_model as sm

EXPECTED_TABLES = {
    "dim_date", "dim_country", "dim_product", "dim_customer", "dim_account", "fx_rate_monthly",
    "fact_balance_snapshot", "fact_origination", "fact_application", "fact_repayment",
    "dq_results", "recon_attribution",
}
FACTS = ("fact_balance_snapshot", "fact_origination", "fact_application", "fact_repayment")
# The board KPI each measure publishes, so the model cannot drift from the reconciliation.
KPI_MEASURES = {
    "portfolio_balance_eur": "Portfolio balance (EUR)",
    "new_accounts": "New accounts",
    "new_originations_eur": "New originations (EUR)",
    "dpd30_rate": "30+ DPD rate",
    "dpd90_rate": "90+ DPD rate",
    "approval_rate": "Approval rate",
    "avg_balance_per_customer_eur": "Avg balance per customer (EUR)",
}


def test_the_model_lists_exactly_the_tables_it_defines():
    assert set(sm.tables()) == EXPECTED_TABLES
    assert set(sm.model_table_refs()) == EXPECTED_TABLES


def test_every_table_reads_gold_in_direct_lake_mode():
    for name, table in sm.tables().items():
        assert table.partition_mode == "directLake", name
        assert table.entity_name == name, name
        assert table.description, name


def test_every_board_kpi_has_a_measure():
    names = set(sm.measures())
    for kpi, measure in KPI_MEASURES.items():
        assert kpi in kpidef.VARIANT_CHAINS, kpi
        assert measure in names, measure


def test_every_measure_is_documented_and_formatted():
    for name, measure in sm.measures().items():
        assert measure.description, f"{name} has no business definition"
        assert len(measure.description) > 40, f"{name} has a one word description"
        assert measure.format_string, f"{name} has no format string"
        assert measure.folder, f"{name} is not in a display folder"


def test_every_measure_only_references_things_that_exist():
    tables = sm.tables()
    measures = sm.measures()
    for name, measure in measures.items():
        for table, column in sm.dax_column_references(measure.dax):
            assert table in tables, (name, table)
            assert column in tables[table].columns, (name, table, column)
        for referenced in sm.dax_measure_references(measure.dax):
            assert referenced in measures, (name, referenced)


def test_local_currency_columns_are_hidden():
    """Adding koruna to zloty is meaningless, so only EUR is exposed."""
    for name, table in sm.tables().items():
        for column in table.columns.values():
            if column.name.endswith("_local"):
                assert column.hidden, f"{name}[{column.name}] is visible"


def test_technical_keys_and_measure_plumbing_are_hidden():
    """Keys and the boolean flags the measures filter on are not for report authors.

    A boolean that is genuinely a business attribute, such as dim_product[is_revolving],
    stays visible.
    """
    for name, table in sm.tables().items():
        for column in table.columns.values():
            if column.name.endswith("_key"):
                assert column.hidden, f"{name}[{column.name}] is a key and should be hidden"
            if column.name.startswith("is_") and name.startswith("fact_"):
                assert column.hidden, f"{name}[{column.name}] is measure plumbing and should be hidden"


def test_date_table_is_marked_and_sorted():
    date = sm.tables()["dim_date"]
    assert date.data_category == "Time"
    assert date.annotations.get("PBI_MarkedDateTable") == "true"
    assert date.columns["month_name"].sort_by == "month"


def test_relationships_are_many_to_one_and_single_direction():
    tables = sm.tables()
    rels = sm.relationships()
    assert rels
    for rel in rels:
        assert rel.from_table in tables and rel.from_column in tables[rel.from_table].columns, rel.name
        assert rel.to_table in tables and rel.to_column in tables[rel.to_table].columns, rel.name
        assert rel.properties.get("crossFilteringBehavior", "oneDirection") == "oneDirection", rel.name


def test_every_fact_joins_the_date_table_exactly_once():
    for fact in FACTS:
        to_date = [r for r in sm.relationships() if r.from_table == fact and r.to_table == "dim_date"]
        assert len(to_date) == 1, fact
        assert to_date[0].from_column == "date_key"


def test_there_is_only_one_path_from_a_fact_to_each_dimension():
    """Two paths to the same dimension make a model ambiguous, so the dimensions stay flat."""
    pairs = [(r.from_table, r.to_table) for r in sm.relationships()]
    assert len(pairs) == len(set(pairs))
    dimension_to_dimension = [r for r in sm.relationships()
                              if r.from_table.startswith("dim_") and r.to_table.startswith("dim_")]
    assert dimension_to_dimension == []


def test_row_level_security_covers_every_table_that_carries_a_country():
    roles = sm.roles()
    assert set(roles) == {"Poland country manager", "Czech country manager",
                          "Romania country manager", "Group reporting"}
    assert set(sm.model_role_refs()) == set(roles)
    for name, role in roles.items():
        assert role.model_permission == "read", name
        if name == "Group reporting":
            assert role.table_permissions == {}
            continue
        assert set(role.table_permissions) == {"dim_country", "dim_account", "dim_customer"}, name
        country = {"Poland country manager": "PL", "Czech country manager": "CZ",
                   "Romania country manager": "RO"}[name]
        for table, expression in role.table_permissions.items():
            assert expression == f'{table}[country_code] = "{country}"', (name, table)


def test_no_calculated_columns_or_tables_direct_lake_cannot_use():
    for path in (sm.DEFINITION / "tables").glob("*.tmdl"):
        text = path.read_text(encoding="utf-8")
        assert "= calculated" not in text, path.name
        assert "calculationGroup" not in text, path.name


def test_the_connection_is_a_placeholder_not_someone_s_tenant():
    text = (sm.DEFINITION / "expressions.tmdl").read_text(encoding="utf-8")
    assert "<SQL_ANALYTICS_ENDPOINT>" in text
    assert "datawarehouse.fabric.microsoft.com" not in text
