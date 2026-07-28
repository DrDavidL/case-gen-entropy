# Final Orders (SCT) — authoring specification

> **Read this when:** building the Final Orders authoring flow, its schema, or the rating stem.
> **Prerequisite:** `../Decisions.md` ADR-004, ADR-005, ADR-010.
> **Does not cover:** panel mechanics (`llm-panels.md`), simulator suppression and rating
> collection (`../../direct-sim/FINAL_ORDERS_TODO.md`), the research-group rationale
> (`Final_Orders_Oracle_Proposal.docx`).

---

## 1. What a Final Order is

Up to five author-chosen clinical actions per case — a diagnostic test, treatment, consultation,
or activation — whose *appropriateness* the learner rates after the encounter on a −2..+2 scale.
Examples: ordering a brain MRI, ordering an echocardiogram, obtaining a PET scan, activating the
stroke team.

Per-case and optional, with no global admin toggle. Zero rows means the simulator never presents
the step.

## 2. The rating stem

**Learner-facing:**

```
Based on the information you gathered during this encounter, and before any
pending results return, ordering a brain MRI now would be:

  -2  Clearly inappropriate
  -1  Probably inappropriate
   0  Equally appropriate to order or not to order
  +1  Probably appropriate
  +2  Clearly appropriate

  [ ] My rating would change substantially with information I was not able
      to obtain during this encounter.
```

**Oracle-facing** — same item, different information state:

```
Based on all clinical information documented in this case record, and before
any pending results return, ordering a brain MRI now would be:

  (identical -2 to +2 anchors)
```

Two properties are load-bearing and must not be lost in UI work:

- **The information state is explicit and differs by rater.** The learner conditions on what they
  elicited; the Oracle on the full blinded record. Comparing the two is only interpretable if each
  knew what they were conditioning on.
- **The decision is anchored in time** ("now, before any pending results return"). Without it,
  raters silently assume different timepoints and disagree for reasons unrelated to clinical
  judgment — construct-irrelevant variance in exactly the quantity being measured.

The checkbox is separate from the rating and must not be folded into a value of 0. Do not shuffle
the anchors; the scale is ordinal and its direction is part of the instrument.

Full justification for the wording, including why it differs from the original draft, is in
`Final_Orders_Oracle_Proposal.docx` §4.

## 3. Authoring flow

1. Generation proposes 3–5 candidate Final Orders, drawn from the case's existing
   `diagnostic_workup` list and ranked toward the more debatable ones.
2. **Nothing is written until the author explicitly accepts, edits, or replaces it.** Candidates
   are suggestions; the decision is the author's.
3. The author supplies suppression synonyms per order (see §5).
4. On finalize, the Oracle panel runs in the background (`llm-panels.md`).
5. The authoring UI shows the resulting distribution with item-quality flags, so the author can
   see before shipping that an order is uninformative.

**Provenance is recorded** — `author_entered` versus `llm_suggested_accepted`. If the same model
family both proposes an order and rates its appropriateness, the distribution is partly
self-fulfilling. Recording provenance lets that be tested rather than argued about; expect it to
be among the first questions a reviewer asks.

## 4. Schema

```
case_final_orders
  id                    PK
  case_version_id       FK -> case_versions.id (indexed)   -- version-pinned, ADR-003
  display_order         INT
  order_text            TEXT     -- "Order a brain MRI"
  stem_template         TEXT     -- optional per-order override of the default stem
  provenance            VARCHAR  -- author_entered | llm_suggested_accepted
  suppress_results      BOOL     default true
  suppression_message   TEXT     default "Result pending"
  suppression_synonyms  JSONB    -- author-supplied alternate phrasings
  created_at, updated_at
```

Oracle results attach through `panel_runs` with
`item_type = 'final_order_appropriateness'` and `item_ref_id = case_final_orders.id`
(`llm-panels.md` §6). Learner responses attach through `learner_item_responses`
(`architecture-target.md` §5). No Final-Order-specific tables beyond the one above.

Cap enforcement: five orders is a hard limit in the Pydantic schema, not a UI convention — it
bounds Oracle cost at 75 calls per case.

## 5. Suppression synonyms

The simulator suppresses results by matching learner order text against `order_text` plus
`suppression_synonyms`. The list is author-supplied because the author knows the case, and because
conservative explicit matching beats fuzzy similarity here: a false positive degrades the
simulation by suppressing something unrelated, a false negative destroys the measurement.

Examples the UI should prompt for:

| Order | Synonyms |
|---|---|
| Order a brain MRI | `MRI`, `MRI brain`, `magnetic resonance`, `MR brain`, `brain imaging` |
| Order an echocardiogram | `echo`, `TTE`, `transthoracic echo`, `cardiac ultrasound` |
| Activate the stroke team | `stroke alert`, `stroke pager`, `call stroke`, `code stroke` |

Watch the near-miss case: "MRI lumbar spine" must **not** be suppressed when the Final Order is a
brain MRI. This is in the simulator's verification checklist.

## 6. What the simulator does with this

Summarized here only for orientation; the specification and task list live in
`../../direct-sim/FINAL_ORDERS_TODO.md`.

- Suppresses Final Order results during the encounter via **deterministic pre-model
  interception** — matched orders short-circuit before any LLM call, so no leak is possible.
- Collects ratings in **phase 3**, alongside clinical reasoning, before phase 4 reveals anything
  (ADR-010).
- Logs whether the learner actually tried to order the Final Order — itself a data point, since it
  says whether their rating is consistent with their own behavior under uncertainty.
