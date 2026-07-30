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
need coarsened timestamps and small-cell suppression.

**Confirmed 2026-07-29.** UNMC holds IRB approval and is the sole source of student identities,
which never enter our pipeline; students are reinforced not to use their names. This is the
arrangement the ADR was written against, now confirmed rather than assumed. Two items remain
because the IRB approval and the data-use agreement answer different questions: get in writing
which protocol covers this and whether our role is named, and record in the DUA that we never
receive the key — that clause is what makes the data we hold coded rather than identifiable.

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

**Open risk — closed 2026-07-29.** Voice mode sends audio to a third party, and retained audio of
a student's voice is identifying regardless of transcript hygiene. **ElevenLabs does not retain
audio for this account** (confirmed by Cory). Retention is an account setting rather than a
property of the vendor, so it is worth re-checking periodically and worth having in writing in the
data processing agreement.

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

## ADR-014 — Research-group answers on the Final Orders / Oracle design.

**Status:** ACCEPTED (2026-07-29) · BUILT · Source: Cory Rohlfsen's comments on
`Status_and_Decisions_Needed`, plus David's notes the same day

**Context.** Section 3 of the status memo put nine questions to the group with a default for each.
Five came back with answers, and three of them changed the design rather than confirming it.

**Decisions.**

**1. The Oracle is for Final Orders only, and only when a case has them.** "The 15-role Oracle
panel should not be used for learner rating. If no Final Order is generated for a case, then no
15-role Oracle panel should be activated." Enforced in code, not by convention: zero
`case_final_orders` rows means `POST .../oracle/run` refuses with `no_final_orders`, and
`run_oracle_for_case_version` returns `skipped` rather than running an empty panel. There is no
global toggle to get wrong.

**2. The panel weighs the cost of commission.** "As long as runs are independent, panel should
consider costs of co-mission (e.g. brain biopsy comes at a great cost so strength of clinical
justification must have real weight)." This is now an explicit paragraph in the shared rater
instruction (`panel_runner.RATER_INSTRUCTION`), tying required justification to the burden of the
action. Independence was already an invariant — one call per (claim × panelist), never batched —
and is now also a stated condition of the group's approval, which raises the cost of anyone
"optimising" it later.

**3. Reasoning effort is `medium`.** "Yes, we don't need highest reasoning possible for this."
Confirms the reading of "fourth notch, not the highest".

**4. The name stays "Final Orders".** "Key Management Decisions is going to confuse the 3 next
steps orders panel which is separate." Two distinct instruments that must not be conflated:
*Final Orders* are authored per case in the generator and rated on a −2..+2 scale; *3 next steps
in management* is a standard open text box in the simulator, unauthored and ungraded by this
subsystem. Proposal §4.3 Change 5 is withdrawn.

**5. The specialty seat is generalised.** Roster seat 12 was a fixed otolaryngologist, chosen for
the planned dizziness cases. It is now "applicable specialty surgeon or subspecialist", set per
case via `case_versions.oracle_specialty` and recorded per run as `panel_runs.roster_specialty`.
A case that names no specialty gets a generic reading of that seat rather than a silently
otolaryngological one. Changed before any run existed, so `ROSTER_VERSION` stays `v1`.

**6. The stem revision is not yet approved.** "If something is being suggested to change what is
currently in place, then I would want to see those changes first." The revision *is* a change, so
it is not treated as adopted. Both wordings live in `backend/utils/oracle_stems.py` — `v1_original`
(Alex's draft) and `v2_revised` (proposal §4.2) — rendered verbatim, selected by
`ORACLE_STEM_VERSION`, and stamped onto every run as `panel_runs.stem_version`. `GET /oracle/stems`
renders both side by side from the code that will actually run, so the comparison cannot drift from
a document. Default is `v2_revised`; switching is one environment variable.

**Consequence, and it is the sharp one:** the stem determines what the panel is asked, so changing
it invalidates every distribution generated before the change. **No production Oracle panel should
run until the group confirms the wording.** Nothing is lost by waiting — no case carries Final
Orders yet.

**Still unanswered:** splitting the panel across two model families (default: single family for
v1) and scheduling the human validation panel (default: propose alongside the spring review).

**See:** `docs/final-orders-sct.md`, `docs/llm-panels.md`

---

## ADR-015 — Model selection stays global and admin-set; provenance is recorded per run.

**Status:** PROPOSED (2026-07-29) — recommendation, awaiting confirmation

**Context.** The simulator has an admin dashboard for choosing a model per task. The concern
raised was that an admin change might be lost on redeploy, and the question was whether to move
model selection into the case record instead.

**The premise does not hold.** `direct-sim/backend/model_settings.py` persists overrides in the
`model_settings` table and serves them from a 30-second cache. A redeploy restarts the process and
empties the cache; it does not touch the table. Admin changes already survive deploys.

**Decision (recommended).** Keep model selection global and admin-set. Do **not** move it into the
case record.

- A case pinned to a model is pinned to a model that will be deprecated. Ten cases authored across
  a year would carry ten different pins and would need editing one by one when a provider retires
  a model.
- The reproducibility need is real but is a *provenance* need, not a *configuration* need. What
  research requires is knowing which model produced a given artifact, which is satisfied by
  recording it on the artifact. The Oracle already does this: `panel_runs` stores `model`,
  `reasoning_effort`, `provider`, `api_surface`, and `prompt_template_version` per run.
- Per-case model choice also multiplies the test matrix by the number of models, for a benefit no
  one has asked for.

**Two real defects this did surface, both in the simulator:**

1. `MODEL_OPTIONS` is stale and, worse, **inconsistent with the defaults**. The `assessment` task
   defaults to `anthropic/claude-opus-4.6`, which is not in `MODEL_OPTIONS` — so the admin dropdown
   cannot represent the value currently in force, and opening the page and saving would silently
   change it to whichever option renders first.
2. **A code default silently takes effect for any task with no database row.** Bumping a default in
   a deploy changes the model for every unoverridden task, with nothing recording that it happened.
   The fix is to record the resolved model on the artifact — `transcripts` and `assessments` should
   carry the model that produced them, the way `panel_runs` does.

**Consequence.** Fixing (1) is a list edit plus a validation that every default appears in
`MODEL_OPTIONS`. Fixing (2) is two columns. Neither is urgent, and both are cheap now and
archaeological later.

---

## ADR-016 — All LLM calls route through OpenRouter, with no fallback.

**Status:** ACCEPTED (2026-07-30) · BUILT

**Context.** The generator called the OpenAI API directly while `direct-sim` already used
OpenRouter. Moving both to one provider gives a single key to manage, access to every model without
a per-provider integration, and a faster path than an Azure deployment.

**Decision.** `backend/utils/llm_client.py` is the single place the provider is chosen. Both call
paths — case generation and the Oracle panel — build their client there, so they cannot end up
pointed at different providers.

**Three consequences worth recording:**

1. **The Oracle moved off the Responses API.** That surface is OpenAI-only. OpenRouter is
   wire-compatible with Chat Completions, and reasoning effort passes through its unified
   `reasoning` parameter via `extra_body`. Verified end to end on 2026-07-30: structured outputs
   parse, effort is accepted, token counts and latency are recorded.
2. **`gpt-5.6` does not exist.** The design proposal specified it; OpenRouter's catalogue ships the
   5.6 line as `-luna` / `-sol` / `-terra`. Selected: `openai/gpt-5.6-sol`. This is exactly the
   class of error the "verify before use" rule exists for — the id was plausible, documented, and
   wrong.
3. **No silent fallback to the OpenAI API.** The OpenRouter key carries zero data retention; the
   direct OpenAI key does not. A fallback triggered by a missing environment variable would quietly
   change the retention posture of case content at the moment a deploy was misconfigured, which is
   the moment least likely to be noticed. Missing configuration raises. `LLM_PROVIDER=openai` is
   available as a deliberate, visible choice.

**Consequence.** `OPENROUTER_API_KEY` must be added to Container Apps secrets before the next
deploy, or the backend will fail to start. That is the intended behaviour.

---

## ADR-017 — The Oracle refuses to rate a case the learner will not see.

**Status:** ACCEPTED (2026-07-30) · BUILT

**Context.** "Are we sure the Oracle gets the same content as the user?" The answer was no. The
blinded context is built from `case_versions.content_structured`; the simulator serves
`case_details.content`. They agree at finalization and diverge afterwards two ways: a hand-edited
markdown override (`render_detached`, ADR-002), and `PUT /sim-ready/case/{id}` updating the
simulator row in place without creating a new version — the known gap in `ToDos.md` Phase 1.

Either one means the panel rates a case that no longer exists, and produces a distribution that
looks entirely valid.

**Decision.** `check_content_parity()` compares the stored rendered projection against the live
simulator content and blocks the panel on any mismatch, before a single call is spent. Surfaced in
the preflight panel so the author sees it while authoring rather than after.

**Not overridable, unlike the leak audit.** A leak hit can be a true match with a benign
explanation — "CVA" under Family History is the father's. Content drift admits no such explanation.
A stale distribution is worse than a missing one precisely because it will be used.

**Consequence.** Editing a case now blocks its Oracle panel until the case is re-saved as a new
version. That is a real workflow constraint, and it is the correct one: it surfaces the missing
"version on edit" work rather than quietly generating data around it.

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
