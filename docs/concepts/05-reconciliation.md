# 05. Reconciliation (read before Phase 4)

This is the phase that decides whether the migration is believed.

## The situation

You have two sets of numbers for the same 24 months: the legacy Excel pack and
the new gold layer. They disagree. Finance assumes the new one is wrong, because
the old one is the one they have been presenting to the board for two years.

Your job is not to argue. It is to explain every difference.

## Concept: the parallel run

Old and new run side by side over the same periods until every difference is
explained and signed off. Only then is the old report switched off.

**Case:** a lender migrating its impairment reporting ran both for three month
ends. The new model was lower. Nobody could sign off until each difference had a
named cause and an owner. Two causes turned out to be bugs in the old report,
which is a normal outcome of a migration.

**Say:** "A migration is not finished when the new report runs. It is finished
when the difference to the old one is fully explained."

## Concept: the bridge

A bridge walks from the old number to the new number in named steps, so each
step is a number you can defend.

```
Legacy published            34,017,642
  fix A                       -xxx,xxx
  fix B                       -xxx,xxx
= Legacy corrected          xx,xxx,xxx
  definition change C           -x,xxx
= Gold published            30,067,421
  unexplained                        0     <- this must be zero
```

The last line is the point of the whole exercise. If the residual is not zero,
the reconciliation is incomplete and nobody should sign anything.

**Say:** "I reconcile with a bridge, not a variance column. A variance tells you
there is a problem. A bridge tells you what the problem is."

## Concept: how each step is proved

A difference is only explained if you can reproduce it.

* **Legacy error.** Fix the faulty formula in a copy of the workbook,
  recalculate, and show the figure move by exactly the amount claimed. The proof
  is the workbook's own recalculation, not my opinion of what the formula meant.
* **Definition change.** Compute both definitions on the new model and show the
  gap. For example, if a rate's denominator changes, gold can produce the old
  denominator and the new one, so the difference is measured and not estimated.
* **New model error.** Found by elimination: once legacy errors and definition
  changes are removed, anything left is ours. We fix it and the residual returns
  to zero.

## Concept: classifying every difference

| Class | Meaning | Who owns the fix |
|---|---|---|
| Legacy error | The old report was wrong | Nobody fixes the old one. It gets retired, and prior reporting may need restating |
| New model error | The new pipeline is wrong | Us, before go live |
| Definition change | Both are arithmetically right, the definition changed and was agreed | Documented in the KPI definitions and announced |

**Say:** "Those three classes are not the same conversation. One is a
restatement, one is a bug, one is a communication job."

## Concept: finding the cause from the shape of the difference

You rarely know the cause up front. The pattern tells you where to look.

| Shape of the difference | Usual cause |
|---|---|
| A constant percentage, one country only | A rate or factor applied in one place |
| Zero until a given month, then growing | Something that started that month, such as a product launch or a changed extract |
| First month of the series behaves differently | An offset, such as a formula reaching back one period |
| Only ratios differ, levels agree | A denominator definition |
| Only one country's levels differ | A country specific formula or a currency conversion |

**Say:** "I diagnose from the shape before I read a single formula. Where the
difference starts and whether it scales tells you what kind of mistake it is."

## Materiality

Not every cent is worth a meeting. The reconciliation declares a tolerance,
for example 0.01 EUR on an amount and half a basis point on a rate, and anything
inside it is rounding. Anything outside it must be named. A tolerance is a
published decision, not a place to hide a difference.

## What Phase 4 will produce

* A row for every month, country and KPI: legacy value, gold value, difference,
  class, cause, and the amount attributed to each cause.
* A bridge per KPI showing the walk from legacy to gold.
* A findings document with, for each cause: which figure, how large, over which
  months, and the proof.
* A test asserting the unexplained residual is zero everywhere, so the claim
  cannot quietly stop being true.
