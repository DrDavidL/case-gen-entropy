"""Shared LLM panel runner: fan one claim out to N independent blinded raters.

Serves the Oracle today and LR re-assessment later through the same contract
(ADR-006). Invariants the runner enforces regardless of consumer:

- **One call per (claim × panelist).** Never batch several claims into one response.
  Rating five orders in one call makes the model calibrate the third against what it
  just said about the first two, which makes the per-item distributions statistically
  dependent and therefore uninterpretable on their own. Five times the calls, still
  inexpensive. Cory's 2026-07-29 review made independence an explicit condition of
  approving the roster.
- **Realized N is recorded separately from requested N.** A call that fails after
  retries becomes a null-outcome row, excluded from the denominator.
- **Per-call rows are the source of truth.** Aggregates are derived.

Calls go through OpenRouter (`backend/utils/llm_client.py`), which is wire-compatible
with Chat Completions, so the panel and the generation pipeline share one SDK and one
structured-output helper. Reasoning effort — the setting Cory specified — rides along as
OpenRouter's unified `reasoning` parameter via `extra_body`, which is why this does not
need the OpenAI-only Responses API.
"""

import asyncio
import hashlib
import logging
import os
import time

import openai
from dotenv import load_dotenv
from pydantic import BaseModel

from backend.models.structured_outputs import OracleRatingStructured
from backend.utils.llm_client import ORACLE_MODEL, build_client, provider_name
from backend.utils.panel_roster import Panelist

load_dotenv()

logger = logging.getLogger(__name__)

# "medium" is Cory's "fourth notch, not the highest", confirmed 2026-07-29 (ADR-014).
ORACLE_REASONING_EFFORT = os.getenv("ORACLE_REASONING_EFFORT", "medium")

# Sequential execution would take roughly half an hour per case.
ORACLE_CONCURRENCY = int(os.getenv("ORACLE_CONCURRENCY", "8"))

PANEL_REQUEST_TIMEOUT = int(os.getenv("PANEL_REQUEST_TIMEOUT", "180"))
PANEL_MAX_RETRIES = int(os.getenv("PANEL_MAX_RETRIES", "3"))
PANEL_RETRY_BASE_DELAY = float(os.getenv("PANEL_RETRY_BASE_DELAY", "2.0"))

PROMPT_TEMPLATE_VERSION = "oracle-v1"
VALID_RATINGS = frozenset({-2, -1, 0, 1, 2})


# The shared rater instruction. Versioned with PROMPT_TEMPLATE_VERSION — changing this
# text changes the instrument, so bump the version when it changes.
#
# The cost-of-commission paragraph is a direct requirement from Cory's 2026-07-29
# review: "panel should consider costs of co-mission (e.g. brain biopsy comes at a great
# cost so strength of clinical justification must have real weight)".
RATER_INSTRUCTION = """\
You are serving on a reference panel rating the appropriateness of a single clinical \
action for one patient encounter.

How to rate:

- You are NOT told the patient's final diagnosis. Rate on the clinical information given.
- Rate ONLY the single action presented. Do not consider any other action that might be \
appropriate for this patient, and do not calibrate your answer against orders you are \
not being asked about.
- Weigh the cost and risk of commission, not only the risk of missing something. Actions \
differ enormously in burden: a brain biopsy, an invasive procedure, a study carrying \
meaningful radiation, contrast, or sedation risk, and any test likely to start a cascade \
of further testing all impose real harm and cost. The higher the burden of the action, \
the stronger the clinical justification must be for it to be appropriate. A \
high-burden action supported only by weak justification is not appropriate.
- Answer from your own stated practice perspective. Reasonable clinicians genuinely \
disagree about appropriateness, and your perspective is why you are on this panel. Do \
not try to guess a consensus answer.

Return:
- rating: one of the integers -2, -1, 0, 1, or 2, exactly as anchored in the item.
- reasoning: 2-3 sentences justifying your rating.
- top_diagnostic_concerns: the two or three diagnoses you are most concerned about in \
this patient, most concerning first.
"""


class PanelCallResult(BaseModel):
    panelist_index: int
    persona_id: str
    persona_hash: str
    value: dict | None = None
    rationale: str | None = None
    top_concerns: list[str] | None = None
    status: str = "ok"  # ok | parse_error | refusal | api_error
    error: str | None = None
    raw_response_id: str | None = None
    latency_ms: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None


def claim_hash(claim: str) -> str:
    return hashlib.sha256(claim.encode("utf-8")).hexdigest()[:16]


def describe_settings() -> dict[str, object]:
    """Report the provider settings a run would use. Surfaced by the status endpoint."""
    return {
        "model": ORACLE_MODEL,
        "reasoning_effort": ORACLE_REASONING_EFFORT,
        "provider": provider_name(),
        "api_surface": "chat.completions",
        "concurrency": ORACLE_CONCURRENCY,
        "prompt_template_version": PROMPT_TEMPLATE_VERSION,
    }


def _build_user_prompt(blinded_context: str, rendered_item: str) -> str:
    return (
        "## Case record (the diagnosis has been withheld from you)\n\n"
        f"{blinded_context}\n\n"
        "## Item\n\n"
        f"{rendered_item}\n"
    )


def _rate_once(
    client: openai.OpenAI,
    panelist: Panelist,
    blinded_context: str,
    rendered_item: str,
) -> PanelCallResult:
    """One panelist, one claim. Retries transient failures; never raises."""
    system = f"{panelist.persona}\n\n{RATER_INSTRUCTION}"
    user = _build_user_prompt(blinded_context, rendered_item)

    base = {
        "panelist_index": panelist.index,
        "persona_id": panelist.persona_id,
        "persona_hash": panelist.persona_hash,
    }

    last_error: str | None = None
    for attempt in range(PANEL_MAX_RETRIES):
        started = time.monotonic()
        try:
            response = client.beta.chat.completions.parse(
                model=ORACLE_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format=OracleRatingStructured,
                # OpenRouter's unified reasoning parameter. Passed via extra_body because
                # the OpenAI SDK has no field for it on Chat Completions; OpenRouter
                # translates it to whatever the underlying provider expects.
                extra_body={"reasoning": {"effort": ORACLE_REASONING_EFFORT}},
            )
            latency_ms = int((time.monotonic() - started) * 1000)

            usage = getattr(response, "usage", None)
            tokens_in = getattr(usage, "prompt_tokens", None) if usage else None
            tokens_out = getattr(usage, "completion_tokens", None) if usage else None

            message = response.choices[0].message
            parsed = getattr(message, "parsed", None)
            if parsed is None:
                # A refusal and an empty parse are different facts about the run and are
                # recorded differently; both are excluded from the denominator.
                refusal = getattr(message, "refusal", None)
                return PanelCallResult(
                    **base,
                    status="refusal" if refusal else "parse_error",
                    error=refusal or "model returned no parsed output",
                    raw_response_id=getattr(response, "id", None),
                    latency_ms=latency_ms,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                )

            if parsed.rating not in VALID_RATINGS:
                return PanelCallResult(
                    **base,
                    status="parse_error",
                    error=f"rating {parsed.rating} outside the -2..+2 scale",
                    rationale=parsed.reasoning,
                    top_concerns=parsed.top_diagnostic_concerns,
                    raw_response_id=getattr(response, "id", None),
                    latency_ms=latency_ms,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                )

            return PanelCallResult(
                **base,
                value={"rating": parsed.rating},
                rationale=parsed.reasoning,
                top_concerns=parsed.top_diagnostic_concerns,
                status="ok",
                raw_response_id=getattr(response, "id", None),
                latency_ms=latency_ms,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )

        except (
            openai.RateLimitError,
            openai.APITimeoutError,
            openai.APIConnectionError,
        ) as e:
            last_error = f"{type(e).__name__}: {str(e)[:200]}"
        except openai.APIStatusError as e:
            last_error = f"HTTP {e.status_code}: {str(e)[:200]}"
            if e.status_code < 500:
                # 4xx will not fix itself. An unknown model id lands here, so say so
                # loudly rather than burning three retries per panelist on it.
                logger.error(
                    "Panelist %d: non-retryable API error, giving up: %s",
                    panelist.index,
                    last_error,
                )
                break
        except Exception as e:  # noqa: BLE001 — one bad panelist must not kill the panel
            last_error = f"{type(e).__name__}: {str(e)[:200]}"
            logger.exception("Panelist %d: unexpected error", panelist.index)
            break

        if attempt < PANEL_MAX_RETRIES - 1:
            wait = PANEL_RETRY_BASE_DELAY * (2**attempt)
            logger.warning(
                "Panelist %d failed (attempt %d/%d), retrying in %.1fs: %s",
                panelist.index,
                attempt + 1,
                PANEL_MAX_RETRIES,
                wait,
                last_error,
            )
            time.sleep(wait)

    return PanelCallResult(**base, status="api_error", error=last_error)


async def run_panel(
    *,
    roster: list[Panelist],
    blinded_context: str,
    rendered_item: str,
) -> list[PanelCallResult]:
    """Rate one claim across the whole roster concurrently.

    Always returns one result per panelist, in roster order. Failures are results with a
    non-ok status, not exceptions and not gaps — the caller needs to record that a
    panelist was asked and did not answer.
    """
    client = build_client(PANEL_REQUEST_TIMEOUT)
    semaphore = asyncio.Semaphore(max(1, ORACLE_CONCURRENCY))

    async def one(panelist: Panelist) -> PanelCallResult:
        async with semaphore:
            return await asyncio.to_thread(
                _rate_once, client, panelist, blinded_context, rendered_item
            )

    results = await asyncio.gather(*(one(p) for p in roster))
    ok = sum(1 for r in results if r.status == "ok")
    logger.info(
        "Panel complete: %d/%d usable ratings (model=%s effort=%s)",
        ok,
        len(roster),
        ORACLE_MODEL,
        ORACLE_REASONING_EFFORT,
    )
    return list(results)
