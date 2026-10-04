"""Point the semantic model at a lakehouse.

The Direct Lake connection is tenant specific, so the committed TMDL carries
placeholders. This fills them in locally and never commits the values.

    py -3.11 scripts/set_semantic_model_connection.py \\
        --endpoint abc123.datawarehouse.fabric.microsoft.com \\
        --database lh_portfolio

Run with --reset to put the placeholders back before committing.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
EXPRESSIONS = ROOT / "fabric" / "semantic-model" / "LendCoPortfolio.SemanticModel" / "definition" / "expressions.tmdl"
ENDPOINT_PLACEHOLDER = "<SQL_ANALYTICS_ENDPOINT>"
DATABASE_PLACEHOLDER = "<LAKEHOUSE_NAME_OR_ID>"
SOURCE_LINE = re.compile(r'Source = Sql\.Database\("([^"]*)", "([^"]*)"\)')


def apply(endpoint: str, database: str) -> str:
    text = EXPRESSIONS.read_text(encoding="utf-8")
    updated = SOURCE_LINE.sub(f'Source = Sql.Database("{endpoint}", "{database}")', text)
    if updated == text:
        raise SystemExit("expressions.tmdl does not contain the expected Sql.Database line")
    EXPRESSIONS.write_text(updated, encoding="utf-8")
    return f'{endpoint} / {database}'


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", help="SQL analytics endpoint of the lakehouse")
    parser.add_argument("--database", help="lakehouse name or id")
    parser.add_argument("--reset", action="store_true", help="restore the placeholders")
    args = parser.parse_args()

    if args.reset:
        print("reset to", apply(ENDPOINT_PLACEHOLDER, DATABASE_PLACEHOLDER))
        return
    if not (args.endpoint and args.database):
        raise SystemExit("give both --endpoint and --database, or use --reset")
    print("connected to", apply(args.endpoint, args.database))


if __name__ == "__main__":
    main()
