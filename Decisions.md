# Decisions

Architectural decisions with rationale, so they are not re-litigated. Newest last.

**Status legend:** `ACCEPTED` — agreed, may not be built yet · `BUILT` — in the code today ·
`SUPERSEDED` — replaced, kept for history

> **Reading this as a fresh session?** Start with `docs/README.md` for the documentation map.
> `CLAUDE.md` describes the code **as it exists today**; this file describes **where it is going**.
> Where they disagree, this file wins and `CLAUDE.md` is stale.

---

## ADR-001 — One case, one record. Retire the Sim-Ready / Beta format toggle.

**Status:** ACCEPTED (2026-07-28) · Supersedes the dual-output-format design described in `CLAUDE.md`

**Context.** The generator offers an either/or `output_format` toggle — "Sim-Ready (Simulator
Compatible)" or "Beta (Full LR Schema)" — that forks the pipeline at preview, session, and
finalize, and writes to two different databases. Beta writes `cases` /
`diagnostic_frameworks` / `feature_likelihood_ratios`; Sim-Ready writes `case_details`.

Two problems. First, the sim-ready path **generates the diagnostic framework and full LR matrix
and then discards them** — they survive only in `st.session_state` and are lost on refresh
(`backend/app/main.py` finalize, sim-ready branch). Every sim-ready case has already paid for the
analysis and thrown it away. Second, the two destinations are separate Neon projects, so the LR
tables and `case_details` **cannot be joined at all**.

**Decision.** Every case is one case with one canonical record. Framework and LR data are
persisted for every case regardless of how it is rendered. The authoring tables move into the
shared database (own schema) so they can be joined to `case_details`. `output_format` degrades to
a rendering/export preference and then disappears from the UI.

**Consequences.** Requires a one-time migration of the beta tables into the shared database.
Unblocks LR transparency/editing, Final Orders, Oracle distributions, and per-case performance
tracking — none of which work while the LR data is unjoinable. The simulator is unaffected: it
keeps reading `case_details` exactly as today.

**See:** `docs/architecture-target.md`

---

## ADR-002 — The structured record is canonical; rendered markdown is derived.

**Status:** ACCEPTED (2026-07-28)

**Context.** For sim-ready cases, `case_details.content` — a markdown blob — is the source of
truth, and the Edit tab lets authors hand-edit it. Once edited, the structured data and the
rendered content diverge permanently. `CaseSaveRequest.rendered_content` overrides the renderer
with no way back.

This blocks three stated goals: save-as-new with variables changed, re-rendering after a template
change, and any regeneration. If a patient's age exists only inside a markdown string, "same case
but 78 years old" is a manual rewrite of every mention.

**Decision.** Authors edit **structured fields**; markdown renders from them. A raw-markdown
escape hatch remains, but is stored as an explicit override with a flag so a detached case is
identifiable. `case_details.content` becomes a projection of the canonical record, written on
save.

**Consequences.** The Edit tab moves from split-markdown editing to field editing, which is more
UI work but makes cloning and re-rendering nearly free. Cases already detached by hand-editing
stay detached; they are not migrated backward.

**See:** `docs/architecture-target.md`

---

## ADR-003 — Cases have a stable family identity and immutable published versions.

**Status:** ACCEPTED (2026-07-28)

**Context.** Two goals conflict without versioning: authors want to edit and resave cases, and the
program wants to track student performance per case. If a case is edited after thirty students
have run it, pre- and post-edit performance are not comparable and **nothing in the schema
records that it happened**.

**Decision.** Introduce `case_family_id` (the stable concept, e.g. "Dizziness — posterior
circulation stroke") and an immutable `version` per published snapshot. Learner runs and Oracle
runs reference a specific version, never a family.

**Consequences.** Cheap now, unrecoverable later — which version a student saw cannot be
reconstructed after the fact. Also subsumes Oracle staleness (ADR-005): a version pin invalidates
stale panels with the same mechanism.

**See:** `docs/architecture-target.md`

---

## ADR-004 — Final Orders live in their own table, not a JSON column.

**Status:** ACCEPTED (2026-07-28)

**Context.** Cory asked for up to ~5 author-chosen clinical actions per case whose appropriateness
learners rate, persisted as a foundational case attribute rather than an admin setting. Options
were a JSON column on `case_details`, or a new table.

**Decision.** New table `case_final_orders`, FK to the case. Purely additive — no `ALTER` on a
table holding live simulator data — and "this case has no Final Orders" is unambiguously zero
rows rather than the `null` / `{}` / `[]` ambiguity of a JSON field. Note that
`SimReadyBase.metadata.create_all(checkfirst=True)` would silently *not* add a column to the
existing table anyway.

**See:** `docs/final-orders-sct.md`

---

## ADR-005 — The Oracle is a model-derived reference distribution, not an expert consensus.

**Status:** ACCEPTED (2026-07-28), methodology pending research-group review

**Context.** Each Final Order gets a reference distribution from ~15 blinded LLM calls. Fifteen
calls to one model with one prompt are fifteen draws from a single posterior, which is
over-concentrated relative to genuine clinical disagreement — reported as "wisdom of the crowd"
it would overstate consensus.

**Decision.** Induce variance through a fixed, version-controlled roster of clinician personas,
the way real SCT panels derive value from heterogeneity. Label the output a *model-derived
reference distribution* in every artifact — UI, database, and any manuscript. Treat
model-versus-human agreement as a study to run, not an assumption.

**Consequences.** Persona prompting yields *simulated* heterogeneity; this is a stated limitation,
not a solved problem. A small human expert panel on 2–3 cases is the recommended validation.

**See:** `docs/llm-panels.md`

---

## ADR-006 — One LLM panel subsystem serves both the Oracle and LR re-assessment.

**Status:** ACCEPTED (2026-07-28)

**Context.** Two separate features were requested: Oracle appropriateness distributions for Final
Orders, and LLM re-assessment of generated likelihood ratios for accuracy.

**Decision.** They are the same shape — take a claim, send it to N independent blinded raters,
store the distribution plus reasoning, surface disagreement to the author. Build one runner and
one set of tables with an item-type discriminator, not two subsystems.

**See:** `docs/llm-panels.md`

---

## ADR-007 — LR re-assessment seeks citation grounding where it exists.

**Status:** ACCEPTED (2026-07-28)

**Context.** LLM-generated likelihood ratios are the weakest scientific link in the system. A
re-assessment loop where two models agree on a fabricated number improves internal consistency,
not external validity.

**Decision.** Re-assessment returns a *range* with a stated basis rather than a point estimate,
and attempts literature grounding via PubMed. Many LRs will have no published source; that is an
expected and acceptable outcome, recorded honestly rather than papered over.

Every LR carries provenance: `llm_generated` · `llm_reassessed` · `author_overridden` ·
`literature_anchored`. Ungrounded LRs are usable but must be visibly marked as such.

**See:** `docs/llm-panels.md`

---

## ADR-008 — We hold no student identifiers and no re-identification key.

**Status:** ACCEPTED (2026-07-28)

**Context.** The simulator issues a code that the student downloads with their transcript and
gives to the school. UNMC maps code to student. Students never enter names.

Avoiding FERPA entirely is likely not achievable for a system operated on behalf of a course. The
achievable and more useful goal is that the data *we* hold is de-identified under the
coded-data provision, which requires that the code is not derived from student PII, is not
disclosed to us, and that the key is never shared.

**Decision.** Codes are generated by UNMC (or by the simulator with no student input), opaque, and
never derived from student data — a hashed NetID is **not** acceptable, since a hash over a
guessable key space remains identifying. Learner data lives in its own schema, separable from case
authoring data, so the "we hold no key" claim is demonstrable rather than asserted. A written
data-use agreement records that we never receive the key.

**Consequences.** Free text, not the schema, is the live exposure — see ADR-009. Analytic exports
need coarsened timestamps and small-cell suppression. IRB determination should be obtained in
writing *before* collection if publication is intended.

**See:** `docs/privacy-data-handling.md`

---

## ADR-009 — Transcript de-identification is prevention first, targeted redaction second.

**Status:** ACCEPTED (2026-07-28)

**Context.** Students cannot be prevented from saying their name aloud, and transcripts are
persisted as free text. A naive name-scrubber is actively harmful here: the simulated patient has
a name that learners legitimately use throughout, so generic person-name redaction would destroy
the transcript while still missing self-introductions phrased unusually.

**Decision.** Three layers, cheapest first:

1. **Prevention — learners introduce themselves as "Dr. X"** (the literal letter X). A positive
   instruction rather than a prohibition, it preserves natural conversation because the patient can
   still address them by name, and it inverts the redaction problem: the scrubber's job becomes
   *confirm the only self-name is "Dr. X"* rather than *find any name*, which is far higher
   precision. The patient persona is instructed never to ask for a name, to accept "Dr. X" as a
   complete introduction without comment, and to never echo a real name if one is given anyway.
   STT variants (`Dr. Ex`, `Dr. Ecks`, `Doctor X`) are treated as equivalent.
2. **Targeted redaction** — pattern-match self-referential introductions on *learner-role turns
   only*, with the case's patient names allowlisted. Redaction events are logged so the rate is
   observable; a high rate means layer 1's copy needs revision.
3. **Review flag** — an LLM pass marks residual candidates for human review rather than
   auto-deleting, since silent deletion of a false positive is unrecoverable.

**Note.** The patient persona previously *prompted learners for their name* — "you haven't even
told me your name. Who are you?" — as part of its professionalism feedback. That directly
contradicted the goal and has been changed to "you haven't introduced yourself. Are you the
doctor?"

**Open risk.** Voice mode sends audio to a third party. Retained audio of a student's voice is
identifying regardless of transcript hygiene, and is a larger exposure than the text. Vendor
retention settings must be verified.

**See:** `docs/privacy-data-handling.md`

---

## ADR-010 — Learner Final Order ratings are collected in simulator phase 3.

**Status:** ACCEPTED (2026-07-28) · Implemented in `direct-sim`

**Context.** The simulator reveals all delayed results in phase 4. Collecting appropriateness
ratings during or after feedback means the result is already on screen and the rating is
meaningless.

**Decision.** Ratings are collected in phase 3, alongside the existing clinical reasoning
submission — already a form-entry step with no orders and no interview. Phase 4 may then reveal
Final Order results, after ratings are locked.

Suppression during the encounter is enforced by **deterministic pre-model interception**, not by
prompt instruction. Every existing simulator safeguard is an instruction given to a model holding
the answer; that is adequate for anti-gaming and inadequate for something a measurement depends on.

**See:** `direct-sim/FINAL_ORDERS_TODO.md`

---

## ADR-013 — Treat the shared `case_details` table as schema-loose; coerce on read.

**Status:** ACCEPTED (2026-07-28) · BUILT

**Context.** `case_details` is declared with `JSON` columns but is shared with the simulator
and predates this generator. As of 2026-07-28, `custom_input` holds a JSON **string** in 64 of
103 rows, a dict in 38, a null in one, and something that decodes to a non-dict in one.
`Image Links` inside it has three shapes: `[{"Test Name", "Test Link"}]` in 33 rows, bare URL
strings in 3, empty or absent in the rest.

The editor assumed a dict and crashed on `.get()`, so 65 of 103 cases could not be opened.
Worse, it rendered links as bare URLs and wrote plain strings back, so saving any of the 33
named-link cases would have silently discarded the test name — which the simulator's orders
prompt actually renders.

Notably, `direct-sim` already types these as `dict | str | None` on `CaseResponse` and
`OrdersRequest`. The simulator has always known the column is heterogeneous; the generator was
the only side assuming otherwise.

**Decision.** The generator owns neither the table nor its history, so it validates rather than
assumes. `coerce_json_field()` and `normalize_image_links()` normalise on read, falling back to
defaults for anything undecodable rather than propagating a shape the caller cannot use.
`GET /sim-ready/case/{id}` normalises centrally so every consumer gets a usable shape, and the
editor coerces again on its own — the table is shared, so no single layer can guarantee it.

**Consequences.** Any new field read from `case_details` needs the same treatment. Do not add
`.get()` calls against these columns without coercing first. Verified by replaying all 103
production rows: 65 would have crashed the old editor, 0 fail now, 33 test names preserved.

**Applies more broadly:** the same reasoning covers any table this repo shares with the
simulator. Writing is ours to control; reading is not.

---

## ADR-012 — Every image carries build provenance, surfaced in the UI and the API.

**Status:** ACCEPTED (2026-07-28) · BUILT

**Context.** `deploy-aca.sh redeploy` built the `:v1` tag and then ran
`az containerapp update --image ...:v1` against an app already running `:v1`. Container Apps
creates a new revision only when the revision spec changes, so an identical image reference
was a no-op: the new image landed in ACR, the app kept serving the old one, and the script
printed success.

The backend ran a **2026-03-10** image until this was found on 2026-07-28. Everything merged
in between — including `92e057c "updated for editing"`, the post-finalization editing work
that was Cory's top March request — was never actually live, while `CLAUDE.md` documented it
as shipped. Nothing in the UI, the API, or the deploy output disagreed.

**Decision.** Two independent fixes, because either alone would have failed:

1. **Unique image tag per build** (`<sha>-<timestamp>`), which forces a new revision. This
   fixes the deploy.
2. **Build provenance baked into every image** — `GIT_SHA`, `BUILD_TIME`, `IMAGE_TAG` as
   Docker build args — surfaced at `GET /` and in the Streamlit footer. This fixes
   *noticing*, which is the part that actually failed for four months.

The footer shows **both** frontend and backend stamps and warns when they diverge. Drift
between the two is precisely what happened and precisely what was invisible. It also warns
when `authoring_persistence` is false.

A build arg is deliberately absent by default, reporting `unknown` rather than a guess — an
unstamped process is a fact worth seeing, not something to paper over.

**Consequences.** Anything believed shipped between 2026-03-10 and 2026-07-28 needs
re-verifying once a real deploy lands. `git rev-parse` marks a dirty working tree as
`<sha>-dirty`, since `az acr build` uploads the working directory rather than a git ref.

---

## ADR-011 — Item-level storage for learner responses, aggregates computed on read.

**Status:** ACCEPTED (2026-07-28)

**Context.** Performance tracking could store per-case scores or per-item responses.

**Decision.** Store per-item. Aggregates are derived and never the only record.

**Consequences.** Yields item difficulty and discrimination per Final Order once enough runs
accumulate — giving two independent estimates of item quality: Oracle entropy as the *a priori*
prediction and observed learner variance as the *empirical* one. Comparing them is a publishable
question that falls out for free if the schema is granular from day one, and is unrecoverable if
it is not.

**See:** `docs/architecture-target.md`
