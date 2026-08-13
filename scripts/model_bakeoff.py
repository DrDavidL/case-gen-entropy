"""Score candidate panel models against clinicians, not against each other.

`ADR-018` split the roster across two model families because personas sharing a model
share its priors. Measured across 36 live runs on 2026-08-12, that split turned out to be
carrying nearly all of the instrument's discrimination: 24 of those runs would have had
**zero** entropy from the primary family alone. So the roster's composition is not a
detail, and the question of which models to add is worth answering with data.

**The trap this script exists to avoid.** The tempting move is to add whichever models
disagree most, because disagreement raises entropy and entropy looks like discrimination.
It is the wrong move. `sct_credit` awards partial credit in proportion to how many raters
chose each option, so a model that dissents because it is *weaker* at medicine hands
learners credit for wrong answers — and it does so invisibly, because the item's
statistics improve while the instrument degrades.

Calibrated entropy is the goal, not maximum entropy. The only way to tell the two apart is
to compare each candidate against clinicians on the same items. Hence two outputs:

  rating-sheet.md   For the research group. The blinded context and the rating items,
                    exactly as the panel saw them, with blank lines to fill in. Contains
                    **no model output** — anchoring a rater on the models' answers would
                    destroy the comparison this is for.
  results.md        Per item, every candidate's ratings *with its stated reasoning*.
                    Score this against the completed sheets afterwards. The reasoning is
                    the point: it is what lets a clinician say whether a minority rating is
                    a defensible position or simply wrong, which is a judgement no
                    agreement statistic can make.

Every call goes through `panel_runner._rate_once`, so the request shape, retries and
failure classification are identical to a production panel. A bake-off that used a
different call path would be measuring a different thing.

Usage:

    uv run python scripts/model_bakeoff.py --items 10 --out bakeoff-output
    uv run python scripts/model_bakeoff.py --dry-run     # select and cost it, call nothing
"""

import argparse
import json
import os
import statistics
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import sqlalchemy as sa  # noqa: E402

from backend.utils import blinded_context as bc  # noqa: E402
from backend.utils import panel_roster, panel_runner  # noqa: E402
from backend.utils.llm_client import build_client  # noqa: E402
from backend.utils.panel_roster import Panelist  # noqa: E402

# The candidates. Every one was verified on 2026-08-12 to accept the strict schema under
# this account's zero-data-retention policy; the whole current Anthropic line (opus-5,
# opus-4.8, sonnet-5) does not and cannot be tested here.
#
# `note` is what the single-item probe suggested, and is exactly the hypothesis this
# script exists to test properly rather than to trust.
CANDIDATES: list[tuple[str, str]] = [
    ("openai/gpt-5.6-sol", "current primary — 12 of 15 seats. Control."),
    ("anthropic/claude-opus-4.6", "current secondary — 3 of 15 seats."),
    ("x-ai/grok-4.6", "candidate. Dissented on the one item probed."),
    ("moonshotai/kimi-k3", "candidate. Dissented on the one item probed."),
    (
        "deepseek/deepseek-v4-pro",
        "candidate. Dissented hardest, and one call errored. Included because the "
        "dissent may be right: on a case whose only findings are vertigo, nystagmus "
        "and ataxia, 'probably inappropriate' for stroke-team activation is a position "
        "a clinician can hold. That is precisely what the sheet is for.",
    ),
    (
        "google/gemini-3.1-pro-preview",
        "negative control — echoed the primary. A model that agrees with everything "
        "restores seats and no discrimination, which is the failure hardest to see. "
        "Also a preview id, so unfit for an instrument regardless of how it scores.",
    ),
    ("z-ai/glm-5.2", "negative control — echoed the primary."),
]

# One fixed set of personas for every candidate, so a difference between two rows is a
# difference between models and not between roles. These are the three seats the roster
# actually swaps (`SECONDARY_MODEL_SEATS`), which keeps the arm size identical to
# production's secondary arm.
PERSONA_SEATS = sorted(panel_roster.SECONDARY_MODEL_SEATS)


def load_items(conn, limit: int) -> list[dict]:
    """Real items from live runs, spread across how much the panel agreed.

    Sampling across the agreement range matters: a bake-off run only on unanimous items
    cannot distinguish a well-calibrated candidate from one that agrees with everything,
    which is the specific confusion this is meant to resolve.
    """
    rows = conn.execute(
        sa.text("""
        select r.id run_id, r.case_version_id, r.item_label, r.item_snapshot,
               r.claim_hash, r.stem_version,
               (r.aggregates->>'modal_proportion')::float agreement,
               (r.aggregates->>'modal_rating')::int mode,
               v.primary_diagnosis, v.content_structured
        from authoring.panel_runs r
        join authoring.case_versions v on v.id = r.case_version_id
        where r.superseded_by is null and r.status = 'complete'
          and r.item_snapshot is not null
        order by r.id desc
    """)
    ).fetchall()

    seen: set[str] = set()
    unique = []
    for r in rows:
        key = f"{r.case_version_id}:{(r.item_label or '').strip().casefold()}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)

    # Spread the selection over the agreement range rather than taking the newest N.
    unique.sort(key=lambda r: (r.agreement if r.agreement is not None else 1.0))
    if len(unique) <= limit:
        chosen = unique
    else:
        step = len(unique) / limit
        chosen = [unique[int(i * step)] for i in range(limit)]

    items = []
    for r in chosen:
        struct = r.content_structured or {}
        if isinstance(struct, str):
            struct = json.loads(struct)
        orders = conn.execute(
            sa.text("""select order_text, suppression_synonyms
                       from authoring.case_final_orders where case_version_id = :v"""),
            {"v": r.case_version_id},
        ).fetchall()
        terms = [
            t for o in orders for t in [o.order_text, *(o.suppression_synonyms or [])]
        ]
        context = bc.build_oracle_context(struct, suppression_terms=terms)
        if context.is_empty:
            continue
        rendered = (r.item_snapshot or {}).get("rendered_item")
        if not rendered:
            continue
        items.append(
            {
                "run_id": r.run_id,
                "case_version_id": r.case_version_id,
                "item_label": r.item_label,
                "rendered_item": rendered,
                "context": context.text,
                "primary_diagnosis": r.primary_diagnosis,
                "panel_agreement": r.agreement,
                "panel_mode": r.mode,
            }
        )
    return items


def run_bakeoff(items, personas, concurrency: int) -> list[dict]:
    client = build_client(panel_runner.PANEL_REQUEST_TIMEOUT)
    jobs = [
        (item, model, persona)
        for item in items
        for model, _note in CANDIDATES
        for persona in personas
    ]

    def one(job):
        item, model, persona = job
        panelist = Panelist(
            index=persona.index,
            persona_id=persona.persona_id,
            role=persona.role,
            persona=persona.persona,
            model=model,
        )
        result = panel_runner._rate_once(
            client, panelist, item["context"], item["rendered_item"]
        )
        return {
            "run_id": item["run_id"],
            "item_label": item["item_label"],
            "model": model,
            "persona_id": persona.persona_id,
            "rating": (result.value or {}).get("rating"),
            "status": result.status,
            "rationale": result.rationale,
            "top_concerns": result.top_concerns,
            "error": result.error,
            "latency_ms": result.latency_ms,
        }

    out = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for i, res in enumerate(pool.map(one, jobs), 1):
            out.append(res)
            if i % 10 == 0 or i == len(jobs):
                print(f"  {i}/{len(jobs)} calls", flush=True)
    return out


def write_rating_sheet(items, path: Path) -> None:
    """Clinician-facing. Deliberately contains no model output whatsoever."""
    lines = [
        "# Panel model bake-off — clinician rating sheet",
        "",
        "Please rate each item **before** looking at any model output. The whole point of",
        "this exercise is to compare models against your judgement, and a rater who has",
        "seen the models' answers can no longer provide that comparison.",
        "",
        "Each item shows the case record exactly as the AI panel receives it: **the",
        "diagnosis is withheld from you as it is from them**, and any test currently under",
        "appropriateness review has been removed from the case's diagnostic workup list.",
        "",
        "Scale, as it appears in the item:",
        "",
        "```",
        "  +2 = clearly appropriate      +1 = probably appropriate",
        "   0 = equally appropriate or not",
        "  -1 = probably inappropriate   -2 = clearly inappropriate",
        "```",
        "",
        "For any rating that is not +2 or -2, a sentence on **why** is worth more to us",
        "than the number. We are trying to distinguish defensible clinical disagreement",
        "from a model simply being wrong, and only the reasoning tells us which is which.",
        "",
        "---",
        "",
    ]
    for n, item in enumerate(items, 1):
        lines += [
            f"## Item {n} — {item['item_label']}",
            "",
            "<details><summary>Case record (click to expand)</summary>",
            "",
            "```",
            item["context"],
            "```",
            "",
            "</details>",
            "",
            "**The question, as the panel is asked it:**",
            "",
            "```",
            item["rendered_item"],
            "```",
            "",
            "| | |",
            "|---|---|",
            "| Your rating (-2 to +2) | |",
            "| Why (one sentence, if not ±2) | |",
            "| Is this item worth using with learners? | |",
            "",
            "---",
            "",
        ]
    path.write_text("\n".join(lines))


def write_results(items, results, path: Path) -> None:
    by_item: dict[int, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for r in results:
        by_item[r["run_id"]][r["model"]].append(r)

    lines = [
        "# Panel model bake-off — model results",
        "",
        "**Score these against the completed clinician sheets, not against each other.**",
        "A candidate that disagrees with the other models is only useful if it agrees with",
        "clinicians; one that disagrees with clinicians manufactures spread that looks like",
        "discrimination while handing learners partial credit for wrong answers.",
        "",
        f"Personas held constant across every model: {', '.join(p.persona_id for p in PERSONAS)}.",
        "Same call path, schema, and reasoning effort as a production panel.",
        "",
        "## Candidates",
        "",
        "| model | note |",
        "|---|---|",
    ]
    for model, note in CANDIDATES:
        lines.append(f"| `{model}` | {note} |")
    lines += ["", "---", ""]

    for n, item in enumerate(items, 1):
        agreement = item["panel_agreement"]
        lines += [
            f"## Item {n} — {item['item_label']}",
            "",
            f"Production panel (15 seats): mode **{item['panel_mode']:+d}**, "
            f"agreement **{agreement:.0%}**. Ground truth (withheld from raters): "
            f"*{item['primary_diagnosis']}*.",
            "",
            "```",
            item["rendered_item"],
            "```",
            "",
            "| model | ratings | mean | vs primary |",
            "|---|---|---|---|",
        ]
        primary_mean = None
        for model, _ in CANDIDATES:
            rows = by_item[item["run_id"]].get(model, [])
            vals = [r["rating"] for r in rows if isinstance(r["rating"], int)]
            if not vals:
                lines.append(f"| `{model}` | all calls failed | — | — |")
                continue
            mean = statistics.mean(vals)
            if primary_mean is None:
                primary_mean = mean
                delta = "— (control)"
            else:
                d = mean - primary_mean
                delta = f"{d:+.2f}" + ("  **dissents**" if abs(d) >= 0.34 else "")
            shown = ", ".join(f"{v:+d}" for v in vals)
            failed = len(rows) - len(vals)
            suffix = f" ({failed} failed)" if failed else ""
            lines.append(f"| `{model}` | {shown}{suffix} | {mean:+.2f} | {delta} |")

        lines += ["", "<details><summary>Stated reasoning, per rater</summary>", ""]
        for model, _ in CANDIDATES:
            for r in by_item[item["run_id"]].get(model, []):
                if r["rationale"]:
                    rating = (
                        f"{r['rating']:+d}" if isinstance(r["rating"], int) else "—"
                    )
                    lines.append(
                        f"- **`{model}`** / {r['persona_id']} → **{rating}**: {r['rationale']}"
                    )
                elif r["status"] != "ok":
                    lines.append(
                        f"- **`{model}`** / {r['persona_id']} → *{r['status']}*: {r['error']}"
                    )
        lines += ["", "</details>", "", "---", ""]

    path.write_text("\n".join(lines))


PERSONAS: list = []


def main() -> int:
    global PERSONAS
    ap = argparse.ArgumentParser()
    ap.add_argument("--items", type=int, default=10)
    ap.add_argument("--out", default="bakeoff-output")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    url = os.environ.get("POSTGRES_URL_SIM_READY")
    if not url:
        print("POSTGRES_URL_SIM_READY is required", file=sys.stderr)
        return 1

    roster = panel_roster.build_roster()
    PERSONAS = [p for p in roster if p.index in PERSONA_SEATS]

    engine = sa.create_engine(url)
    with engine.connect() as conn:
        items = load_items(conn, args.items)

    calls = len(items) * len(CANDIDATES) * len(PERSONAS)
    print(
        f"{len(items)} items x {len(CANDIDATES)} models x {len(PERSONAS)} personas "
        f"= {calls} calls"
    )
    for i, item in enumerate(items, 1):
        print(
            f"  {i:>2}. {item['item_label']!r:34} panel agreement "
            f"{item['panel_agreement']:.0%}  (case_version {item['case_version_id']})"
        )
    if args.dry_run:
        return 0

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    print("\nrunning...")
    results = run_bakeoff(items, PERSONAS, args.concurrency)

    (out / "raw.json").write_text(
        json.dumps({"items": items, "results": results}, indent=2)
    )
    write_rating_sheet(items, out / "rating-sheet.md")
    write_results(items, results, out / "results.md")

    ok = sum(1 for r in results if r["status"] == "ok")
    print(f"\n{ok}/{len(results)} calls returned a rating")
    for model, _ in CANDIDATES:
        rows = [r for r in results if r["model"] == model]
        vals = [r["rating"] for r in rows if isinstance(r["rating"], int)]
        failed = len(rows) - len(vals)
        mean = f"{statistics.mean(vals):+.2f}" if vals else "—"
        print(
            f"  {model:32} n={len(vals):3} mean={mean}"
            + (f"  ({failed} failed)" if failed else "")
        )
    print(f"\nwrote {out}/rating-sheet.md, {out}/results.md, {out}/raw.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
