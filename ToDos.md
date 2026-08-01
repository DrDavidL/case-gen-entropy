# ToDos

Sequenced work for the OSCE/SCT build. Decisions behind these items are in `Decisions.md`;
specifications are in `docs/` (see `docs/README.md` for the map).

Simulator-side work is tracked separately in `../direct-sim/FINAL_ORDERS_TODO.md`.

---

## Start here — state as of 2026-07-31

**Phases 1 through 3 are live. Phase 4a and 4b are live. `ADR-020` (React SPA) is the active
workstream. **Phase 4c is built and deployed** — all four blockers plus the editor. What it has
not had is a human opening it in a browser.**

| | | Verified how |
|---|---|---|
| case-gen live build | `18d426f` | `GET /` on the backend |
| direct-sim live build | `389af1c` | `GET /api/version` on **`direct-sim-beta`** |
| Shared DB revision | `0004_panel_run_item_snapshot` | `alembic current` + queried `alembic_version` |
| `authoring` tables | all 7, including `panel_runs` / `panel_ratings` | queried `information_schema` |
| Deploy | push to `main` in either repo | both pipelines green at the SHAs above |

### Open right now

- **`direct-sim` PR #13 merged 2026-07-31** (urllib3, eslint 10, postcss, react-router 8, CI
  permission fix). Its shipped-dependency audit is now **zero**. The merge broke the image build
  and was repaired in `114320c`; see failure 4 below before touching that lockfile
- **The Python dependency backlog is cleared** (`389af1c`, deployed and SHA-verified). Details
  under "direct-sim dependency advisories"
- **The Oracle panel is verified through the production backend**, which retires the last
  blocker on `docs/email-draft-2026-07-30.md`. Only the stem confirmation remains
- **No students are using either app.** That is why the eslint major and the router migration
  were done now. It is a window for disruptive work, and it expires
- A scheduled routine (`trig_01A2K4CeFtRpMddWatMEP4Ct`) fires 2026-08-04 for the react-router
  bump. **Now redundant** — PR #13 does it. It no-ops safely, but disable it at
  <https://claude.ai/code/routines>

### Seven failures found on 2026-07-31, all the same shape

Each is a check that **ran, reported success, and verified the wrong thing.** Same family as
`ADR-012`'s mutable image tag, which let a four-month-old image serve behind green deploys. When
touching any gate, prove it fails on a deliberate defect before trusting that it passes:

1. **`npx tsc --noEmit` in the pre-push hook checked zero files.** Both repos use solution-style
   `tsconfig.json` (`"files": []` plus `references`), where `--noEmit` typechecks nothing and
   exits 0. A planted type error passed it. Fixed to `tsc -b --force`
2. **`direct-sim`'s secret scan had never run on a pull request.** `permissions: contents: read`
   is one scope short of the `pull-requests: read` that gitleaks-action needs on a
   `pull_request` event; it 403'd before scanning anything. Push events passed, no PRs had been
   opened, so nobody saw it
3. **Generated TypeScript types described nothing** for endpoints without a `response_model`
4. **A clean local `npm ci` proved nothing, and broke the deploy.** `frontend/package.json`
   declares `"packageManager": "npm@10.9.8"`, which is what `node:22-slim` ships; the lockfile
   was generated under npm 11.6.2. The two resolve optional wasm transitives differently, npm 11
   elided two `@emnapi` entries npm 10 requires, and the production build failed on
   `npm ci`. Every local gate had passed because every one ran under the wrong npm.

   **Use the npm the image uses**: `npx npm@10.9.8 install`, and confirm `npm ci` passes under
   both. Also check `.github/workflows/deploy.yml` for which Dockerfile is actually built — the
   node 20 → 22 fix went into `Dockerfile.react`, but the deploy builds `Dockerfile.combined`,
   so it was inert.
5. **`direct-sim`'s `.githooks/pre-commit` still fails *open* on `pip-audit`.** Seen live while
   committing the dependency bumps on 2026-07-31: it printed
   `pip-audit... skipped (pip-audit not installed)` and passed. This is the identical defect that
   was fixed for `gitleaks` in the very same hook on 2026-07-30 — the step directly above it — so
   the file now contains one fail-closed check and one fail-open one. A machine without the binary
   commits a vulnerable lockfile with every check reporting green.

   **The fix is not simply installing pip-audit.** Do that alone and the gate becomes permanently
   red, because `weasyprint` has an advisory with **no patched release**, and a gate that must be
   overridden to do ordinary work stops being a gate — the same reasoning already written into the
   npm section of `~/.claude/hooks/pre-push-checks.sh`. Fail closed on a *missing binary*, and
   carry an explicit, commented `--ignore-vuln` for advisories with no available fix.

6. **`web/src/lib/api.ts` was never committed.** `.gitignore` carried a stock Python `lib/` rule.
   Unanchored, that matches at *any* depth, so it swallowed all of `web/src/lib/` — including
   `api.ts`, the SPA's entire backend client. Phase 4b therefore shipped an SPA that cannot build
   from a fresh clone: `App.tsx` → pages → `./lib/api`, a module not in the repo. It worked
   locally only because the file exists in the working tree.

   Proved by extracting `HEAD` to a temp dir and listing `web/src/lib/` — empty. No production
   impact, because nothing builds `web/` until Phase 4e, but any collaborator cloning this repo
   had a broken SPA and 4e would have hit it. Fixed in `9348268`: `/lib/` and `/lib64/` anchored
   to the repo root (`dist/` stays unanchored on purpose so `web/dist/` remains ignored),
   `types.gen.ts` ignored explicitly as a generated artifact, and `build` now runs `gen:types`
   first so a clean checkout generates types instead of failing on a missing module.
   **Verified by actually cloning the repo and building it**, not by simulating.

7. **The committed `openapi.json` described an API the backend does not serve** — and the gate
   written to catch that class of problem was itself hollow on the first try. Two parts:

   - `scripts/dump_openapi.py` imports the app, which runs `Base.metadata.create_all()` and probes
     the authoring schema at module scope. With no database the dump **died, wrote nothing, and
     left the committed file untouched** — so a "is the schema current?" diff came back clean. A
     deliberately planted stale schema passed. Fixed by neutralising `create_all` and the probes
     inside the dump script, not by making production code conditional on a build-only env var.
   - Once the gate actually ran, it failed immediately and correctly. The schema had been generated
     with `uv run --with fastapi --with pydantic`, which resolves the **newest** releases, while
     `requirements.txt` pins fastapi 0.104.1 / pydantic 2.5.0 — what the production image installs.
     The generated JSON Schema is version-sensitive (2.5.0 wraps `$ref`s in `allOf` and omits
     `additionalProperties`; newer adds `ctx`/`input` to `ValidationError`). Every TypeScript type
     was therefore generated from a description of a different API version.

   **Generate against `requirements.txt`, always.** An ad-hoc `--with` silently reintroduces it.
   Field names happened to match this time, so nothing broke at runtime — which is exactly why it
   would have gone on indefinitely.

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

**Now verified** — see "Oracle: verified in production" immediately below.

### Oracle: verified in production, 2026-07-31

**The panel runs end to end through the deployed Azure backend.** `POST
/sim-ready/case/106/oracle/run` against `casegen-backend` returned `queued`, and
`GET .../oracle` reported `complete` with **15/15 ratings realized in 10.9 s**, all `status=ok`,
via OpenRouter on `openai/gpt-5.6-sol` (12 seats) + `anthropic/claude-sonnet-5` (3 seats).

**This retires the `OPENROUTER_API_KEY` doubt.** The key in Container Apps was previously only
inferred non-empty from the fact that the backend starts; it is now proven *valid*, because 15
real model calls returned content. That was the last unproven blocker on sending
`docs/email-draft-2026-07-30.md`.

Also confirmed by the same run: append-only lineage works (run 1 now carries `superseded_by=2`,
nothing overwritten, 30 `panel_ratings` rows across the two runs), the leak audit and
`check_content_parity()` both pass through the production path, and `BackgroundTasks` survived
comfortably — though at 8 s per item the 3–5 minute replica-scale-in worry is still untested at
5 Final Orders.

**An earlier run already existed** (run 1, 04:17 UTC the same day, also 15/15). Nothing records
whether it went through Azure or a local backend, because `panel_runs` carries no build stamp —
which is exactly the open "record the generator build" item under Deploy integrity. Run 2 was
executed deliberately against the production URL to remove that ambiguity.

**What this does *not* establish.** Both runs rated a single degenerate item: "CT scan of the
abdomen" on a STEMI case. All 15 seats said `-2`; entropy 0.0, SD 0.0, modal proportion 1.0, and
the aggregate auto-flagged `low_discrimination`. That is the **same weakness already recorded for
`ADR-018`** — the previous test item was unambiguous in the opposite direction — so whether the
two-model split adds *rating* variance remains unknown. The machinery is proven; the measurement
is not.

### The immediate next actions, in order

1. **Get the rating stem confirmed by the group — preliminary yes, not yet final.** As of
   2026-07-31 the research group's preliminary word is to **keep our version** (`v2_revised`, which
   is already the `ORACLE_STEM_VERSION` default), with a firm answer to follow.

   **Treat that as not-yet-decided for anything you intend to keep.** The stem is the measurement
   instrument: every distribution generated under it is invalidated if the wording changes, so a
   preliminary yes is enough to keep building and testing, and not enough to start accumulating
   research data. Runs already stamp `stem_version`, so exploratory runs made now are
   identifiable and discardable if the answer moves.
2. **Run a panel on a genuinely debatable Final Order.** Both existing runs are unanimous, so the
   `ADR-018` question is still open. Do it on a disposable case: adding an order to an existing one
   destroys prior runs' referent — see "Editing Final Orders orphans completed panel runs" below.
3. **Adopt the OSCE cases that are actually in use** and skim what the reconstruction produced.
4. Then the verification pass under "Deploy integrity" below.

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

### Editing Final Orders orphaned completed panel runs — fixed 2026-07-31

- [x] **Fixed** in `case-gen-entropy efe063f` + `direct-sim 67ca661` (migration
      `0004_panel_run_item_snapshot`, applied to production and verified).

      The defect: `replace_final_orders()` deleted every row and re-inserted with fresh ids, and
      `panel_runs.item_ref_id` is deliberately not a foreign key (it points into a different table
      per `item_type`). So adding a second Final Order to a case whose first one had been rated
      silently detached the completed run — no error, nothing logged, the distribution just stopped
      appearing. And nothing recorded what had been rated, so it was unrecoverable: only
      `claim_hash` survived, and a hash cannot say what the panel was asked.

      Two changes, because there were two distinct problems:

      - **Rows reconcile by identity now, rather than delete-and-reinsert.** Identity is the order
        text, whitespace- and case-insensitive. The text is the claim; `stem_action` and the
        suppression fields are how it is phrased and matched. So add, reorder, retype, and
        synonym-edit all preserve ids and keep ratings attached. **Changing the text still yields a
        new id, deliberately** — it is a different claim and must not inherit the old distribution.
      - **Runs carry `item_label` + `item_snapshot`**, including the rendered stem. `order_text`
        alone would still not say what the panel was asked, because the stem is the instrument.
        Gated behind `panel_run_snapshot_ready()` so a deploy that lands ahead of the migration
        degrades to null snapshots instead of failing every run on an unknown column.

      Deleting a rated order is still allowed — the author's list is authoritative — but it is now
      **reported**: `PUT .../final-orders` returns `detached_panel_runs`, and the store logs a
      warning naming the orders and run counts. **Phase 4d's Final Orders editor must surface that
      field**; it is the whole point of returning it.

      Verified against the production schema with disposable rows: 24 assertions, counts back to
      baseline afterwards. The two pre-existing runs were backfilled by the migration and now read
      `item_label = "CT scan of the abdomen"`.

- [ ] `PUT /sim-ready/case/{id}/final-orders` has **no `response_model`**, so `detached_panel_runs`
      is invisible to the generated TypeScript. Declare one before Phase 4d builds against it —
      this is the same gap that made all four SPA endpoints emit `{}`

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
- [x] **`OPENROUTER_API_KEY` in Container Apps is proven valid** (2026-07-31), not merely
      non-empty. Startup only established the latter, and a revoked key passes startup and fails
      inside the model call — which is exactly what the local `.env` did. Settled by running the
      panel through the deployed backend: 15 real calls returned content
- [x] End-to-end pass through the production URL: 15/15 ratings landed, aggregates computed
      (histogram, entropy, SCT credit, transparency rate), and the item-quality flag fired
      correctly. **One Final Order, not two**, and the item was unanimous — see the caveat under
      "Oracle: verified in production" above
- [ ] Still open: run the panel on a **debatable** item, and on more than one Final Order at once
- [ ] Confirm the background task survives an Azure Container Apps replica for the full 3–5
      minutes. `BackgroundTasks` runs in-process, so a scale-in mid-run leaves a run stuck in
      `running` with no reaper. **Not yet exercised** — a 1-item run finishes in ~8 s, nowhere near
      the window where scale-in matters

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

### The editor is a React SPA — sequenced plan `ADR-020`

Planned 2026-07-31. Read `ADR-020` for why this is not Streamlit.

**Phase 4a — backend, no UI. Built 2026-07-31, not yet deployed.** The canonical record was
write-only from outside the process, so no editor could exist in any framework until these
landed. All of it is usable from the current Streamlit UI, so 4a stands on its own.

- [x] `GET /sim-ready/case/{id}/structured` — returns `content_structured` plus `parity_broken` /
      `parity_reason`, so an editor can show parity state continuously instead of the author
      discovering it when the Oracle refuses to run
- [x] `PUT /sim-ready/case/{id}/structured` — the `ADR-002` inversion. Renders **before** any
      write, so a renderer failure is a 422 and never leaves the simulator row disagreeing with
      the structured record. Always a new version; no `in_place` mode, because a structured edit
      is by definition a change to the canonical record. Costs no model call, and clears
      `render_detached` by construction
- [x] Export tab reads `/analysis` and `/structured`, falling back to session state and saying
      which copy it used. Was a live bug: the data has been in Postgres since `ADR-001`
- [x] `POST /oracle/render-items` replaces the client-side mirror. **The mirror had already
      drifted** — it rendered `A-fib protocol` as "ordering *a* a-fib protocol" where the real
      `default_action_phrase` gives "ordering a-fib protocol", so authors were reviewing a
      sentence learners would not see
- [x] `extract_door_chart_section()` deleted (defined, never called)
- [x] **Write path exercised end to end** (2026-07-31) on a disposable case created and deleted
      by the test: 26 assertions covering version lineage, framework/LR/Final Orders carry-forward,
      `case_details.content` matching `content_rendered` byte for byte, the Door Chart delimiter
      surviving, idempotent content on re-save, and parity repair on a detached version. Residual
      rows verified zero afterwards. Reads, 401, 422, and 404 verified separately
- [ ] **`PUT /structured` and `POST /resync` both repair parity, but one destroys work.** The
      test confirmed a structured save overwrites hand-edited markdown: the edit is gone, silently
      and by design, because the record is canonical (`ADR-002`). `/resync` does the opposite —
      it folds the hand edits *into* the record with a model call. Both are correct operations and
      they are not interchangeable, so the editor must never present them as one button, and the
      structured save needs a confirmation when `render_detached` is true. **Build this in 4c
      before the editor ships**, not after an author loses an evening's markdown
- [ ] JWT login endpoint. **Do not remove HTTP Basic when adding it** — the Streamlit UI
      authenticates with Basic and stays live until 4e, so the two must coexist through the
      transition. `ADR-020` says auth "moves" to JWT; the move completes at cutover, not here

**Phase 4b — SPA shell. Built and deployed 2026-07-31 (`60dc587`).** Lives in **`web/`**, not
`frontend/` — `frontend/` is still Streamlit and stays until 4e. Vite, React 19, Tailwind 4,
**react-router 8**, eslint 10.

- [x] Scaffold, with `openapi-typescript` generating `web/src/lib/types.gen.ts` from a committed
      `web/openapi.json` `ADR-020`
- [x] Case list, read-only case view, build footer
- [x] `scripts/dump_openapi.py` regenerates the schema. **Run it after changing any request or
      response model**, then `cd web && npm run gen:types`
- [ ] CI should run the dump and fail if the result is dirty, so a model change cannot land
      without its schema. Not wired up yet

Two things a fresh session needs to know here:

- **Generated types are only as good as the `response_model` declarations.** Every endpoint the
  SPA needed originally returned a bare dict, so the schema described them as empty objects and
  the generator emitted `{}` — protection that looked real and checked nothing. Four response
  models were added to fix it. **A new endpoint the SPA consumes needs a declared
  `response_model` or it silently repeats this.**
- **No auth in the SPA yet, deliberately.** Every endpoint it touches is unauthenticated. JWT
  arrives in 4c with the editor, which is the first thing that needs it.

**Phase 4c — the structured editor. This is the release.** Sequenced 2026-07-31.

**Release shape, decided:** ship 4c as a real authoring surface **alongside Streamlit**, which
keeps Generate / Export / Final Orders / Oracle. They already coexist and the backend serves both.
Full cutover (4d + 4e) waits. Rationale: structured editing is the `ADR-002` win and is worth
having in Cory's and Alex's hands now; replicating Streamlit's other 2,333 lines first would
delay it for screens that already work.

**Where `web/` actually stands:** 424 lines of hand-written app code — `App.tsx`, `api.ts`,
`CaseListPage`, `CaseViewPage`, `BuildFooter` — plus 2,753 generated. Two read-only pages, no
forms, no auth. The structured record is **49 scalar fields across 10 groups** (9 top-level plus
nested `door_chart.vital_signs`) and 3 dynamic lists.

*Blockers — none of these are the editor, and all of them gate it:*

- [x] **Auth: login form sending `Authorization: Basic`** `ADR-021`, shipped `64ee481`, verified
      in production. `GET /auth/check` validates a credential without doing anything, so a bad
      password surfaces at login rather than by a save failing.

      It uses a **second** dependency, `verify_credentials_silent`: same constant-time check, no
      `WWW-Authenticate` header. That header is what makes a browser throw its own native
      credential dialog on a 401, which would fight the app's login form with a popup the user
      cannot style or sign out of. Deliberately not a change to `verify_credentials` — the
      challenge is correct for Streamlit and every direct API caller. Confirmed still intact:
      `PUT /edit-case` answers `401 WWW-Authenticate: Basic`.

      Credential lives in `sessionStorage`, not `localStorage`: it is a shared password rather
      than a revocable token, so it should not outlive the tab. **That is not an XSS boundary.**
      A 401 on an authenticated call clears it, so the app falls back to login instead of retrying
      a credential that cannot work
- ~~Rotate `APP_PASSWORD`~~ — **deliberately deferred 2026-07-31**, not a blocker. Rotating means
      a Container Apps secret update plus a coordinated backend + Streamlit restart, which locks
      out anyone holding the current value, so it waits until the whole research team is reachable.
      **The editor ships on the known-default password**: until rotation, treat the SPA URL as
      effectively public and keep it inside the research group. See "Security posture" `ADR-021`
- [x] **`POST /sim-ready/render-preview`** shipped `9348268`, verified in production: writes
      nothing, costs no model call, unauthenticated to match `/oracle/render-items`, declared with
      a `response_model`. **Not case-scoped on purpose** — the preview must reflect the author's
      unsaved buffer, so there is nothing to look up. Calls the same `render_sim_ready_content()`
      a save calls, so a preview is byte-for-byte what would be stored; confirmed against the
      live record for case 106.

      `DOOR_CHART_DELIMITER` is now a named constant in `sim_ready_transform` and interpolated
      into the template. It had been a bare literal in the renderer plus a second copy in
      `frontend/app.py`, for a string the simulator parses by. Proved byte-identical output
      against all three stored records before shipping
- [x] **The `/structured` vs `/resync` confirmation** shipped `64ee481`.
      `DetachedSaveConfirm` states the loss in terms of what disappears rather than flag names,
      and offers `/resync` — the opposite operation — **in the same dialog**, so an author who
      meant to keep the markdown reaches it instead of learning afterwards that it existed. Never
      one button. `ParityBanner` surfaces divergence continuously on the case view, so it is not
      discovered when the Oracle refuses to run.

      **Still to wire:** the dialog exists and is correct, but nothing calls it yet — the save
      path it guards is part of the editor below

*The editor itself — built `659d6ef`, deployed:*

- [x] `Field`/`FieldGroup` driven off a declarative spec in `web/src/lib/fieldSpec.ts`, whose keys
      are checked at compile time via `StringKeys<T>` over the generated types. **That check is the
      point**: a field renamed server-side becomes a type error instead of a blank input an author
      cannot distinguish from an empty value, saving with the real field untouched
- [x] Server-rendered preview pane, debounced 400 ms against `/sim-ready/render-preview`
- [x] Dynamic list rows keyed by a client-side stable id, stripped by `toApi` before the write.
      Index keys are the same family as the `sim_image_links` leak — remove a row and every row
      below shifts, so React reuses the wrong node and an author's cursor and in-flight edit land
      on a different row
- [x] `DetachedSaveConfirm` fires **only** on `render_detached`. `content_drift` also breaks parity,
      but a structured save is the correct repair for it with nothing to discard
- [x] `ParityBanner` on both the case view and the editor

      **Verified:** nine assertions on the row logic, including that deleting the first row leaves
      the second row's id and data intact; then a live round-trip proving the editor's exact payload
      is accepted and re-renders the stored markdown **byte for byte**, so opening a case and saving
      without edits is a no-op, while a real edit does change the render.

- [ ] **Not yet done: a human has not opened it in a browser.** Everything above is verified
      headlessly. Drive it once against a real case before telling anyone it is ready.

      **It is now reachable** (`58fcab6`):
      <https://casegen-backend.greenbush-b78bdd23.eastus.azurecontainerapps.io/app>

      Sign in top-right with `APP_USERNAME` / `APP_PASSWORD`, open a case, click *Edit
      fields*. Reads work signed out; only saving needs the credential. **Case 106 ("New
      tester") is the safe one to edit** — it is a throwaway with an authoring record.
      Most of the other 102 cases have no `case_version` and will say so rather than let
      you edit them `ADR-019`

*Cheap, missing, and independent of the above:*

- [x] **`.github/workflows/schema-check.yml`** regenerates the schema and fails if it differs from
      what is committed. Proven to fail before being trusted to pass — see failure 7 below
- [x] `PUT /sim-ready/case/{id}/final-orders` now declares `FinalOrdersUpdateResponse`, so
      `detached_panel_runs` reaches the generated types and the Phase 4d editor can surface it

**Phase 4d — remaining screens.** Generate form, Final Orders editor, Oracle view (histograms,
preflight, leak audit), Export. All against endpoints that already exist.

**Phase 4e — cutover.** The *serving* half is done (`58fcab6`); the retirement half is not, and
should not happen until the editor has actually been used.

- [x] FastAPI serves `web/dist` **at `/app`, not `/`**. `GET /` stays the build stamp every deploy
      is verified against — shadowing it with `index.html` would remove the only signal that says
      whether a revision rolled `ADR-012`. The prefix also means the catch-all cannot shadow an API
      route. Path containment is checked before any filesystem access
- [x] The node stage in `Dockerfile.backend` installs `npm@11.6.2` before `npm ci`, because
      `web/package.json` declares that packageManager and `node:22-slim` ships npm 10.x — the same
      mismatch that broke direct-sim's deploy. `node:22`, not 20, for react-router 8

- [ ] **Build the SPA on `node:22-slim` or newer, not `node:20-slim`.** `react-router@8.3.0`
      declares `engines.node >=22.22.0`. direct-sim hit exactly this: the router migration was
      written against a Dockerfile still on node 20, which would have shipped an image built on
      an unsupported runtime. Caught only because a second pass looked at the Dockerfile
- [ ] Retire the `casegen-frontend` container app. Its removal makes the frontend/backend
      build-stamp drift warning structurally impossible rather than monitored — see
      "Deploy integrity" below and `ADR-012`
- [ ] Delete `frontend/` (Streamlit) once `web/` reaches parity. **This also clears 11 open
      Dependabot alerts by itself** — GitPython is a Streamlit transitive and nothing else pulls
      it
- [ ] Remove HTTP Basic from the backend at this point, not before. It is what the Streamlit UI
      authenticates with, so the two auth paths coexist until Streamlit is gone `ADR-020`

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

- [x] **Pip backlog cleared 2026-07-31** — `direct-sim 389af1c`, deployed and verified live
      (`GET /api/version` reports `389af1c`, so the revision actually rolled).

      Nine targeted bumps, one `uv lock --upgrade-package <name>==<version>` each: Pillow
      12.2.0→12.3.0, GitPython 3.1.47→3.1.55, PyJWT 2.12.1→2.13.0, starlette 1.0.0→1.3.1, tornado
      6.5.5→6.5.7, soupsieve 2.8.3→2.8.4, idna 3.11→3.15, Mako 1.3.11→1.3.12, click 8.3.2→8.3.3.
      **42 of the 46 open alerts are addressed.** Zero packages added or removed, and this time
      weasyprint and uvicorn did not ride along.

      Four things worth carrying forward:

      - **Pin the exact version, do not bare-`--upgrade-package`.** `gitpython` 3.1.55 was exactly
        8 days old while 3.1.56/3.1.57 were inside the 7-day window; an unpinned upgrade would have
        taken them. Every target's PyPI upload date was checked, not assumed.
      - **`click` 8.3.3 (PYSEC-2026-2132) was found by pip-audit and is absent from Dependabot
        entirely.** The two scanners use different advisory databases, so neither enumeration is
        complete alone — the table this item used to contain was built from the alerts API and was
        short by one package plus a fifth starlette advisory.
      - **GitPython is no longer an argument for hurrying Phase 4e.** Its 11 alerts were written
        off here as "cleared for free when Streamlit goes"; one lockfile line cleared them today.
      - **weasyprint still has no patched release.** Recordable, not fixable, and it is what makes
        a naive `pip-audit` pre-commit gate permanently red — see failure 5 above.

      Remaining open after this: weasyprint, plus three npm dev-only alerts (`vite` ×2,
      `@babel/core`) that never reach a student.
- [ ] **Dependabot — the diagnosis in this file was wrong about which repo, corrected 2026-07-31.**
      There is an API that answers it directly, and it should have been the first thing checked:

      ```bash
      gh api repos/DrDavidL/direct-sim/automated-security-fixes        # {"enabled":true,"paused":false}
      gh api repos/DrDavidL/case-gen-entropy/automated-security-fixes  # {"enabled":true,"paused":true}
      ```

      **`direct-sim` is not paused. `case-gen-entropy` is.** The entry previously here asserted the
      opposite, reasoning from `direct-sim`'s PR history (11 PRs, 5 merged, 6 closed unmerged,
      stopped 2026-02-05). That ratio is consistent with a pause but does not establish one, and
      the flag says otherwise. Neither repo has a `.github/dependabot.yml`, so only *security*
      updates exist in either — which makes that one endpoint the whole picture, not part of it.

      This is the **third** wrong conclusion drawn about Dependabot here in one day. The lesson the
      previous two produced was "read the PR history, not the Actions view." That was still
      indirect evidence. Read the flag.

- [x] **The alert list did refresh, just slowly.** `direct-sim` went **46 → 4** open alerts after
      `389af1c`, exactly the 42 predicted. The four left are `weasyprint` (medium, **no patched
      version exists**) and three npm dev-only ones (`vite` high + moderate, `@babel/core` low),
      none of which reach a student.

      Recorded because it briefly looked otherwise: `Graph Update: uv in /.` jobs sit `queued` with
      zero steps for a long while (three did on 2026-07-31, and the last to reach a terminal state
      before that were two `cancelled` ones on 2026-04-28), which reads exactly like a stuck runner.
      **Give it time before concluding the graph is broken** — checking the count minutes after a
      push is too early, and that misreading was the fourth wrong Dependabot conclusion in a day.

- [ ] Delete the 9 stale `origin/dependabot/pip/*` branches left behind by closed PRs
      (`h11-0.16.0`, `pip-26.0`, `protobuf-5.29.5`, `protobuf-5.29.6`, `requests-2.32.4`,
      `tornado-6.5.1`, `urllib3-2.5.0`, `urllib3-2.6.3`, `weasyprint-68.0`). Harmless, but they
      make the remote branch list unreadable and two of them are superseded urllib3 bumps

      **This is upstream of the whole Aug 4 problem.** With Dependabot running, `react-router-dom`
      7.18.2 would have arrived as a PR on 2026-07-28, aged through review, and been mergeable on
      Aug 4 with no decision to make. The 7-day rule was never the friction; the paused bot was.
      Fix this before doing more manual bumps, or the backlog just rebuilds

#### The Aug 4 bump — investigated 2026-07-31, recipe below

The one-line version: **`npm audit --audit-level=high` cannot pass, and the gate is asking the
wrong question.** Production dependencies carry exactly two high advisories, both `react-router`.
Everything else is dev-only tooling that never reaches a student.

Verified by experiment on a scratch branch (reverted; the tree is clean):

- **`react-router-dom` 7.18.2 is the whole browser-facing fix.** `npm audit --omit=dev` reports
  `react-router` and `react-router-dom` and nothing else. Clears the 7-day rule on **2026-08-04**
- **`brace-expansion` cannot be overridden.** Forcing `5.0.8` makes eslint die with
  `TypeError: expand is not a function` — v5 changed its export shape and minimatch@3 calls it the
  old way. `tsc` and `build` still pass; only lint breaks, so this fails *quietly* if nobody runs
  lint. The real fix is **eslint 10**: `@eslint/config-array` only moves to `minimatch ^10.2.4`
  there. eslint 9.39.5 does bring `js-yaml` 4.3.0, which does fix that one advisory
- **Do not use `^8.0.16` for vite.** It resolves to 8.2.0, one day old, and swaps the bundler
  (rolldown rc.13 → 1.2.1 plus 13 new lightningcss binaries). `~8.0.16` keeps it in the 8.0 line.
  vite's own two advisories are dev-server-only and Windows-only, so they do not affect this
  deployment at all
- **vite 8.0.16 needs `@napi-rs/wasm-runtime` newer than the 7-day cutoff.** Pinning it back to
  1.1.6 produces a lockfile `npm install` accepts and **`npm ci` rejects**, which would break
  `Dockerfile.react`. 1.2.0 clears the rule on Aug 4, same day as react-router
- **`npm install --before=<date>` is the wrong tool here.** It re-resolves the entire tree to
  pre-cutoff versions and desyncs unrelated transitives. Use targeted versions instead
- **Overrides desync the lockfile on the first `npm install`.** Run `npm install` **twice**, then
  `npm ci`, or the clean-install CI step fails on `@emnapi/wasi-threads`

Recipe for Aug 4 or later, in `frontend/`:

```bash
npm install react-router-dom@7.18.2        # the only browser-facing fix
npm install                                # second pass: reconciles the lockfile
rm -rf node_modules && npm ci              # must succeed — Dockerfile.react uses it
npx tsc -b && npm run lint && npm run build
npm audit --omit=dev --audit-level=high    # must exit 0
```

Then a login-and-load smoke test against the built SPA before pushing.

- [x] **Pre-push gate scoped to `npm audit --omit=dev --audit-level=high`.** Verified in
      `~/.claude/hooks/pre-push-checks.sh` on 2026-07-31: it blocks on shipped dependencies only
      and prints a non-blocking WARN for dev-only advisories. The reasoning is written into the
      hook itself. That file also now runs `npx tsc -b --force`, so failure 1 above is fixed there
      too. **Both of these entries were stale — the work was already done.**
- [x] **eslint 10 migration** — `frontend/package.json` declares `eslint ^10.8.0`, with
      `minimatch 10.2.5` and `brace-expansion 5.0.8` overrides resolving the chain. Landed in PR #13
- [x] `postcss` override at `8.5.18`. Landed in PR #13
- [x] The formatting-only commit `542c08e` is on `origin/main`; nothing is unpushed in `direct-sim`

### Security posture

- [ ] **Rotate `APP_PASSWORD` once the research team is reachable.** A publicly known default, and
      the only gate on a generator that writes to the shared production database. Accepted
      2026-07-28 on the grounds that UNMC uses it broadly; **re-affirmed 2026-07-31 with the risk
      knowingly widened**, because the React editor ships on it `ADR-021`.

      Deferred for a real reason rather than inertia: rotation is a Container Apps secret update
      plus `.env`, and it needs a coordinated backend + Streamlit restart that locks out anyone
      still holding the old value. That is a team-coordination problem, not a code one.

      **Until then, the SPA URL is effectively public.** It is unauthenticated for reads today and
      will accept the default credential for writes. Keep it inside the research group, and do the
      rotation before it goes to anyone else. When rotating: update the Container Apps secret,
      update `.env`, restart `casegen-backend` and `casegen-frontend` together, and confirm the
      Streamlit UI can still save before telling anyone the new value.

### Carried over from the main merge

- [ ] `tier_level` on the beta `feature_likelihood_ratios` table — `main` added it via a runtime
      `ALTER TABLE` in `_ensure_schema()`, deliberately not ported (runtime DDL conflicts with
      Alembic ownership, `ADR-012`). The column may already exist from a previous boot. Check,
      then handle it when the beta tables migrate into `authoring` `ADR-001`
