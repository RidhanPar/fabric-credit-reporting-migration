"""Write the Power BI semantic model as TMDL text files.

The model is Direct Lake over the gold tables. It is generated rather than hand
written so that the column list, the measure definitions and their documentation
have a single source, and so a test can check the generated files against the
real gold schema.

    py -3.11 scripts/build_semantic_model.py

Output: fabric/workspace/LendCoPortfolio.SemanticModel/ in the same layout
Fabric Git integration uses, so the folder can be synced straight into a workspace.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).parents[1]
OUT = ROOT / "fabric" / "workspace" / "LendCoPortfolio.SemanticModel"
NAMESPACE = uuid.UUID("1b6e7a8c-0d4f-4c3a-9f51-6c0e6a1d2f30")

# Placeholders filled in by scripts/set_semantic_model_connection.py once the
# workspace exists. Kept out of git as real values, since they are tenant specific.
SQL_ENDPOINT = "<SQL_ANALYTICS_ENDPOINT>"
LAKEHOUSE_DATABASE = "<LAKEHOUSE_NAME_OR_ID>"


def tag(*parts: str) -> str:
    return str(uuid.uuid5(NAMESPACE, "/".join(parts)))


@dataclass
class Column:
    name: str
    data_type: str
    summarize_by: str = "none"
    format_string: str = ""
    hidden: bool = False
    is_key: bool = False
    sort_by: str = ""
    description: str = ""


@dataclass
class Table:
    name: str
    description: str
    columns: list[Column]
    measures: list[Measure] = field(default_factory=list)
    date_table: bool = False


@dataclass
class Measure:
    name: str
    dax: str
    description: str
    format_string: str
    folder: str
    table: str = "fact_balance_snapshot"
    hidden: bool = False


def money(name: str, **kwargs) -> Column:
    return Column(name, "decimal", format_string="#,0.00", **kwargs)


# Tables -------------------------------------------------------------------------------
# Local currency amounts are hidden: adding Czech koruna to Polish zloty is
# meaningless, so the model only exposes EUR. Technical keys and flags are hidden too.

TABLES: list[Table] = [
    Table("dim_date", "Calendar. Marked as the model's date table so time intelligence works.", [
        Column("date_key", "int64", hidden=True),
        Column("date", "dateTime", format_string="yyyy-mm-dd", is_key=True,
               description="Calendar date. The fact tables only carry month end dates."),
        Column("year", "int64"),
        Column("quarter", "int64"),
        Column("month", "int64", hidden=True),
        Column("month_name", "string", sort_by="month"),
        Column("year_month", "string", description="Year and month, as 2026-08."),
        Column("month_end_date", "dateTime", format_string="yyyy-mm-dd", hidden=True),
        Column("month_end_key", "int64", hidden=True),
        Column("is_month_end", "boolean", hidden=True),
    ], date_table=True),

    Table("dim_country", "The three lending markets. Row level security filters this table.", [
        Column("country_code", "string", description="PL, CZ or RO."),
        Column("country_name", "string"),
        Column("currency", "string", description="Local currency. Reporting currency is always EUR."),
    ]),

    Table("dim_product", "Product catalogue: two product lines, five products.", [
        Column("product_code", "string"),
        Column("product_name", "string"),
        Column("product_line", "string", description="Personal Loans or Credit Cards."),
        Column("is_revolving", "boolean", description="True for credit cards."),
        Column("launch_date", "dateTime", format_string="yyyy-mm-dd"),
    ]),

    Table("dim_customer", "One row per customer. Filtered by row level security.", [
        Column("customer_id", "string"),
        Column("country_code", "string", hidden=True),
        Column("employment_status", "string"),
        Column("risk_grade", "string", description="A to E at application."),
        Column("customer_since", "dateTime", format_string="yyyy-mm-dd"),
        money("monthly_income_local", hidden=True),
        Column("age_band", "string"),
    ]),

    Table("dim_account", "One row per account, for the account level drillthrough.", [
        Column("account_id", "string"),
        Column("customer_id", "string", hidden=True),
        Column("application_id", "string", hidden=True),
        Column("country_code", "string", hidden=True),
        Column("product_code", "string", hidden=True),
        Column("currency", "string"),
        Column("open_date", "dateTime", format_string="yyyy-mm-dd"),
        Column("open_date_key", "int64", hidden=True),
        money("original_amount_local", hidden=True),
        Column("term_months", "int64", description="Null for credit cards, which have no term."),
        Column("annual_interest_rate", "decimal", format_string="0.00%"),
        Column("close_date", "dateTime", format_string="yyyy-mm-dd"),
        Column("write_off_date", "dateTime", format_string="yyyy-mm-dd"),
        money("write_off_amount_local", hidden=True),
        Column("account_state", "string", description="OPEN, CLOSED or WRITTEN_OFF."),
    ]),

    Table("fx_rate_monthly", "Treasury rates per month end. Stocks use the closing rate, flows the average.", [
        Column("month_end_key", "int64", hidden=True),
        Column("month_end", "dateTime", format_string="yyyy-mm-dd", hidden=True),
        Column("currency", "string"),
        Column("rate_to_eur_month_end", "decimal", format_string="0.0000"),
        Column("rate_to_eur_month_avg", "decimal", format_string="0.0000"),
    ]),

    Table("fact_balance_snapshot", "One row per account per month end. The balance measures are semi additive.", [
        Column("account_id", "string", hidden=True),
        Column("customer_id", "string", hidden=True),
        Column("country_code", "string", hidden=True),
        Column("product_code", "string", hidden=True),
        Column("currency", "string", hidden=True),
        Column("snapshot_date", "dateTime", format_string="yyyy-mm-dd", hidden=True),
        Column("date_key", "int64", hidden=True),
        Column("account_status", "string"),
        Column("is_active", "boolean", hidden=True),
        Column("days_past_due", "int64"),
        Column("dpd_bucket", "string", sort_by="dpd_bucket_order",
               description="Current, 1-29, 30-59, 60-89, 90-179."),
        Column("dpd_bucket_order", "int64", hidden=True),
        Column("is_dpd30", "boolean", hidden=True),
        Column("is_dpd90", "boolean", hidden=True),
        money("balance_local", hidden=True),
        Column("fx_rate", "decimal", format_string="0.0000", hidden=True),
        money("balance_eur"),
    ]),

    Table("fact_origination", "One row per account, at the month end it was disbursed in.", [
        Column("account_id", "string", hidden=True),
        Column("customer_id", "string", hidden=True),
        Column("country_code", "string", hidden=True),
        Column("product_code", "string", hidden=True),
        Column("currency", "string", hidden=True),
        Column("open_date", "dateTime", format_string="yyyy-mm-dd", hidden=True),
        Column("date_key", "int64", hidden=True),
        Column("month_end_key", "int64", hidden=True),
        money("amount_local", hidden=True),
        Column("fx_rate_avg", "decimal", format_string="0.0000", hidden=True),
        money("amount_eur"),
        Column("fx_rate_month_end", "decimal", format_string="0.0000", hidden=True),
        money("amount_eur_month_end_rate", hidden=True),
    ]),

    Table("fact_application", "One row per credit application, in its current state.", [
        Column("application_id", "string", hidden=True),
        Column("customer_id", "string", hidden=True),
        Column("country_code", "string", hidden=True),
        Column("product_code", "string", hidden=True),
        Column("channel", "string", description="ONLINE, BRANCH or BROKER."),
        Column("risk_grade", "string"),
        Column("status", "string", description="APPROVED, DECLINED, WITHDRAWN or INCOMPLETE."),
        Column("application_date", "dateTime", format_string="yyyy-mm-dd", hidden=True),
        Column("date_key", "int64", hidden=True),
        Column("month_end_key", "int64", hidden=True),
        Column("decision_date", "dateTime", format_string="yyyy-mm-dd"),
        Column("is_decisioned", "boolean", hidden=True),
        Column("is_approved", "boolean", hidden=True),
        money("requested_amount_local", hidden=True),
        money("requested_amount_eur"),
    ]),

    Table("fact_repayment", "One row per payment received.", [
        Column("payment_id", "string", hidden=True),
        Column("account_id", "string", hidden=True),
        Column("customer_id", "string", hidden=True),
        Column("country_code", "string", hidden=True),
        Column("product_code", "string", hidden=True),
        Column("currency", "string", hidden=True),
        Column("payment_date", "dateTime", format_string="yyyy-mm-dd", hidden=True),
        Column("date_key", "int64", hidden=True),
        Column("payment_channel", "string", description="DIRECT_DEBIT, BANK_TRANSFER or CARD."),
        money("amount_local", hidden=True),
        money("amount_eur"),
    ]),

    Table("dq_results", "Data quality check results, one row per check per run. Reported on, not joined.", [
        Column("batch_id", "string"),
        Column("layer", "string", description="silver, gold or kpi."),
        Column("table_name", "string"),
        Column("check_name", "string"),
        Column("kind", "string"),
        Column("severity", "string", description="ERROR stops the publish, WARN does not."),
        Column("rows_checked", "int64"),
        Column("rows_failed", "int64"),
        Column("passed", "boolean"),
        Column("detail", "string"),
        Column("seconds", "double", format_string="0.00"),
        Column("checked_at", "dateTime", format_string="yyyy-mm-dd hh:nn:ss"),
    ]),

    Table("recon_attribution", "Every difference between the legacy pack and gold, with its named cause.", [
        Column("month_end", "dateTime", format_string="yyyy-mm-dd"),
        Column("scope", "string", description="Country code, or GROUP."),
        Column("kpi", "string"),
        Column("amount", "double", format_string="#,0.00",
               description="Effect of this cause on this figure, in the KPI's own unit."),
        Column("tolerance", "double", format_string="#,0.00######", hidden=True),
        Column("cause_id", "string", description="F1 to F3 are legacy faults, D1 to D8 definition changes."),
        Column("cause", "string"),
        Column("classification", "string",
               description="LEGACY_ERROR, DEFINITION_CHANGE, NEW_MODEL_ERROR or UNEXPLAINED."),
        Column("kind", "string", hidden=True),
    ]),
]


# Measures -------------------------------------------------------------------------------
# Every measure carries the business definition it was signed off with. A test fails
# if any measure has no description or no format string.

BALANCE = "fact_balance_snapshot"

MEASURES: list[Measure] = [
    Measure("Portfolio balance (EUR)",
            "LASTNONBLANKVALUE(dim_date[date_key], CALCULATE(SUM(fact_balance_snapshot[balance_eur]), "
            "fact_balance_snapshot[is_active] = TRUE()))",
            "Balance of active accounts in EUR at a month end, converted at that month's closing rate. "
            "A balance is a stock, so over a longer period this reports the latest month end in the "
            "period rather than a sum of month ends.",
            "#,0", "Portfolio"),
    Measure("Portfolio balance last month (EUR)",
            "CALCULATE([Portfolio balance (EUR)], DATEADD(dim_date[date], -1, MONTH))",
            "The portfolio balance one month earlier, used by the growth measure. Time intelligence "
            "like this only works because dim_date is marked as the model's date table.",
            "#,0", "Portfolio", hidden=True),
    Measure("Portfolio balance MoM %",
            "DIVIDE([Portfolio balance (EUR)] - [Portfolio balance last month (EUR)], "
            "[Portfolio balance last month (EUR)])",
            "Month on month growth of the portfolio balance.",
            "0.0%", "Portfolio"),
    Measure("Active accounts",
            "LASTNONBLANKVALUE(dim_date[date_key], CALCULATE(COUNTROWS(fact_balance_snapshot), "
            "fact_balance_snapshot[is_active] = TRUE()))",
            "Accounts with an active status at a month end. Semi additive, like the balance.",
            "#,0", "Portfolio"),
    Measure("Active customers",
            "LASTNONBLANKVALUE(dim_date[date_key], CALCULATE(DISTINCTCOUNT(fact_balance_snapshot[customer_id]), "
            "fact_balance_snapshot[is_active] = TRUE()))",
            "Distinct customers holding at least one active account at a month end. A customer with a loan "
            "and a card counts once.",
            "#,0", "Portfolio"),
    Measure("Avg balance per customer (EUR)",
            "DIVIDE([Portfolio balance (EUR)], [Active customers])",
            "Portfolio balance divided by distinct active customers. This is the definition agreed in the "
            "migration. The legacy pack divided by active accounts, which is a different number.",
            "#,0", "Portfolio"),
    Measure("Avg balance per account (EUR)",
            "DIVIDE([Portfolio balance (EUR)], [Active accounts])",
            "The legacy definition of average balance, kept so the old and new figures can be compared "
            "during the parallel run.",
            "#,0", "Migration"),

    Measure("Balance 30+ DPD (EUR)",
            "LASTNONBLANKVALUE(dim_date[date_key], CALCULATE(SUM(fact_balance_snapshot[balance_eur]), "
            "fact_balance_snapshot[is_dpd30] = TRUE()))",
            "Balance of active accounts 30 or more days past due, at a month end.",
            "#,0", "Credit quality"),
    Measure("30+ DPD rate",
            "DIVIDE([Balance 30+ DPD (EUR)], [Portfolio balance (EUR)])",
            "Balance 30 or more days past due as a share of the active portfolio balance. A balance "
            "weighted rate, not an account count rate, because it is the money at risk that matters for "
            "impairment.",
            "0.00%", "Credit quality"),
    Measure("Balance 90+ DPD (EUR)",
            "LASTNONBLANKVALUE(dim_date[date_key], CALCULATE(SUM(fact_balance_snapshot[balance_eur]), "
            "fact_balance_snapshot[is_dpd90] = TRUE()))",
            "Balance of active accounts 90 or more days past due, at a month end. Accounts are written off "
            "at 180 days, so they leave this measure then.",
            "#,0", "Credit quality"),
    Measure("90+ DPD rate",
            "DIVIDE([Balance 90+ DPD (EUR)], [Portfolio balance (EUR)])",
            "Balance 90 or more days past due as a share of the active portfolio balance.",
            "0.00%", "Credit quality"),
    Measure("30+ DPD rate last month",
            "CALCULATE([30+ DPD rate], DATEADD(dim_date[date], -1, MONTH))",
            "The 30+ rate one month earlier, used by the change measure. Hidden, because a report "
            "author should pick the change measure rather than build the subtraction again.",
            "0.00%", "Credit quality", hidden=True),
    Measure("30+ DPD rate change",
            "[30+ DPD rate] - [30+ DPD rate last month]",
            "Change in the 30+ rate against the previous month, in percentage points.",
            "+0.00%;-0.00%;0.00%", "Credit quality"),

    Measure("New accounts", "COUNTROWS(fact_origination)",
            "Accounts disbursed in the period, counted in the month the money left the building.",
            "#,0", "Origination", table="fact_origination"),
    Measure("New originations (EUR)", "SUM(fact_origination[amount_eur])",
            "Amount disbursed in the period, in EUR. A flow, so it is converted at the monthly average "
            "rate rather than the month end rate.",
            "#,0", "Origination", table="fact_origination"),
    Measure("Avg loan size (EUR)", "DIVIDE([New originations (EUR)], [New accounts])",
            "Average amount disbursed per new account.",
            "#,0", "Origination", table="fact_origination"),

    Measure("Applications received", "COUNTROWS(fact_application)",
            "Applications submitted in the period, whatever happened to them.",
            "#,0", "Applications", table="fact_application"),
    Measure("Applications decisioned",
            "CALCULATE(COUNTROWS(fact_application), fact_application[is_decisioned] = TRUE())",
            "Applications that reached a credit decision: approved or declined. Withdrawn and incomplete "
            "applications are excluded.",
            "#,0", "Applications", table="fact_application"),
    Measure("Applications approved",
            "CALCULATE(COUNTROWS(fact_application), fact_application[is_approved] = TRUE())",
            "Applications approved in the period, counted by the month the application was submitted, "
            "so approvals line up with the demand that produced them.",
            "#,0", "Applications", table="fact_application"),
    Measure("Approval rate", "DIVIDE([Applications approved], [Applications decisioned])",
            "Approved as a share of decisioned applications. This is the definition agreed in the "
            "migration, because an application nobody decided says nothing about credit appetite.",
            "0.0%", "Applications", table="fact_application"),
    Measure("Approval rate (legacy definition)", "DIVIDE([Applications approved], [Applications received])",
            "The legacy definition: approved over everything received, including withdrawn and incomplete. "
            "Kept for the parallel run, and it reads about 5 points lower.",
            "0.0%", "Migration", table="fact_application"),

    Measure("Repayments (EUR)", "SUM(fact_repayment[amount_eur])",
            "Payments received in the period, in EUR at the monthly average rate.",
            "#,0", "Collections", table="fact_repayment"),

    Measure("Quality checks run", "COUNTROWS(dq_results)",
            "Data quality checks recorded for the runs in context.",
            "#,0", "Trust", table="dq_results"),
    Measure("Quality checks failed",
            "CALCULATE(COUNTROWS(dq_results), dq_results[passed] = FALSE())",
            "Checks that failed. An ERROR here means gold did not publish.",
            "#,0", "Trust", table="dq_results"),
    Measure("Legacy error effect (EUR)",
            "CALCULATE(SUM(recon_attribution[amount]), recon_attribution[classification] = \"LEGACY_ERROR\", "
            "recon_attribution[kpi] = \"portfolio_balance_eur\")",
            "How much of the difference between the legacy pack and the new model is explained by faults in "
            "the legacy formulas, on the portfolio balance. Negative means the legacy pack was too high.",
            "#,0", "Trust", table="recon_attribution"),
    Measure("Unexplained differences",
            "CALCULATE(COUNTROWS(recon_attribution), recon_attribution[classification] = \"UNEXPLAINED\")",
            "Published figures whose difference has no named cause. This must be zero, or the migration is "
            "not finished.",
            "#,0", "Trust", table="recon_attribution"),
]


# Relationships ---------------------------------------------------------------------------
# Many to one, single direction, everywhere. The facts carry their own country and
# product keys, so dim_account and dim_customer are deliberately not joined to
# dim_country or dim_product: that would create two paths to the same dimension.

RELATIONSHIPS: list[tuple[str, str, str, str]] = [
    ("fact_balance_snapshot", "date_key", "dim_date", "date_key"),
    ("fact_balance_snapshot", "account_id", "dim_account", "account_id"),
    ("fact_balance_snapshot", "customer_id", "dim_customer", "customer_id"),
    ("fact_balance_snapshot", "product_code", "dim_product", "product_code"),
    ("fact_balance_snapshot", "country_code", "dim_country", "country_code"),
    ("fact_origination", "date_key", "dim_date", "date_key"),
    ("fact_origination", "account_id", "dim_account", "account_id"),
    ("fact_origination", "customer_id", "dim_customer", "customer_id"),
    ("fact_origination", "product_code", "dim_product", "product_code"),
    ("fact_origination", "country_code", "dim_country", "country_code"),
    ("fact_application", "date_key", "dim_date", "date_key"),
    ("fact_application", "customer_id", "dim_customer", "customer_id"),
    ("fact_application", "product_code", "dim_product", "product_code"),
    ("fact_application", "country_code", "dim_country", "country_code"),
    ("fact_repayment", "date_key", "dim_date", "date_key"),
    ("fact_repayment", "account_id", "dim_account", "account_id"),
    ("fact_repayment", "customer_id", "dim_customer", "customer_id"),
    ("fact_repayment", "product_code", "dim_product", "product_code"),
    ("fact_repayment", "country_code", "dim_country", "country_code"),
    ("fx_rate_monthly", "month_end_key", "dim_date", "date_key"),
]

# Row level security ------------------------------------------------------------------------
# Static roles: three countries and group. Every role filters all three tables that
# carry a country, because dim_account and dim_customer are not joined to dim_country,
# so a visual built only from those tables would otherwise show everything.
RLS_TABLES = ("dim_country", "dim_account", "dim_customer")
ROLES: list[tuple[str, str]] = [
    ("Poland country manager", "PL"),
    ("Czech country manager", "CZ"),
    ("Romania country manager", "RO"),
]
GROUP_ROLE = "Group reporting"


def column_tmdl(table: Table, column: Column) -> list[str]:
    # A TMDL description is a /// comment on the line above the object it describes.
    lines = [f"\t/// {column.description}"] if column.description else []
    lines.append(f"\tcolumn {column.name}")
    lines.append(f"\t\tdataType: {column.data_type}")
    if column.is_key:
        lines.append("\t\tisKey")
    if column.hidden:
        lines.append("\t\tisHidden")
    if column.format_string:
        lines.append(f'\t\tformatString: {column.format_string}')
    lines.append(f"\t\tlineageTag: {tag(table.name, column.name)}")
    lines.append(f"\t\tsummarizeBy: {column.summarize_by}")
    lines.append(f"\t\tsourceColumn: {column.name}")
    if column.sort_by:
        lines.append(f"\t\tsortByColumn: {column.sort_by}")
    return lines


def measure_tmdl(measure: Measure) -> list[str]:
    lines = ["", f"\t/// {measure.description}", f"\tmeasure '{measure.name}' = {measure.dax}"]
    lines.append(f"\t\tformatString: {measure.format_string}")
    if measure.hidden:
        lines.append("\t\tisHidden")
    lines.append(f"\t\tdisplayFolder: {measure.folder}")
    lines.append(f"\t\tlineageTag: {tag('measure', measure.name)}")
    return lines


def table_tmdl(table: Table) -> str:
    lines = [f"/// {table.description}", f"table {table.name}"]
    if table.date_table:
        lines.append("\tdataCategory: Time")
    lines.append(f"\tlineageTag: {tag('table', table.name)}")
    for measure in [m for m in MEASURES if m.table == table.name]:
        lines += measure_tmdl(measure)
    for column in table.columns:
        lines.append("")
        lines += column_tmdl(table, column)
    lines += [
        "",
        f"\tpartition {table.name} = entity",
        "\t\tmode: directLake",
        "\t\tsource",
        f"\t\t\tentityName: {table.name}",
        "\t\t\tschemaName: dbo",
        "\t\t\texpressionSource: DatabaseQuery",
    ]
    if table.date_table:
        lines += ["", "\tannotation PBI_MarkedDateTable = true"]
    return "\n".join(lines) + "\n"


def model_tmdl() -> str:
    lines = [
        "model Model",
        "\tculture: en-GB",
        "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
        "\tdiscourageImplicitMeasures",
        "\tsourceQueryCulture: en-GB",
        "\tdataAccessOptions",
        "\t\tlegacyRedirects",
        "\t\treturnErrorValuesAsNull",
        "",
        'annotation PBI_QueryOrder = ["DatabaseQuery"]',
        "",
        'annotation PBI_ProTooling = ["DevMode"]',
        "",
        "ref table " + "\nref table ".join(t.name for t in TABLES),
        "",
        "ref role " + "\nref role ".join([f"'{name}'" for name, _ in ROLES] + [f"'{GROUP_ROLE}'"]),
        "",
    ]
    return "\n".join(lines)


def relationships_tmdl() -> str:
    blocks = []
    for from_table, from_column, to_table, to_column in RELATIONSHIPS:
        name = tag("relationship", from_table, from_column, to_table)
        blocks.append("\n".join([
            f"relationship {name}",
            f"\tfromColumn: {from_table}.{from_column}",
            f"\ttoColumn: {to_table}.{to_column}",
        ]))
    return "\n\n".join(blocks) + "\n"


def role_tmdl(name: str, country: str | None) -> str:
    lines = [
        f"/// {'Sees every country.' if country is None else f'Sees {country} only.'}",
        f"role '{name}'",
        "\tmodelPermission: read",
    ]
    if country is not None:
        for table in RLS_TABLES:
            lines += ["", f"\ttablePermission {table} = {table}[country_code] = \"{country}\""]
    return "\n".join(lines) + "\n"


def expressions_tmdl() -> str:
    return "\n".join([
        "/// Direct Lake connection to the lakehouse. Filled in per workspace by",
        "/// scripts/set_semantic_model_connection.py, because the values are tenant specific.",
        "expression DatabaseQuery =",
        "\t\tlet",
        f'\t\t    Source = Sql.Database("{SQL_ENDPOINT}", "{LAKEHOUSE_DATABASE}")',
        "\t\tin",
        "\t\t    Source",
        f"\tlineageTag: {tag('expression', 'DatabaseQuery')}",
        "\tannotation PBI_IncludeFutureArtifacts = False",
        "",
    ])


MEASURE_DOC = ROOT / "docs" / "MEASURES.md"


def measure_doc() -> str:
    """The measure dictionary, for people who do not want to read TMDL."""
    lines = [
        "# Measure dictionary",
        "",
        "Generated by `scripts/build_semantic_model.py` from the semantic model. Each",
        "measure's description is also stored in the model itself, so the definition",
        "travels with the number into Power BI tooltips.",
        "",
        "Do not edit this file by hand.",
        "",
    ]
    folders: dict[str, list[Measure]] = {}
    for measure in MEASURES:
        folders.setdefault(measure.folder, []).append(measure)
    for folder, items in folders.items():
        lines += [f"## {folder}", ""]
        for m in items:
            lines += [
                f"### {m.name}" + ("  (hidden)" if m.hidden else ""),
                "",
                m.description,
                "",
                f"Table: `{m.table}`. Format: `{m.format_string}`.",
                "",
                "```dax",
                f"{m.name} = {m.dax}",
                "```",
                "",
            ]
    return "\n".join(lines)


def main() -> None:
    definition = OUT / "definition"
    for folder in (definition / "tables", definition / "roles"):
        folder.mkdir(parents=True, exist_ok=True)
    # Clear generated files rather than the folders, so an open editor or a shell
    # sitting in the directory cannot block the rebuild on Windows.
    for stale in list(definition.rglob("*.tmdl")):
        stale.unlink()

    (OUT / ".platform").write_text(json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": "SemanticModel", "displayName": "LendCoPortfolio"},
        "config": {"version": "2.0", "logicalId": tag("item", "semanticmodel")},
    }, indent=2) + "\n", encoding="utf-8")
    (OUT / "definition.pbism").write_text(json.dumps({
        "version": "4.2",
        "settings": {},
    }, indent=2) + "\n", encoding="utf-8")

    (definition / "database.tmdl").write_text("database\n\tcompatibilityLevel: 1604\n", encoding="utf-8")
    (definition / "model.tmdl").write_text(model_tmdl(), encoding="utf-8")
    (definition / "expressions.tmdl").write_text(expressions_tmdl(), encoding="utf-8")
    (definition / "relationships.tmdl").write_text(relationships_tmdl(), encoding="utf-8")
    for table in TABLES:
        (definition / "tables" / f"{table.name}.tmdl").write_text(table_tmdl(table), encoding="utf-8")
    for name, country in ROLES:
        (definition / "roles" / f"{name}.tmdl").write_text(role_tmdl(name, country), encoding="utf-8")
    (definition / "roles" / f"{GROUP_ROLE}.tmdl").write_text(role_tmdl(GROUP_ROLE, None), encoding="utf-8")

    MEASURE_DOC.write_text(measure_doc(), encoding="utf-8")

    print(f"{OUT.relative_to(ROOT)}: {len(TABLES)} tables, {len(MEASURES)} measures, "
          f"{len(RELATIONSHIPS)} relationships, {len(ROLES) + 1} roles")


if __name__ == "__main__":
    main()
