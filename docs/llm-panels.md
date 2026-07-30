# LLM panel subsystem

> **Read this when:** building or changing the Oracle (Final Order appropriateness), LR
> re-assessment, or any future feature that fans a claim out to N independent raters.
> **Prerequisite:** `../Decisions.md` ADR-005, ADR-006, ADR-007.
> **Does not cover:** case storage (`architecture-target.md`), Final Orders authoring
> (`final-orders-sct.md`), simulator behavior (`../../direct-sim/FINAL_ORDERS_TODO.md`).

---

## 1. One subsystem, two consumers

The Oracle and LR re-assessment are the same operation with different item types:

> take a claim → send it to N independent, blinded raters → store each rating with its reasoning →
> compute a distribution → surface disagreement to the author

Build one runner and one set of tables with an `item_type` discriminator (ADR-006). The
differences between consumers are entirely in the prompt template, the response schema, and the
aggregation function — none of which justify a second subsystem.

| | Oracle | LR re-assessment |
|---|---|---|
| Claim | "Ordering a brain MRI is appropriate" | "LR for *HINTS exam central pattern* given *posterior stroke* is 4.2" |
| Rater output | Ordinal −2..+2 + rationale + top diagnostic concerns | Range + basis + confidence + citations |
| Aggregation | Histogram, mode, entropy, SCT credit vector | Median range, divergence from original, grounding rate |
| Blinding | Diagnosis withheld | Original LR value withheld |

Note the second blinding requirement: a re-assessor shown the existing LR will anchor on it. The
panel must estimate independently, and only then is the original compared against the distribution.

## 2. Shared runner contract

```
run_panel(
    item_type,            # 'final_order_appropriateness' | 'lr_reassessment'
    claim,                # the thing being rated
    blinded_context,      # what raters see — built by the caller, fails closed
    roster,               # ordered persona list; versioned
    model, effort,        # provider settings
    response_schema,      # structured output contract
) -> PanelRun
```

Invariants the runner enforces regardless of consumer:

- **One call per (claim × panelist).** Never batch multiple claims into one call — rating several
  items in one response creates contrast and anchoring effects that make the per-item
  distributions statistically dependent and therefore uninterpretable on their own. Costs N× more
  and remains cheap.
- **Concurrency with a semaphore.** Default 8. The existing generation pipeline is strictly
  sequential via `asyncio.to_thread`; sequential panel execution would take roughly half an hour
  per case.
- **Realized N is recorded separately from requested N.** A call that fails after retries is a
  null-outcome row, excluded from the denominator. Never rate 14 and report 15.
- **Per-call rows are the source of truth.** Aggregates are computed, never the only record.
- **Runs are append-only.** A re-run supersedes via pointer; nothing is overwritten. For a
  research dataset, what was generated and when is part of the data.

## 2a. Does the panel rate the case the learner sees?

Not automatically, and the answer used to be no. The two sides come from different columns: the
blinded context is built from `case_versions.content_structured`, while the simulator serves
`case_details.content`. They agree at finalization and diverge afterwards in two ways.

| Divergence | Cause | Detected as |
|---|---|---|
| `render_detached` | Author hand-edited the markdown at finalize, so it is no longer a projection of the structured record (ADR-002) | `render_detached` |
| In-place edit | `PUT /sim-ready/case/{id}` writes `case_details.content` without creating a new `case_version`, leaving the structured record behind — the known gap in `ToDos.md` Phase 1 | `content_drift` |

`check_content_parity()` compares `case_versions.content_rendered` against the live
`case_details.content` and **blocks the panel** on either. Unlike the leak audit this is *not*
overridable: a leak hit can be a true match with a benign explanation, but content drift cannot be
explained away — it means the distribution would describe a case that no longer exists. A stale
distribution is worse than a missing one, because it will be used.

The authoring UI shows parity in the preflight panel before any call is spent, and blocking here is
what surfaces the missing "version on edit" work rather than quietly producing bad data around it.

## 3. Blinding is built, not stripped

Blinded context is constructed from structured fields, **never** by redacting rendered text.
Construction fails closed: a field not explicitly included cannot leak. Redaction fails open: a
pattern not anticipated leaks silently.

For the Oracle the rendered case content is unusable directly — it contains a Diagnostic Reasoning
section with the differential and rationale, a Teaching Points section, and a summary paragraph
that routinely names the diagnosis outright.

**Included:** door chart, vitals, full OLDCARTS HPI, PMHx/SHx/FHx, medications and allergies, ROS,
physical exam, results of tests the case specifies (except Final Orders).

**Excluded by construction:** case title, paragraph summary, diagnostic reasoning, teaching
points, `primary_diagnosis`, the author's original description, any Final Order result.

**Leak audit, blocking.** Before the panel runs, check the blinded context case-insensitively
against the diagnosis string, its tokens, and a curated synonym/abbreviation list. Matching is
word-boundary anchored — without that, `MI` hits inside `mild` and the audit becomes noise. A hit
blocks and shows the author what leaked, with the `### ` section it came from. A warning would be
dismissed.

**The audit has legitimate false positives, and pretending otherwise is how it gets disabled.** A
posterior-circulation-stroke case whose family history reads "Father CVA at 70" trips the `CVA`
synonym. That is a relative's history, not this patient's diagnosis, and blocking it outright would
make the audit unusable on exactly the cases this project is built around. The resolution is a
**recorded override**: `POST .../oracle/run` accepts `leak_override_reason`, the only way past a
failing audit, and stores it on every run it produces (`panel_runs.leak_override_reason`). The
default stays fail-closed, the exception is visible in the research data, and nobody is tempted to
loosen the matcher. Overrides are logged at error level even though they were authorised.

**Post-hoc transparency signal.** Oracle panelists name their top 2–3 diagnostic concerns. If
>80% name the exact ground truth, the case is diagnostically transparent — not necessarily a
defect, but it changes how the item behaves and the author should see it.

## 4. Where variance comes from

Fifteen calls to one model with one prompt are fifteen draws from a single posterior, not fifteen
experts (ADR-005). Real SCT panels derive their value from heterogeneity — typically 10–20
clinicians across specialties, settings, and experience.

Variance is induced through a **fixed, version-controlled persona roster**, so runs stay
reproducible. Default roster (n=15) is weighted toward emergency medicine because the initial
cases are ED presentations:

| # | Role |
|---|---|
| 1–3 | EM attending — community, academic, high-volume urban |
| 4–5 | EM attending — 2 years post-residency; 20 years post-residency |
| 6–7 | General internist; hospitalist |
| 8–11 | Neurologist; cardiologist; family medicine; geriatrician |
| 12 | **Applicable specialty surgeon or subspecialist** — bound per case |
| 13 | EM physician, explicit diagnostic-stewardship orientation |
| 14 | EM physician, explicit risk-averse orientation |
| 15 | Medical educator, clinical reasoning expertise |

**Seat 12 is case-bound** (ADR-014). It was a fixed otolaryngologist, chosen for the planned
dizziness cases; Cory asked that it generalise, since the relevant specialist depends on the
presentation. It is set per case via `case_versions.oracle_specialty`, resolved at
`build_roster(specialty)`, and recorded per run as `panel_runs.roster_specialty`. A case that names
no specialty gets the generic description rather than a silently otolaryngological reading.

Panelists 13 and 14 are deliberate, and the group approved them explicitly. Appropriateness ratings
for advanced imaging are genuinely bimodal along the stewardship-versus-defensive axis; a roster
omitting it produces falsely tight distributions and makes items look more settled than they are.
This represents real practice variation rather than steering the panel.

`panel_roster_version` is stored per run so a roster change is visible in the data. **Do not edit a
published roster in place** — add a version. Roster `v1` is what ships; nothing ran under the
otolaryngology-specific draft.

## 4a. What every panelist is told

Beyond the persona, all raters share one instruction block, versioned with
`prompt_template_version` (`panel_runner.RATER_INSTRUCTION`, currently `oracle-v1`). Three parts are
load-bearing:

- **Blinding is stated.** The rater is told they are not given the final diagnosis, so they reason
  under uncertainty rather than hunting for a withheld answer.
- **One action only.** "Do not calibrate your answer against orders you are not being asked about."
  This backs up the one-call-per-claim invariant at the prompt level.
- **Cost of commission carries weight.** Required by the group (ADR-014): burden and risk of acting
  count, not only the risk of missing something, and the higher the burden of the action — a brain
  biopsy, an invasive procedure, a cascade-triggering test — the stronger the justification must be.
  Without this, a panel of clinicians told to consider a catastrophic miss drifts uniformly toward
  +2 and every item loses its discrimination.

Panelists are also asked to answer from their own stated perspective and explicitly *not* to guess
a consensus, since the disagreement is the measurement.

## 5. Model settings

| Parameter | Value | Env var | Notes |
|---|---|---|---|
| Provider | OpenRouter | `LLM_PROVIDER` | `openrouter` (default) or `openai` |
| Model | `openai/gpt-5.6-sol` | `ORACLE_MODEL` | Verified on OpenRouter 2026-07-30 |
| Reasoning effort | `medium` | `ORACLE_REASONING_EFFORT` | Confirmed by Cory 2026-07-29 (ADR-014) |
| Panel size | 15 | — | Roster length |
| Concurrency | 8 | `ORACLE_CONCURRENCY` | Semaphore |
| Stem version | `v2_revised` | `ORACLE_STEM_VERSION` | **Not yet group-approved** (ADR-014) |
| Specialty seat | generic | `ORACLE_DEFAULT_SPECIALTY` | Overridden per case |

**The proposal's `gpt-5.6` does not exist.** Checked against OpenRouter's catalogue on
2026-07-30: the 5.6 line ships as `-luna`, `-sol`, and `-terra` variants, and a bare
`openai/gpt-5.6` resolves to nothing. `openai/gpt-5.6-sol` is the selected model. An unknown id
fails as a 4xx on the first call for every panelist, which the runner reports as `api_error`
without retrying — loud, but only once someone looks.

**Both call paths go through OpenRouter** (`backend/utils/llm_client.py`), which is wire-compatible
with Chat Completions, so the panel and the generation pipeline share one SDK and one
structured-output helper. Reasoning effort rides along as OpenRouter's unified `reasoning`
parameter through `extra_body`:

```python
client.beta.chat.completions.parse(
    model=ORACLE_MODEL,
    messages=[{"role": "system", "content": persona}, {"role": "user", "content": item}],
    response_format=OracleRatingStructured,
    extra_body={"reasoning": {"effort": ORACLE_REASONING_EFFORT}},
)
```

This replaced an earlier Responses-API implementation. The Responses API is OpenAI-only; moving to
Chat Completions is what lets one provider serve both paths.

**There is deliberately no silent fallback to the OpenAI API.** The OpenRouter key carries
zero-data-retention and the direct OpenAI key does not, so a fallback triggered by a missing
variable would quietly change the retention posture of student-adjacent content at exactly the
moment nobody is watching — a fresh deploy. Missing configuration raises instead. Set
`LLM_PROVIDER=openai` to choose the direct path deliberately.

The Responses API is a **separate call path** from the rest of the generator, which runs
`client.beta.chat.completions.parse()` on `gpt-4o` and has no reasoning-effort parameter:

```python
response = client.responses.create(
    model=ORACLE_MODEL,
    reasoning={"effort": ORACLE_REASONING_EFFORT},
    input=[
        {"role": "system", "content": persona},
        {"role": "user", "content": item_prompt},
    ],
)
```

Variance comes from personas, not temperature — reasoning models do not expose temperature the way
chat models do, and it would be the wrong lever regardless.

## 6. Schema

Both tables live in the `authoring` schema, created by `0003_final_orders_and_panels` in
`direct-sim`. This app detects them via `final_orders_schema_ready()` and never runs DDL. That
probe is deliberately **separate** from `authoring_schema_ready()`: a deploy carrying 0002 but not
0003 must keep persisting framework and LR data and lose only the new features.

```
panel_runs
  id                      PK
  item_type               'final_order_appropriateness' | 'lr_reassessment'   (indexed)
  item_ref_id             INT (indexed) — NOT an FK; points into a different table per item_type
  case_version_id         FK -> case_versions.id      -- staleness pin (ADR-003)
  panel_size_requested, panel_size_realized
  model, reasoning_effort, provider, api_surface
  prompt_template_version, stem_version, panel_roster_version, roster_specialty
  blinded_context_hash, claim_hash
  leak_override_reason    TEXT nullable — set only when an author ran past a failed audit
  status                  pending | running | complete | failed
  error                   TEXT nullable
  superseded_by           FK nullable -> panel_runs.id
  aggregates              JSONB   -- convenience copy; ratings rows are authoritative
  created_at, completed_at

panel_ratings
  id                      PK
  run_id                  FK -> panel_runs.id (indexed)
  panelist_index, persona_id, persona_hash
  value                   JSONB   -- {"rating": int} for the Oracle
  rationale               TEXT
  top_concerns            JSONB   -- drives the transparency signal
  status                  ok | parse_error | refusal | api_error
  error                   TEXT nullable
  raw_response_id, latency_ms, tokens_in, tokens_out, created_at
```

Two details that look like oversights and are not:

- **`item_ref_id` has no foreign key.** It resolves to `case_final_orders.id` or
  `feature_likelihood_ratios.id` depending on `item_type`. A polymorphic reference cannot carry an
  FK without either two nullable columns or a join table, neither of which earns its cost here.
- **A run where nobody answered is `failed`, not `complete`.** `complete_run` demotes it. Reporting
  it as complete with an empty distribution is how a zero-rating run gets read as data.

## 7. Aggregation

Store the histogram; compute everything else at read time, so the scoring rule can change without
regenerating data.

**Oracle:** full 5-bin histogram · modal rating and modal proportion · mean and SD · Shannon
entropy across bins (0 unanimous, max log₂5 ≈ 2.32) · SCT credit vector where
`credit(k) = count(k) / count(mode)`.

**Item-quality flags for the authoring UI** — the entropy display for case authors. Evaluated in
this order, first match wins, with thresholds as named constants in `panel_aggregate.py` because
they are judgement calls the group may want to move:

| # | Condition | Flag shown to the author |
|---|---|---|
| 0 | realized N = 0 · realized N < 8 | No usable ratings · small panel, treat as provisional |
| 1 | modal proportion ≥ 0.80 | Low discrimination — most panelists agree; will not separate learners |
| 2 | entropy ≥ 2.00 | No signal — disagreement without pattern; the order or stem may be ambiguous |
| 3 | p(−2) ≥ 0.25 **and** p(+2) ≥ 0.25 **and** p(0) ≤ 0.15 | Genuine controversy — often the most informative item type |
| 4 | modal proportion 0.40–0.70 with ≥ 0.75 on the mode and its neighbours | Good discrimination |

Two ordering details matter, and both were wrong in the first cut:

- **Entropy is checked before controversy.** A distribution diffuse enough to reach entropy 2.0 is
  spread across four or five bins, which is absence of signal, not two camps.
- **Controversy is measured on the extreme bins only.** Testing `p(−2)+p(−1)` against `p(+1)+p(+2)`
  also matches a perfectly uniform distribution, which is the opposite finding. A flat 3/3/3/3/3
  panel scored as "genuine controversy" would tell an author to ship their worst item.

The transparency flag is additive rather than exclusive: it can accompany any of the above.

**LR re-assessment:** median of the panel's ranges · divergence of the original LR from that
median · fraction of panelists offering a citation · flag when the original falls outside the
panel's interquartile range.

## 8. Citation grounding (LR re-assessment)

Two models agreeing on a fabricated number improves internal consistency, not external validity
(ADR-007). Re-assessment therefore:

- returns a **range with a stated basis**, not a point estimate;
- attempts literature grounding via PubMed, returning identifiers where a source exists;
- records honestly when no source exists — expected for many LRs, and not a failure.

Provenance on every LR: `llm_generated` · `llm_reassessed` · `author_overridden` ·
`literature_anchored`. Ungrounded LRs remain usable but must be visibly marked in the UI and in
exports. The distinction has to survive into any downstream artifact, or the grounding work is
wasted.

## 9. Cost and scheduling

75 Oracle calls per case (5 orders × 15 panelists) at ~3–5k input tokens each. Cost is modest;
**runtime is the binding constraint** — 3–5 minutes at concurrency 8, which exceeds typical HTTP
and Azure ingress timeouts.

Panels therefore run as a background job. Finalization returns immediately with the case saved and
the panel marked `pending`, and a status endpoint reports progress. Panels run at finalization, not
preview — authors regenerate previews repeatedly while drafting, and each would otherwise trigger
a full panel. Runs are keyed by content hash so re-finalizing an unchanged case is a no-op.
