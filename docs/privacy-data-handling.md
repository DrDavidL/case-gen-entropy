# Privacy and data handling

> **Read this when:** touching transcripts, learner data, analytic exports, schema access, or
> anything that crosses the boundary between case authoring and student records.
> **Prerequisite:** `../Decisions.md` ADR-008, ADR-009.
> **Does not cover:** case authoring (`architecture-target.md`).
>
> **This is engineering guidance written from a design conversation, not a legal determination.**
> The structure below is intended to make the compliance argument straightforward; it should be
> confirmed with UNMC counsel and the IRB before collection begins.

---

## 1. The posture

Students never enter names. The simulator issues an opaque code, which the student downloads with
their transcript and gives to the school. UNMC maps code to student and holds that mapping.

Avoiding FERPA entirely is probably not achievable for a system operated on behalf of a course — a
party acting for the institution is covered. The achievable and more useful goal:

> **We hold no student identifiers and no re-identification key.**

That places the data we hold within the coded-data provision (34 CFR §99.31(b)(1)), which
contemplates exactly this arrangement and holds when three conditions are met:

1. the code is **not derived from student PII**;
2. the code is **not disclosed to us**;
3. the institution **does not disclose the key**.

All three are structural, and all three are things we can build for and record. A written data-use
agreement with UNMC stating that we never receive the key is what makes the argument
demonstrable rather than merely asserted.

**A hashed NetID is not acceptable as a code.** A hash over a small, guessable key space is
reversible by enumeration and remains identifying. Codes must be random.

## 2. Schema separation

Learner data lives in its own schema, separate from case authoring (ADR-008):

| Schema | Contents | Access |
|---|---|---|
| `authoring` | Case families, versions, frameworks, LRs, Final Orders, panel runs | Case authors, developers |
| `learner` | Runs, item responses, transcripts, assessments | Restricted; separately grantable |

Case data is not student data and should not inherit its handling constraints. The separation is
also what lets "we hold no key" be demonstrated by showing the schema rather than asserted in
prose.

## 3. Free text is the live exposure

The schema is the easy part. Transcripts are free text and a student will eventually say their
name aloud. This cannot be prevented at the source, and it is the most underestimated risk in the
design.

**A naive person-name scrubber is actively harmful here.** The simulated patient has a name that
learners use legitimately throughout the encounter. Generic name redaction would gut the
transcript while still missing self-introductions phrased in unanticipated ways — the worst of
both outcomes.

Three layers, cheapest first (ADR-009):

### Layer 1 — Prevention: learners are "Dr. X"

**Status: implemented.** The highest-yield intervention, and it is a prompt change.

Learners introduce themselves as **"Dr. X"** — the literal letter X, not their own name.

Why this beats "introduce yourself as Doctor":

- It is a **positive instruction**, which people follow far better than a prohibition.
- It **preserves natural conversation** — the patient can address them ("Take your time, Dr. X")
  instead of awkwardly avoiding a name.
- It **inverts the redaction problem.** The scrubber's job becomes *confirm the only self-name is
  "Dr. X"* rather than *find any name in free text*. Anything else appearing as a self-introduction
  is an anomaly, which is a far higher-precision signal.

Implemented in:

| Location | Change |
|---|---|
| `direct-sim/sim_prompts.py` → `learner_tasks`, `no_orders_learner_tasks` | Callout + task 1: introduce as "Dr. X" |
| `direct-sim/sim_prompts.py` → `sim_persona` §5b | Never ask for a name; accept "Dr. X" without comment; never echo a real name |
| `direct-sim/sim_prompts.py` → `sim_persona_aliquot2` §6 | Same rule for the orders phase |
| `case-gen-entropy/backend/utils/sim_ready_transform.py` → `build_default_learner_tasks()` | Per-case default learner tasks carry the instruction |

Two details that are easy to get wrong:

- **STT variants.** Speech-to-text renders this as `Dr. Ex`, `Dr. Ecks`, or `Doctor X`. All
  personas and any future validation pattern must treat these as equivalent.
- **The persona used to ask for names.** Its professionalism feedback included *"you haven't even
  told me your name. Who are you?"* — actively soliciting the identifier we are trying to keep out.
  Changed to *"you haven't introduced yourself. Are you the doctor?"*

The generator default and the simulator constant must stay in sync; both carry a comment saying so.

### Layer 2 — Targeted redaction

**Status: not built.** Pattern-match **self-referential introductions on learner-role turns only**,
with the case's patient names allowlisted. With layer 1 in place the check is mostly a validation:
any self-introduced name that is not "Dr. X" (or an STT variant) is the target.

```
"I'm Dr. <Name>" · "my name is <Name>" · "this is <Name>" · "<Name> speaking"
"I'm a third year named <Name>" · "Dr. <Name> here"
```

Redaction replaces with a marker and **logs the event**, so the rate is observable rather than
invisible. A high rate means layer 1 is not working and the orientation copy needs revision.

### Layer 3 — Review flag, not auto-delete

An LLM pass marks residual candidates for human review. It does not delete. Silent deletion of a
false positive is unrecoverable and a transcript is the primary research artifact.

## 4. Voice mode — the larger exposure

Voice sends audio to a third party. **Retained audio of a student's voice is identifying
regardless of transcript hygiene**, and is a bigger exposure than anything in the text pipeline.

Before voice mode is used with real students:

- Verify the vendor's audio retention settings and disable retention where possible.
- Confirm what the data processing agreement says about retention and training use.
- Apply the same transcript scrubbing to returned voice transcripts — they are text by the time
  they reach us and go through the identical pipeline.

This is an open item, not a solved one.

## 5. Analytic exports

Ten cases at one institution produces small cells fast. Broken down by case × student level ×
attempt, cells reach single digits, and a "targeted requester" — someone at UNMC who knows who ran
which case when — can re-identify. **Timestamps alone are often sufficient** against a class
schedule.

Export rules:

- Coarsen timestamps (week or term, never datetime).
- Suppress small cells below an agreed threshold.
- Export from a separate analytic view, never from the operational tables directly.
- No free-text transcripts in aggregate exports.

## 6. Process items

Cheap now, painful later:

- [ ] Written data-use agreement with UNMC recording that we never receive the key
- [ ] IRB determination **in writing before collection**, if publication is intended — the
      educational-QI framing will likely land as exempt or not-human-subjects, but obtaining it
      retroactively is a well-known way to lose a dataset
- [ ] Retention and deletion policy, defined per case version
- [ ] Vendor audio retention verified and documented (§4)
