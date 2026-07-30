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
the step **and no Oracle panel runs for the case** — Cory's explicit condition (ADR-014): no Final
Order means no script concordance item, so there is nothing for a reference distribution to
describe. Enforced in code: `POST .../oracle/run` refuses with `no_final_orders`, and
`run_oracle_for_case_version` returns `skipped`.

**"Final Orders" is the settled name** (ADR-014). Do not rename it to "Key Management Decisions":
that collides with the simulator's separate *3 next steps in management* box, which is an
unauthored open text field and is not this instrument. Proposal §4.3 Change 5 is withdrawn.

## 2. The rating stem

> **Not yet approved.** Cory's 2026-07-29 review said the current stem "seems reasonable" and
> asked to see any proposed change first. The revision below *is* a change, so it is implemented
> but not treated as settled. **Do not run a production Oracle panel until the group confirms the
> wording** — the stem determines what the panel is asked, so changing it afterwards invalidates
> every distribution generated before the change. Nothing is lost by waiting: no case carries Final
> Orders yet. See `Decisions.md` ADR-014.

Both wordings live in `backend/utils/oracle_stems.py` and are selected by `ORACLE_STEM_VERSION`:
`v1_original` (Alex's draft) and `v2_revised` (below). Every run is stamped with
`panel_runs.stem_version`. `GET /oracle/stems` renders both side by side **from the code that will
actually run**, so the comparison the group reviews cannot drift from the instrument.

**Learner-facing (`v2_revised`):**

```
Based on the information you gathered during this encounter, and before any
pending results return, ordering a brain MRI now would be:

  -2 = Clearly inappropriate
  -1 = Probably inappropriate
   0 = Equally appropriate to order or not to order
  +1 = Probably appropriate
  +2 = Clearly appropriate

  [ ] My rating would change substantially with information I was not able
      to obtain during this encounter.
```

**Oracle-facing** — same item, different information state:

```
Based on all clinical information documented in this case record, and before
any pending results return, ordering a brain MRI now would be:

  (identical -2 to +2 anchors, no checkbox)
```

### The `{action}` placeholder

The stem takes a **gerund phrase**, not a bare noun: `ordering a brain MRI`,
`activating the stroke team`. Hard-coding `ordering {order}` into the lead renders "ordering
activating the stroke team" for the stroke-activation example from Cory's own list.

Stored as `case_final_orders.stem_action`. When null it is derived from `order_text`, which is
correct for tests and treatments and wrong for activations and consults — so the authoring UI shows
the fully rendered item live as the author types, rather than letting them discover the phrasing
after a panel has run on it.

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

1. `POST /final-orders/propose` returns 3–5 candidates, drawn from the case's `diagnostic_workup`
   and ranked toward the more debatable ones. It **writes nothing**.
2. **Nothing is persisted until the author explicitly accepts, edits, or replaces it.** Candidates
   are suggestions; the decision is the author's. Each carries a `debatability` note saying why
   clinicians would disagree, and a rendered preview of the exact item a learner will read.
3. The author supplies suppression synonyms per order (see §5). Accepting a candidate pre-fills
   them from its suggestions.
4. On finalize (`run_oracle: true`) or on demand, the Oracle panel runs in the background
   (`llm-panels.md`). Never at preview: authors regenerate previews repeatedly while drafting and
   each would otherwise trigger 75 calls.
5. The View tab shows the resulting distribution with item-quality flags, so the author can see
   before shipping that an order is uninformative.

**Editing is supported from day one**, not deferred. Post-finalization editing was the top item
from the March feedback; shipping Final Orders that could not be edited would reopen it
immediately. `PUT /sim-ready/case/{id}/final-orders` replaces the list on the case's latest
version.

### Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/final-orders/propose` | * | Candidate Final Orders. Writes nothing |
| GET | `/sim-ready/case/{id}/final-orders` | No | Orders + flattened suppression terms — the simulator's read |
| PUT | `/sim-ready/case/{id}/final-orders` | * | Replace the list on the latest version |
| GET | `/sim-ready/case/{id}/oracle/preflight` | * | Blinded context, leak audit, rendered items, roster |
| POST | `/sim-ready/case/{id}/oracle/run` | * | Queue the panel; refuses on no orders or a failed audit |
| GET | `/sim-ready/case/{id}/oracle` | No | Distributions and item-quality flags |
| GET | `/oracle/stems` | No | Both stem versions rendered side by side |
| GET | `/oracle/roster` | No | Versioned roster and provider settings |

`GET /` and `/health` report `final_orders` so a deploy missing migration 0003 is visible rather
than mysterious.

**Provenance is recorded** — `author_entered` versus `llm_suggested_accepted`. If the same model
family both proposes an order and rates its appropriateness, the distribution is partly
self-fulfilling. Recording provenance lets that be tested rather than argued about; expect it to
be among the first questions a reviewer asks.

## 4. Schema

```
case_final_orders                                    -- authoring schema, migration 0003
  id                    PK
  case_version_id       FK -> case_versions.id (indexed)   -- version-pinned, ADR-003
  display_order         INT
  order_text            TEXT     -- "Brain MRI" — label, and the simulator's match target
  stem_action           TEXT     -- "ordering a brain MRI"; null derives from order_text
  stem_template         TEXT     -- optional per-order override of the stem lead
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

`stem_template` overrides the lead but **never the anchors** — an author editing wording cannot
accidentally change the scale.

**There is no `case_detail_id` column, deliberately.** The simulator knows only a
`case_details.id`, and resolves Final Orders through the most recent `case_versions` row carrying
that id (`load_final_orders_for_case_detail`, or `GET /sim-ready/case/{id}/final-orders`). A
denormalised copy would look convenient and would make "which orders belong to this case"
ambiguous the moment a case has two versions — which is the situation versioning exists to create.

Cap enforcement: five orders is a hard limit in the Pydantic schema (`max_length=5`) **and**
re-checked in `replace_final_orders` before the write, not a UI convention. It bounds Oracle cost
at 75 calls per case.

Writes **replace** rather than merge. The submitted list is the author's authoritative statement of
what the case has, so a deleted order actually disappears instead of lingering attached.

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
