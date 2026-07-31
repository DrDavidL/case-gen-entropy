# ToDos

Sequenced work for the OSCE/SCT build. Decisions behind these items are in `Decisions.md`;
specifications are in `docs/` (see `docs/README.md` for the map).

Simulator-side work is tracked separately in `../direct-sim/FINAL_ORDERS_TODO.md`.

---

## Start here — state as of 2026-07-30

**Phases 1 through 3 are built, deployed, and verified live.** Both working trees are clean.

| | | Verified how |
|---|---|---|
| case-gen live build | `663e0c3` | `GET /` on the backend, built 23:56Z |
| direct-sim live build | `87b15d3` | `GET /api/version` on **`direct-sim-beta`** |
| Shared DB revision | `0003_final_orders_and_panels` | queried `alembic_version` directly |
| `authoring` tables | all 7, including `panel_runs` / `panel_ratings` | queried `information_schema` |
| Deploy | push to `main` in either repo | both pipelines green at the SHAs above |

**The FQDN that answers `/api/version` is `direct-sim-beta`, not `direct-sim`.** The deploy
workflow updates both apps; `direct-sim` is the legacy Streamlit one and returns its index HTML
for every path, so a version check against it looks like a broken endpoint rather than the wrong
app. Full host:
`direct-sim-beta.yellowmushroom-f2e62d1e.eastus.azurecontainerapps.io`

### Shipped 2026-07-30 — `ADR-019`

`case-gen-entropy 663e0c3`, `direct-sim 87b15d3`. Confirmed live in production: `POST
.../adopt` and `POST .../copy` answer `401` unauthenticated (route present, auth enforced) where
an unknown path answers `404`, and a legacy case's Oracle error now names the adoption route.

What `ADR-019` changed, in one paragraph: saving an edited case used to overwrite the simulator
row and write no version, and separately, 102 of the 103 existing cases had no `case_version` at
all, so they could not carry Final Orders or an Oracle panel and `/resync` could not help them.
Saving now defaults to a new version with lineage and re-reads edited content into the structured
record; `POST /sim-ready/case/{id}/adopt` gives a legacy case its first version, reconstructed from
its markdown. Read `ADR-019` before touching the save path.

**Verified end to end** against the production schema, using temporary rows on the throwaway
`tester` case (id 5) that were removed afterwards — the database was confirmed back at 103 cases,
1 family, 1 version: adoption reconstructs a 16-field record with a correct door chart and HPI;
Final Orders attach where they previously 404'd; the leak audit runs over 9 real terms instead of
passing vacuously on 0; new-version saves carry lineage and Final Orders forward; a whitespace-only
edit correctly spends no model call; a declined re-read is honestly marked detached; both preflight
and the runner block on a blank diagnosis.

**Not verified:** the Oracle panel has still never run on a real case. That is 15 calls per Final
Order and it is gated on the stem decision, not on code.

### The immediate next actions, in order

1. **Run one Oracle panel end to end.** It has still never happened on a real case, and it is the
   last thing standing between here and sending `docs/email-draft-2026-07-30.md` — which invites
   Cory and Alex to run one. The OpenRouter key in Container Apps is proven non-empty (the backend
   would not start otherwise) but **not proven valid**: a revoked key passes startup and fails
   inside the model call, which is exactly what the local `.env` did on 2026-07-30. Sending first
   means asking them to be the ones who find out.
2. **Get the rating stem confirmed by the group** before that panel generates anything anyone
   keeps. Still the one decision that is expensive to change later: it invalidates every
   distribution generated under the old wording.
3. **Adopt the OSCE cases that are actually in use** and skim what the reconstruction produced.
4. Then the verification pass under "Deploy integrity" below.
5. **Dependabot**, surfaced by the 2026-07-30 push and not yet looked at: **65 advisories on
   direct-sim (42 high)** and **9 on case-gen (2 high)**. Separate from the secret-scanning work
   and worth clearing before students touch the simulator.

### Environment — changed 2026-07-30, will bite a fresh session

- **`OPENROUTER_API_KEY` now comes from `.env` only.** Two shell exports used to shadow it and
  both were broken: `~/.zshenv` had a revoked key, and `~/.zshrc` read a Keychain item that does
  not exist and so exported an empty string. Both were removed (backups: `~/.zsh{env,rc}.bak-2026-07-30`).
- **`load_dotenv()` does not override an already-set variable, and treats empty-string as set.**
  A process launched from a terminal that predates the fix inherits `OPENROUTER_API_KEY=""` and
  passes it to every child, so `.env` is ignored and the backend raises at startup. **Restart the
  terminal.** To check: `python3 -c "import os; print(repr(os.environ.get('OPENROUTER_API_KEY')))"`
  — `''` is the poisoned state, `None` is correct.
- **Secret scanning is now in both repos, by two different mechanisms.** `brew install gitleaks`
  is required either way. In **case-gen** it is the pre-commit framework
  (`uvx pre-commit install`), using `gitleaks-system` because the default hook builds from source
  and pre-commit hardcodes `GOTOOLCHAIN=local`. In **direct-sim** it is that repo's own
  `.githooks/pre-commit`, which already covered ruff, gitleaks, large files, `uv.lock`, and
  `pip-audit` but had never been activated — `git config core.hooksPath .githooks` is now set in
  this clone and is needed again in any fresh one. Both were tested with a canary commit that they
  correctly rejected. Both repos' full history scans clean.
- **Both hooks now fail closed when the gitleaks binary is missing.** direct-sim's previously
  printed "skipped (gitleaks not installed)" and passed, so a machine without gitleaks would have
  committed secrets with every check reporting green. Fixed 2026-07-30 and tested in all three
  states: clean, secret staged, and binary absent. `brew install gitleaks` is therefore a hard
  prerequisite, and `.github/workflows/secret-scan.yml` remains the backstop that `--no-verify`
  cannot skip.

**Seven things that have bitten us, worth knowing before touching related code:**

1. `deploy-aca.sh redeploy` silently no-op'd for four months because it reused a mutable `:v1`
   tag. Fixed, and every image now carries a build stamp — check it after any deploy `ADR-012`
2. `case_details` JSON columns hold heterogeneous shapes. Never call `.get()` on them without
   coercing first `ADR-013`
3. The pre-push hook runs system `ruff 0.9.4`, not the newer `uvx` one. Check **both**
   `ruff check .` and `ruff format --check .` with the system binary before pushing.
4. **Never put `git push` in the same Bash call as `git add`/`git commit`.** The pre-push hook
   matches `git push` at any line start and blocks the *entire* call, so the commit silently
   does not run.
5. In `frontend/app.py`, **`st.rerun()` resets the active tab** — `st.tabs` keeps its selection
   client-side and a server-forced rerun discards it. Use `on_click` callbacks for in-tab state
   mutations. Also: state derived once via `if "key" not in st.session_state` is never
   refreshed, which silently leaked one case's image links into another.
6. **Streamlit prefers a widget's stored value over its `value=` argument.** Clearing only the
   state key behind a keyed widget changes nothing on screen. `edit_oracle_specialty` did exactly
   this: the previous case's specialty stayed in the box, was read back on the next run, and was
   saved onto the newly loaded case, driving the wrong subspecialist seat on its Oracle roster.
   Fixed 2026-07-30 by putting the *widget* keys in `SIM_EDIT_KEYS`. Anything rendered with `key=`
   belongs in that list, not just the state key it feeds.
7. **A blank `primary_diagnosis` makes the leak audit report success having checked nothing** —
   `audit_leak` derives its terms from that field. Now blocking and deliberately not covered by
   the leak-audit override `ADR-019`

**Infrastructure note:** an older generation of this app (`backend-app`, `frontend-app`,
`redis-app` on `medical-case-env` in `casegen-rg`) was found live on Sept 2025 code, pointed at
the same production database. It is now scaled to zero with ingress disabled. Deletion commands
are in "Deferred / revisit" below.

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
- [x] **Version on edit** (2026-07-30) — `PUT /sim-ready/case/{id}` now writes a new `case_version`
      with `family_id` + `parent_version_id` by default, and re-reads edited content into the
      structured record so the save does not leave the Oracle blocked. `save_mode=in_place` keeps
      the old behaviour explicitly, and `POST .../copy` forks a new family `ADR-003` `ADR-019`
- [x] **Adoption for pre-authoring-record cases** (2026-07-30) — `POST /sim-ready/case/{id}/adopt`
      rebuilds the structured record from the case's markdown as v1 of a new family. Per case and
      author-initiated rather than a bulk backfill: 102 of 103 cases qualify, most of them test
      rows, and each adoption reconstructs content someone should read `ADR-019`
- [ ] **Adopt the cases that are actually in use.** The mechanism exists; the OSCE cases still need
      an author to run it and check what came back. Framework/LR data stays unavailable for them —
      it was never stored
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

**Built 2026-07-29.** Not yet applied to production or exercised end to end — see "Before the
first real run" below.

- [x] `case_final_orders` table `ADR-004` — `backend/models/database.py`, migration
      `0003_final_orders_and_panels` in `direct-sim`
- [x] Generation proposes 3–5 candidates from `diagnostic_workup`; author explicitly accepts.
      `POST /final-orders/propose` writes nothing
- [x] Authoring UI: order text, stem action with a live rendered preview, suppression synonyms,
      provenance, per-row delete
- [x] Edit support from the start — `PUT /sim-ready/case/{id}/final-orders`, replace semantics
- [x] Cap of 5 enforced in the Pydantic schema **and** re-checked before the write
- [x] `stem_action` column — deriving the gerund from the label gets activations wrong
      ("ordering a stroke team activation")

## Phase 3 — Oracle panel

**Built 2026-07-29.** Same caveat.

- [x] Shared panel runner: `panel_runs` + `panel_ratings`, `item_type` discriminator `ADR-006`
- [x] Responses API call path (`client.responses.parse`, `reasoning.effort`) — separate from the
      existing `gpt-4o` / `chat.completions.parse` path
- [x] Blinded Oracle view built from structured fields, failing closed `ADR-005`.
      `diagnostic_workup.rationale` is excluded: it is authoring reasoning, not patient data, and
      routinely names the diagnosis
- [x] Blocking leak audit against the diagnosis, its tokens, and a curated synonym list, with a
      **recorded override** for legitimate hits (family history of CVA in a stroke case) `ADR-014`
- [x] Background execution + status endpoint
- [x] Version-pinned staleness (computed from the context hash on read) + append-only re-runs
      via `superseded_by` `ADR-003`
- [x] **Entropy / discrimination display for case authors** — item-quality flags in
      `docs/llm-panels.md` §7
- [x] Panel only runs when the case has Final Orders `ADR-014`
- [x] Cost-of-commission weighting in the shared rater instruction `ADR-014`
- [x] Specialty seat generalised from otolaryngologist, bound per case `ADR-014`

### Before the first real run — blocking

- [x] **Migration 0003 applied to production** — confirmed 2026-07-30: `alembic_version` holds
      `0003_final_orders_and_panels`, all seven `authoring` tables exist, and `GET /` reports
      `final_orders: true`
- [ ] **Confirm the rating stem with the group.** Cory asked to see the change before adopting it.
      `GET /oracle/stems` renders both versions side by side. Changing it later invalidates every
      distribution generated before the change `ADR-014`
- [x] **`ORACLE_MODEL` verified.** The proposal's `gpt-5.6` does not exist; the 5.6 line ships as
      `-luna` / `-sol` / `-terra`. Now `openai/gpt-5.6-sol`, confirmed against OpenRouter's
      catalogue and exercised with a real 3-panelist run on 2026-07-30
- [x] **All LLM calls moved to OpenRouter** on a dedicated zero-retention key. The Oracle moved off
      the OpenAI-only Responses API to Chat Completions with `extra_body` reasoning, so one
      provider serves both paths `ADR-016`
- [x] **Oracle/learner content parity check** — blocks the panel when the structured record and the
      simulator's content have diverged
- [x] **`OPENROUTER_API_KEY` is set in Container Apps.** Inferred rather than read: `LLMService()`
      is constructed at import and `build_client()` raises on a missing key, so a backend that
      serves `GET /` has a non-empty one. **Non-empty is not the same as valid** — a revoked key
      passes startup and fails 60 seconds later inside a model call, which is exactly what the
      local `.env` did. Unproven until a panel actually runs
- [ ] End-to-end pass: author a case with 2 Final Orders, run the panel, confirm 15 ratings land,
      the histogram renders, and the flags are sensible
- [ ] Confirm the background task survives an Azure Container Apps replica for the full 3–5
      minutes. `BackgroundTasks` runs in-process, so a scale-in mid-run leaves a run stuck in
      `running` with no reaper

## Phase 4 — Editing model

**Raised 2026-07-28 while fixing a UI bug; do these before, or as part of, the rest of Phase 4.**
The trigger was "Add Link jumps tabs", but investigating it exposed that both editing workflows
(new draft, loaded existing case) share one `st.session_state` slot with no ownership boundary.
That is what let one case's image links leak into another.

- [ ] **Guard the draft-clobber path.** Loading an existing case overwrites an unsaved draft
      silently. Same class of bug as the image-link leak, still present
- [x] **Make save semantics explicit** (2026-07-30). The three actions are now on the screen and
      named for what they do: *Save as New Version* (default), *Save as New Case*, *Overwrite
      Case*. Each states its consequence, including that overwriting detaches the Oracle
      `ADR-003` `ADR-019`
- [ ] **Then** revisit whether new-case and existing-case editing want separate tabs. Deferred
      deliberately: more tabs alone would not fix the shared-state problem, and splitting would
      duplicate ~600 lines of editor UI that would drift. Settle the semantics first — the tab
      question mostly dissolves
- [ ] Audit remaining session-state keys derived via `if "key" not in st.session_state` for the
      same never-refreshed defect that hit `sim_image_links`


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

Answered 2026-07-29 by Cory (comments on `Status_and_Decisions_Needed`) — full text in
`Decisions.md` ADR-014:

- [x] Reasoning effort `medium` — confirmed, "we don't need highest reasoning possible for this"
- [x] 15-role roster approved — but **for the Final Order SCT only**, never for learner rating,
      and never for a case without Final Orders
- [x] Stewardship / risk-averse pair — include, with runs independent and cost of commission
      weighted
- [x] Keep the name "Final Orders" — "Key Management Decisions" collides with the simulator's
      separate 3-next-steps box
- [x] Specialty seat generalised from otolaryngologist (David, same day)
- [ ] **Rating stem — conditional.** "If something is being suggested to change what is currently
      in place, then I would want to see those changes first." Both versions are implemented and
      switchable; send the side-by-side from `GET /oracle/stems` and get a yes or no **before any
      production panel runs**
- [x] **Split the panel across two model families — yes, done** (David, 2026-07-30). Applied only
      where personas are similar; seats 13/14 deliberately stay on one model so the
      stewardship-vs-risk-averse contrast is not confounded `ADR-018`
- [ ] **Test the split on a genuinely debatable item.** Verified mechanically, but the test item
      (central HINTS pattern, brain MRI) is clinically unambiguous — all five seats correctly said
      +2. Wording diverged across families and stayed homogeneous within them, so whether the split
      adds *rating* variance is still unknown `ADR-018`
- [ ] Schedule the human validation panel? Still unanswered; default propose alongside the spring
      review

## Simulator (direct-sim) — from the 2026-07-29 review

Tracked in full in `../direct-sim/FINAL_ORDERS_TODO.md`.

- [x] **Unique ID on both downloads — already correct.** The transcript PDF is generated first, the
      backend mints the code, and the assessment PDF is generated with that same code. Both PDFs
      carry "Submission Code: <code>" and both DB rows store it. Verified by reading
      `AssessmentPanel.tsx:83-162` and `backend/main.py:477-566`; not yet re-verified in the
      running app
- [x] **Advancing past the interview without retrieving the voice transcript — already gated.**
      `SimulationPage.tsx:66-69` blocks the phase-1 advance until a transcript exists, with
      voice-specific copy telling the learner to End then Retrieve
- [ ] **A case with `allow_orders = false` cannot leave phase 1.** The advance button lives inside
      `{activeTab === 'orders' && ordersAllowed && ...}` (`SimulationPage.tsx:166-221`), and the tab
      bar is also hidden when orders are off — so there is no affordance at all. Found while
      checking the item above. Confirm how many production cases have `allow_orders = false` before
      judging severity
- [ ] **Partial voice transcripts.** The gate checks that a transcript is non-empty, not that it is
      current. Retrieve early, keep talking, advance — and the later turns are silently missing.
      The copy says "retrieve again when fully done"; nothing enforces it. Options: warn when the
      call has been active since the last retrieval, or auto-retrieve on advance
- [ ] `MODEL_OPTIONS` is stale and excludes the `assessment` default (`anthropic/claude-opus-4.6`),
      so the admin dropdown cannot represent the value in force `ADR-015`
- [ ] Record the resolved model on `transcripts` and `assessments`, the way `panel_runs` does
      `ADR-015`

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

---

## Deferred / revisit

Parked deliberately, with the reasoning, so a later session can pick them up rather than
rediscover them.

### Infrastructure cleanup — after a week of stability

The old generation is scaled to zero with ingress disabled (2026-07-28), not deleted, so it
stays trivially reversible. Once satisfied nothing depended on it:

```bash
az containerapp delete -n backend-app  -g casegen-rg --yes
az containerapp delete -n frontend-app -g casegen-rg --yes
az containerapp delete -n redis-app    -g casegen-rg --yes
az containerapp env delete -n medical-case-env -g casegen-rg --yes
az group delete --name medical-case-generator-rg --yes   # stopped ACI + medcasegen5986 ACR
```

Then `medical-case-env-logs` once the env is gone. **Do not prune `labdlcontainer`** — it is a
Standard registry in `lab-simulation` and may serve other projects.

- [ ] Delete the idled old generation (above)
- [ ] Revert if anything breaks: `--min-replicas 1` + `az containerapp ingress enable`

### direct-sim dependency advisories

- [ ] **25 high / 5 medium open Dependabot alerts**, no PRs open. The `react-router` cluster
      includes an unauthenticated RCE advisory reachable from a page students load.
      `npm audit fix` touches 34 packages with no major bumps. Deferred because broad dependency
      upgrades are not a default action — needs a deliberate call, ideally the targeted
      `react-router-dom` bump first, then `npm run build` and an SPA smoke test.
- [ ] A formatting-only commit (`542c08e`) is committed but unpushed in `direct-sim`, blocked
      behind the same audit gate.

### Security posture

- [ ] **`APP_PASSWORD` is a publicly known default.** Accepted 2026-07-28 on the grounds that
      UNMC uses it broadly. Recorded as a rotation candidate, not a blocker: it is the only gate
      on a generator that writes to the shared production database. Rotating means a Container
      Apps secret update plus a `.env` change.

### Carried over from the main merge

- [ ] `tier_level` on the beta `feature_likelihood_ratios` table — `main` added it via a runtime
      `ALTER TABLE` in `_ensure_schema()`, deliberately not ported (runtime DDL conflicts with
      Alembic ownership, `ADR-012`). The column may already exist from a previous boot. Check,
      then handle it when the beta tables migrate into `authoring` `ADR-001`
