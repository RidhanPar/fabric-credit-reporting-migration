# Phase 5 manual steps

Phase 5 is the semantic model and the report. The model is committed here as TMDL
text, so it is reviewable and testable, but it has not been opened by the Power BI
engine yet, because Direct Lake needs a Fabric capacity. Section A runs now.
Sections B to E are for when you have a capacity.

## A. Check the model locally (no capacity needed)

```bash
py -3.11 scripts/build_semantic_model.py
```

```bash
py -3.11 -m pytest -q tests/test_semantic_model.py
```

**You should see:** `12 tables, 26 measures, 20 relationships, 4 roles`, then
`14 passed`.

Then read two files. They are the deliverable of this phase:

* `fabric/semantic-model/LendCoPortfolio.SemanticModel/definition/tables/fact_balance_snapshot.tmdl`,
  which holds the portfolio and credit quality measures with their business
  definitions as `///` comments.
* `docs/REPORT_SPEC.md`, the four pages and every visual on them.

## B. Create the model in Fabric

Direct Lake needs the connection details of your lakehouse, which are tenant
specific, so the committed TMDL has placeholders.

1. In the workspace, open `lh_portfolio`, then **New semantic model**. Name it
   `LendCoPortfolio_probe`, tick the gold tables, create. This throwaway model
   exists only to read its connection details.
2. Open its settings and copy the **SQL analytics endpoint** string, which looks
   like `abcd1234.datawarehouse.fabric.microsoft.com`.
3. Fill the real values into the local TMDL:

```bash
py -3.11 scripts/set_semantic_model_connection.py --endpoint <your endpoint> --database lh_portfolio
```

4. Commit the model through Git integration (Phase 6 sets this up), or, if you
   prefer to do Phase 5 before Phase 6: open the folder
   `fabric/semantic-model/LendCoPortfolio.SemanticModel` in Power BI Desktop as a
   project and publish it to the workspace.
5. Delete `LendCoPortfolio_probe`.
6. Put the placeholders back before committing anything:

```bash
py -3.11 scripts/set_semantic_model_connection.py --reset
```

**You should see:** a semantic model named `LendCoPortfolio` in the workspace,
with 12 tables, and the measures grouped into the folders Portfolio, Credit
quality, Origination, Applications, Collections, Migration and Trust.

**Screenshot:** `docs/screenshots/p5-model-measures.png` (the fields pane showing
the display folders and one measure's description).

Honest note: if the TMDL does not load first time, that is expected to be a
formatting fix, not a redesign. The quickest recovery is to let Fabric create the
model from the lakehouse, export its TMDL through Git, and diff it against this
folder. Tell me what the error says and I will correct the generator.

## C. Row level security

1. Workspace list, the `LendCoPortfolio` model, `...` > **Security**.
2. You should see four roles: three country managers and group reporting. Add
   yourself to **Poland country manager**.
3. `...` > **Test as role** and open the report.

**You should see:** Poland only, in every visual, and the country slicer showing
one country. Then test as **Group reporting** and see all three.

**Screenshot:** `docs/screenshots/p5-rls-poland.png`

Interview point to keep in mind: this is a reporting control, not a data control.
Anyone with workspace access can read the lakehouse tables underneath it.

## D. Build the report

Follow `docs/REPORT_SPEC.md`, pages 1 to 4. Build page 1 first and check the
country summary table against the legacy workbook's Summary tab for the same
month: the two will not match, and the migration evidence page explains why,
which is the whole point of the parallel run.

**Screenshots:**
* `docs/screenshots/p5-exec-summary.png`
* `docs/screenshots/p5-delinquency.png`
* `docs/screenshots/p5-account-drillthrough.png`
* `docs/screenshots/p5-migration-evidence.png`

## E. What to send back

1. The screenshots above, into `docs/screenshots/`.
2. Any error the model gave while loading, exactly as written.
3. Later, for Phase 6: the Performance Analyzer timings for each page.

Nothing from this phase goes into the README as a measured result until you send
it. The README says plainly that the model is authored but not yet loaded.
