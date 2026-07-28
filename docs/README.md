# Documentation map

Progressive disclosure: read the smallest thing that answers your question. Every document below
declares what it covers and what it does not, so you can stop reading early.

## Start here, always

| If you are… | Read |
|---|---|
| New to the repo | `../README.md`, then `../CLAUDE.md` |
| Writing code | `../CLAUDE.md` (current state) **and** `../Decisions.md` (target state) |
| About to design something | `../Decisions.md` first — the question may already be settled |
| Picking up planned work | `../ToDos.md` |

> **Important for fresh sessions.** `../CLAUDE.md` describes the system **as it exists today**.
> `../Decisions.md` describes **where it is going**, and several accepted decisions supersede what
> `CLAUDE.md` documents. Where they conflict, `Decisions.md` wins. Building from `CLAUDE.md`
> alone will reproduce the architecture we are moving away from.

## Deep reference — load on demand

| Document | Read it when | It does **not** cover |
|---|---|---|
| `architecture-target.md` | Touching the case record, storage, versioning, editing, or the format toggle | LLM call mechanics; privacy |
| `llm-panels.md` | Building or changing the Oracle, LR re-assessment, or anything that fans out to N raters | Case storage; simulator behavior |
| `final-orders-sct.md` | Building the Final Orders authoring flow or its schema | Simulator-side suppression and rating collection |
| `privacy-data-handling.md` | Touching transcripts, learner data, exports, or schema access | Case authoring |
| `Final_Orders_Oracle_Proposal.docx` | You need the research-group-facing version, or the methodology rationale in prose | Implementation detail |

## The other repository

The simulator lives at `../../direct-sim` and shares the same Neon database. Work there is
tracked in `direct-sim/FINAL_ORDERS_TODO.md`, which also audits the simulator's existing
disclosure safeguards. Read it before changing anything a learner sees, and before any schema
change to shared tables — the simulator owns the Alembic migrations.

## Conventions

- Decisions go in `../Decisions.md` as numbered ADRs, never inline in code comments or these docs.
  These docs reference ADRs; they do not restate the rationale.
- `CLAUDE.md` stays lean. New detail goes in `docs/` with a routing-table entry above.
- When a decision changes, mark the old ADR `SUPERSEDED` rather than deleting it — the history is
  the point.
