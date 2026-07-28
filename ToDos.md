# ToDos

Sequenced work for the OSCE/SCT build. Decisions behind these items are in `Decisions.md`;
specifications are in `docs/` (see `docs/README.md` for the map).

Simulator-side work is tracked separately in `../direct-sim/FINAL_ORDERS_TODO.md`.

---

## Phase 1 — Stop the data loss, unify the record

Prerequisites for everything else.

- [x] **`authoring` schema in the shared database** — `case_families`, `case_versions`,
      `diagnostic_frameworks`, `feature_likelihood_ratios`. New writes land in the ADR-001
      destination directly rather than via a cross-database bridge. `backend/models/database.py`
- [x] **Persist framework + LRs on the sim-ready path.** Was generated and discarded on every
      case. `backend/utils/authoring_store.py`, finalize path in `backend/app/main.py` `ADR-001`
- [x] **`case_families` + `case_versions` with lineage** (`parent_version_id`) and a version
      counter, in place from the first write rather than retrofitted `ADR-003`
- [x] **`GET /sim-ready/case/{id}/analysis`** so persisted framework/LR data is reachable and the
      Export tab can stop depending on session state
- [x] **Alembic owns the schema.** Baseline stamped + `authoring` migration in `direct-sim`
      (`0001_baseline`, `0002_authoring_schema`). The app now *detects* the tables via
      `authoring_schema_ready()` and never runs DDL for them. Runbook: `direct-sim/MIGRATIONS.md`
- [x] **Migration applied to production** (2026-07-28). Needed `stamp 0001_baseline --purge`,
      not a plain stamp: `alembic_version` held `867af2511f84`, a revision whose file had been
      deleted. Verified after: `authoring` present with all four tables, `case_details`/
      `transcripts`/`assessments` row counts unchanged.
- [ ] **Point the Export tab at the new endpoint** instead of `st.session_state`
- [ ] **Version on edit** — `PUT /sim-ready/case/{id}` currently updates in place without
      creating a new `case_version`. Should pass `family_id` + `parent_version_id` `ADR-003`
- [ ] **Backfill** existing sim-ready cases as v1 of their own family (analysis unavailable for
      those — it was never stored)
- [ ] **Migrate the legacy beta tables** out of the second Neon project into `authoring` `ADR-001`
- [ ] **Split learner data into its own schema** `ADR-008`

### Schema ownership — settled

Alembic in `direct-sim` owns the shared database's schema, including `authoring`. This app
detects the tables and disables authoring persistence if they are absent; it never runs DDL for
them. Models live here, migrations live there — keep them in step, nothing enforces it.

Verified on a throwaway Postgres: both revisions apply cleanly, and `alembic revision
--autogenerate` afterward produces an **empty** migration — confirming the baseline matches the
ORM models and that the `include_name` filter stops autogenerate from dropping `authoring.*`.

## Phase 2 — Final Orders

- [ ] `case_final_orders` table `ADR-004`
- [ ] Generation proposes 3–5 candidates from `diagnostic_workup`; author explicitly accepts
- [ ] Authoring UI: order text, stem override, suppression synonyms, provenance
- [ ] Edit support from the start — the update endpoint already exists, and shipping without it
      reopens the March feedback
- [ ] Cap of 5 enforced in the Pydantic schema, not just the UI

## Phase 3 — Oracle panel

- [ ] Shared panel runner: `panel_runs` + `panel_ratings`, `item_type` discriminator `ADR-006`
- [ ] Responses API call path (`gpt-5.6`, `reasoning.effort`) — separate from the existing
      `gpt-4o` / `chat.completions.parse` path
- [ ] Blinded Oracle view built from structured fields, failing closed `ADR-005`
- [ ] Blocking leak audit against the diagnosis and its synonyms
- [ ] Background execution + status endpoint (3–5 min exceeds ingress timeouts)
- [ ] Version-pinned staleness + re-run, append-only `ADR-003`
- [ ] **Entropy / discrimination display for case authors** — the item-quality flags in
      `docs/llm-panels.md` §7

## Phase 4 — Editing model

- [ ] Structured field editing replaces split-markdown editing `ADR-002`
- [ ] Renderer writes `case_details.content` on save
- [ ] Save-as-new with variable substitution; `parent_version_id` lineage
- [ ] Raw-markdown override sets `render_detached` and warns at the moment of detaching

## Phase 5 — LR transparency and re-assessment

- [ ] LR editing UI with provenance display `ADR-007`
- [ ] Re-assessment via the shared panel runner, blinded to the original value
- [ ] PubMed citation grounding where a source exists; honest recording where none does
- [ ] Divergence flags where the original falls outside the panel's IQR

## Phase 6 — Performance tracking

- [ ] `learner_runs` + `learner_item_responses`, item-level `ADR-011`
- [ ] Per-item difficulty and discrimination once runs accumulate
- [ ] Oracle entropy (a priori) vs. observed learner variance (empirical) comparison
- [ ] Coarsened analytic exports with small-cell suppression `ADR-008`

## Phase 7 — Retire the toggle

- [x] **Sim-Ready / Beta radio removed from the Generate tab.** Brought forward from last
      place: once Sim-Ready persisted the framework and LR matrix (Phase 1), Beta offered
      nothing it lacked while writing to a database the simulator cannot read. `ADR-001`
- [ ] Remove the `output_format` branch from the backend once no Beta case needs re-export
- [ ] Migrate the legacy beta tables into `authoring`, then drop the second Neon project

---

## Deploy integrity

- [x] Unique image tag per build in `deploy-aca.sh` — identical `:v1` tags made
      `az containerapp update` a no-op `ADR-012`
- [x] Build provenance (`GIT_SHA`, `BUILD_TIME`, `IMAGE_TAG`) baked into both images,
      exposed at `GET /` and in the Streamlit footer, with a frontend/backend drift warning
- [ ] **Verify post-finalization editing actually works in production.** The backend ran a
      2026-03-10 image until 2026-07-28, so `92e057c` (2026-03-20) never ran — it is the *only*
      undeployed pre-today commit, checked against the log, so this is a focused pass rather
      than an audit. It is also Cory's top March request, and `CLAUDE.md` documents it as
      shipped. Test:
      - [ ] Edit tab → "Load Existing Sim-Ready Case for Editing" → case list populates
      - [ ] Load a finalized case; content and simulator fields prefill
      - [ ] Edit History Questions / Physical Exam / Framework / LR sections
      - [ ] Save → `PUT /sim-ready/case/{id}` returns 200 and changes persist on reload
      - [ ] Confirm it updated in place rather than creating a duplicate case
- [x] Same build stamp for `direct-sim` — unauthenticated `GET /api/version`, startup log, and
      sidebar footer. One image serves both SPA and API there, so a single stamp covers both
- [ ] Record the generator build in `case_versions` for case provenance — knowing which build
      authored a case matters once cases span months `ADR-003` `ADR-012`

## Privacy — track alongside, not after

- [x] Learner orientation copy: **introduce as "Dr. X"** `ADR-009`
- [x] Patient persona never asks for or echoes a real name; accepts "Dr. X" without comment;
      STT variants (`Dr. Ex` / `Dr. Ecks`) treated as equivalent
- [x] Removed the persona line that actively prompted learners for their name
- [x] "Dr. X" surfaced in the simulator sidebar where the learner sees it before starting.
      Replaced pre-existing copy reading "Dr. L (or your own initial)" — an initial is itself a
      weak identifier in a single-institution cohort
- [ ] Targeted transcript redaction on learner turns, patient names allowlisted
- [ ] Redaction event logging so the rate is observable
- [ ] Verify vendor audio retention for voice mode — larger exposure than transcript text
- [ ] Written data-use agreement with UNMC: we never receive the key
- [ ] IRB determination in writing before collection

---

## Open questions

Awaiting research-group review (defaults in `docs/Final_Orders_Oracle_Proposal.docx` §10):

- [ ] Adopt the revised rating stem?
- [ ] Does "fourth notch" map to reasoning effort `medium`?
- [ ] Approve the 15-role panel roster, including the stewardship / risk-averse pair?
- [ ] Rename "Final Orders" to "Key Management Decisions"?
- [ ] Split the panel across two model families?
- [ ] Schedule the human validation panel?

---

## From the `main` merge (2026-07-28)

The branch had diverged at `9a0e8fc` and never rebased. Resolved in `af97877`; these
are the loose ends it surfaced.

- [ ] **`tier_level` on the beta `feature_likelihood_ratios` table.** `main` added it via a
      runtime `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in `_ensure_schema()`, which ran at
      import. Not ported — runtime DDL conflicts with Alembic owning the schema `ADR-012`.
      The column may already exist in the beta DB from a previous boot. Check, then handle it
      properly when the beta tables migrate into `authoring` `ADR-001`
- [x] **LR matrix endpoints now honour `tier_level`.** They accepted the param and dropped it,
      so CSV/Excel always contained every tier. Default is now **2** (tier 1 is broad, tier 3
      very specific); the Export tab exposes it as a selector so a case can be re-exported at
      tier 1 without regenerating anything
- [x] **`/regenerate-lrs` exposed in the Edit tab.** Re-runs LR generation against the current
      framework with exact bucket names — the fix for renaming a diagnostic bucket, which
      otherwise orphans every LR pointing at the old name `ADR-007`
