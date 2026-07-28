# Target architecture — the unified case record

> **Read this when:** touching case storage, the format toggle, the editing flow, versioning, or
> anything that needs to join clinical content to LR/entropy data.
> **Prerequisite:** `../Decisions.md` ADR-001, ADR-002, ADR-003, ADR-011.
> **Does not cover:** LLM panel mechanics (`llm-panels.md`), Final Orders schema
> (`final-orders-sct.md`), learner data handling (`privacy-data-handling.md`).

---

## 1. What is wrong today

Three coupled problems, all downstream of the `output_format` toggle.

**The pipeline forks and one branch discards its work.** `output_format` branches at
`/preview-case`, in `SessionData`, and at `/finalize-case`. The sim-ready branch generates the
diagnostic framework and the full LR matrix, uses them to render nothing, and writes only the
`case_details` row. The LR data lives in `st.session_state.generated_case` and is gone on refresh.
Every sim-ready case has paid for the analysis and thrown it away.

**The two destinations are different databases.** `POSTGRES_URL` (beta tables) and
`POSTGRES_URL_SIM_READY` (`case_details`, shared with the simulator) are separate Neon projects.
There is no FK, no join, no query that relates an LR to the case it describes. This is the
concrete reason "Full LR Schema" is not simulator-compatible.

**Markdown is the source of truth.** For sim-ready cases the canonical artifact is
`case_details.content`, a markdown blob the Edit tab lets authors modify directly.
`CaseSaveRequest.rendered_content` overrides the renderer. After one hand-edit, the structured data
and the rendered document diverge with no path back.

## 2. Target shape

```
                    ┌──────────────────────────────────┐
                    │  authoring schema (shared DB)    │
                    │                                  │
  author edits ───► │  case_families                   │
  structured        │  case_versions      ◄── canonical│
  fields            │    ├─ clinical content (JSONB)   │
                    │    ├─ diagnostic_frameworks      │
                    │    ├─ feature_likelihood_ratios  │
                    │    └─ case_final_orders          │
                    └───────────────┬──────────────────┘
                                    │ render on save
                                    ▼
                    ┌──────────────────────────────────┐
                    │  case_details  (simulator reads) │
                    │    content = rendered projection │
                    └──────────────────────────────────┘
```

Three properties define the target:

1. **One record per case.** Framework and LR data persist for every case, not just "beta" ones.
2. **Structured is canonical, markdown is derived.** `case_details.content` is written by the
   renderer on save. It is an output, not an input.
3. **Everything joins.** All authoring tables live in the shared database alongside
   `case_details`.

The simulator is unaffected. It continues to read `case_details` exactly as it does today, which
is what makes this migration safe to do incrementally.

## 3. Case identity and versioning

```
case_families
  id                PK
  slug              stable human key, e.g. "dizziness-posterior-circulation-stroke"
  title
  created_at, created_by

case_versions
  id                PK
  case_family_id    FK -> case_families.id (indexed)
  version           INT, monotonic within family
  status            draft | published | retired
  content_structured  JSONB   -- canonical clinical record
  content_rendered    TEXT    -- projection written on save
  render_detached     BOOL    -- true if an author hand-edited the markdown
  parent_version_id   FK nullable — set when cloned via "save as new"
  case_detail_id      FK -> case_details.id — the simulator-facing row
  published_at, created_at, updated_at
```

Rules that make this worth having:

- A `published` version is **immutable**. Editing a published version creates version N+1.
- Learner runs, Oracle runs, and LR panels all reference a `case_versions.id`, never a family.
- `parent_version_id` records lineage for "save as new with variables changed", so a family of
  variants (same case, different age/sex/vitals) is traceable.

This is the mechanism that makes performance tracking honest. Without it, an edit silently makes
pre- and post-edit learner data incomparable and nothing records that it happened. It also
subsumes Oracle staleness — a panel is stale exactly when its `case_version_id` is no longer the
published one.

## 4. Editing model

Authors edit structured fields. The renderer produces markdown. Cloning is a structured copy.

- **Save** → new draft version, re-render, update the linked `case_details` row.
- **Save as new** → clone `content_structured` into a new family or a new lineage branch, change
  the variables, re-render. This is the operation that becomes nearly free under ADR-002 and is
  effectively impossible today.
- **Raw markdown override** → still permitted, but sets `render_detached = true` and surfaces a
  visible warning. A detached version cannot be re-rendered or cloned with variable substitution,
  and the UI must say so at the moment of detaching, not afterward.

The Door Chart delimiter (`## PATIENT DOOR CHART and Learner Instructions`) remains load-bearing —
the simulator splits on that exact string. Under this design the renderer guarantees it, rather
than relying on the author not to break it.

## 5. Learner performance data

Stored per item, never as a rolled-up score (ADR-011). Lives in a **separate schema** from case
authoring (ADR-008) so access can be granted independently.

```
learner_runs
  id, case_version_id (FK), unique_code, started_at, completed_at, student_level

learner_item_responses
  id, learner_run_id (FK), item_type, item_ref_id,
  response_value, response_meta (JSONB), created_at
```

`item_type` discriminates Final Order ratings from future item types, matching the panel
subsystem's discriminator (`llm-panels.md`) so the two sides can be compared directly.

**The free result.** With item-level storage, once enough runs accumulate you can compute
difficulty and discrimination per Final Order. That gives two independent estimates of item
quality — Oracle entropy as the *a priori* prediction, observed learner variance as the
*empirical* one. The comparison is a publishable question and it costs nothing if the schema is
granular from day one.

## 6. Migration path

Ordered so each step is independently useful and nothing is a big-bang cutover.

1. **Persist framework + LRs on the sim-ready path.** Smallest change on the board, and it stops
   the ongoing data loss. Do this before anything else.
2. **Consolidate databases.** Move the authoring tables into the shared database under an
   `authoring` schema. One-time migration of a small dataset. Migrations run through the
   simulator's Alembic (`direct-sim/alembic/`) — note its `versions/` directory is empty while
   `create_all` runs at startup, so a baseline must be stamped first.
3. **Add `case_families` / `case_versions`.** Backfill existing cases as version 1 of their own
   family.
4. **Final Orders + Oracle** on top of the unified record (`final-orders-sct.md`).
5. **Invert the editing model** — structured field editing in the UI, renderer on save.
6. **LR re-assessment** reusing the panel subsystem (`llm-panels.md`).
7. **Retire the toggle.** By this point `output_format` selects an export format, not a storage
   path, and can be removed from the Generate tab.

Steps 1–3 are prerequisites for everything else. Steps 4 and 6 are independent of each other.
