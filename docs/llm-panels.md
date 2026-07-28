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
against the diagnosis string, its tokens, and a curated synonym/abbreviation list. A hit blocks
and shows the author what leaked. A warning would be dismissed.

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
| 12 | Otolaryngologist (dizziness cases) |
| 13 | EM physician, explicit diagnostic-stewardship orientation |
| 14 | EM physician, explicit risk-averse orientation |
| 15 | Medical educator, clinical reasoning expertise |

Panelists 13 and 14 are deliberate. Appropriateness ratings for advanced imaging are genuinely
bimodal along the stewardship-versus-defensive axis; a roster omitting it produces falsely tight
distributions and makes items look more settled than they are. This represents real practice
variation rather than steering the panel — but expect the question and keep the rationale handy.

`panel_roster_version` is stored per run so a roster change is visible in the data.

## 5. Model settings

| Parameter | Value | Notes |
|---|---|---|
| Model | `gpt-5.6` | Responses API; env-configurable |
| Reasoning effort | `medium` | Reading of "fourth notch, not the highest" — confirm against the published ladder |
| Panel size | 15 | Configurable |
| Concurrency | 8 | Semaphore |

The Responses API is a **separate call path** from the rest of the generator, which runs
`client.beta.chat.completions.parse()` on `gpt-4o` and has no reasoning-effort parameter:

```python
response = client.responses.create(
    model=ORACLE_MODEL,
    reasoning={"effort": ORACLE_REASONING_EFFORT},
    input=[{"role": "system", "content": persona},
           {"role": "user", "content": item_prompt}],
)
```

Variance comes from personas, not temperature — reasoning models do not expose temperature the way
chat models do, and it would be the wrong lever regardless.

## 6. Schema

```
panel_runs
  id                      PK
  item_type               'final_order_appropriateness' | 'lr_reassessment'
  item_ref_id             FK into the consumer's table
  case_version_id         FK -> case_versions.id      -- staleness pin (ADR-003)
  panel_size_requested, panel_size_realized
  model, reasoning_effort, provider, prompt_template_version, panel_roster_version
  blinded_context_hash, claim_hash
  status                  pending | complete | failed
  superseded_by           FK nullable -> panel_runs.id
  aggregates              JSONB   -- convenience copy; ratings rows are authoritative
  created_at, completed_at

panel_ratings
  id                      PK
  run_id                  FK -> panel_runs.id (indexed)
  panelist_index, persona_id, persona_hash
  value                   JSONB   -- shape per item_type
  rationale               TEXT
  status                  ok | parse_error | refusal | api_error
  raw_response_id, latency_ms, tokens_in, tokens_out, created_at
```

## 7. Aggregation

Store the histogram; compute everything else at read time, so the scoring rule can change without
regenerating data.

**Oracle:** full 5-bin histogram · modal rating and modal proportion · mean and SD · Shannon
entropy across bins (0 unanimous, max log₂5 ≈ 2.32) · SCT credit vector where
`credit(k) = count(k) / count(mode)`.

**Item-quality flags for the authoring UI** — the entropy display for case authors:

| Signal | Meaning shown to the author |
|---|---|
| modal proportion ≥ 0.80 | Low discrimination — most panelists agree; this item will not separate learners |
| entropy ≥ ~2.0 (near uniform) | No signal — disagreement without pattern; the item or stem may be ambiguous |
| mass at both tails, little center | Genuine controversy — often the most informative item type; verify the stem is not ambiguous |
| modal proportion ≈ 0.4–0.7 with adjacent mass | Good discrimination |

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
