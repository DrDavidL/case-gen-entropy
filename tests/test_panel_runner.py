"""How a panelist can fail, and what the run is allowed to say about it.

Every test here fakes the client. A panel test that reaches OpenRouter would be testing
OpenRouter, would cost money, and would fail for reasons that have nothing to do with
this repo.

The distinction under test is not cosmetic. A truncated response and a refusal produce
the same missing rating and demand opposite responses: re-run the panel, or rewrite the
item. On 2026-08-12 the code called both `api_error`, and the resulting investigation
went looking for a refusal that had never happened.
"""

import httpx
import openai
import pytest

from backend.models.structured_outputs import OracleRatingStructured
from backend.utils import panel_runner
from backend.utils.panel_roster import Panelist

PANELIST = Panelist(
    index=0,
    persona_id="em_stewardship",
    role="Emergency physician",
    persona="You are an emergency physician.",
    model="openai/gpt-5.6-sol",
)


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch):
    """Retries are under test; waiting for them is not."""
    monkeypatch.setattr(panel_runner, "PANEL_RETRY_BASE_DELAY", 0.0)
    monkeypatch.setattr(panel_runner.time, "sleep", lambda _seconds: None)


class FakeCompletions:
    """Replays a scripted sequence of outcomes, one per call.

    An element is either an exception to raise or a response object to return, so a test
    can express "fails twice, then succeeds" directly.
    """

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.kwargs = []

    def parse(self, **kwargs):
        self.calls += 1
        self.kwargs.append(kwargs)
        outcome = self.script.pop(0) if self.script else self.script_exhausted()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def script_exhausted(self):
        raise AssertionError(f"client called {self.calls} times, more than the script")


class FakeClient:
    def __init__(self, script):
        self.completions = FakeCompletions(script)
        self.beta = self
        self.chat = self


def message(*, parsed=None, refusal=None):
    class Message:
        pass

    m = Message()
    m.parsed = parsed
    m.refusal = refusal
    return m


def response(*, parsed=None, refusal=None, response_id="resp_1"):
    class Response:
        pass

    class Usage:
        prompt_tokens = 1200
        completion_tokens = 90

    r = Response()
    r.id = response_id
    r.usage = Usage()
    r.choices = [
        type("Choice", (), {"message": message(parsed=parsed, refusal=refusal)})()
    ]
    return r


def rating(value: int):
    return OracleRatingStructured(
        rating=value,
        reasoning="Two sentences of justification.",
        top_diagnostic_concerns=["stroke", "seizure"],
    )


def truncated_json_error():
    """The real 2026-08-12 failure: schema-conforming JSON, cut mid-string."""
    try:
        OracleRatingStructured.model_validate_json(
            '{"rating":2,"reasoning":"Posterior circulation stroke can lack'
        )
    except Exception as e:  # noqa: BLE001 — capturing it is the point
        return e
    raise AssertionError("expected the truncated payload to fail validation")


def rate(script):
    client = FakeClient(script)
    result = panel_runner._rate_once(
        client, PANELIST, "blinded context", "rendered item"
    )
    return result, client.completions


def test_a_usable_rating_is_recorded_with_its_provenance():
    result, calls = rate([response(parsed=rating(2))])

    assert result.status == "ok"
    assert result.value == {"rating": 2}
    assert result.model == "openai/gpt-5.6-sol"
    assert result.persona_id == "em_stewardship"
    assert result.top_concerns == ["stroke", "seizure"]
    assert result.tokens_in == 1200 and result.tokens_out == 90
    assert result.latency_ms is not None
    assert calls.calls == 1


def test_a_truncated_response_is_retried_and_can_succeed():
    """The fix. This used to cost the seat outright on the first cut."""
    result, calls = rate([truncated_json_error(), response(parsed=rating(2))])

    assert result.status == "ok"
    assert result.value == {"rating": 2}
    assert calls.calls == 2


def test_a_truncated_response_that_never_recovers_is_not_called_a_refusal():
    result, calls = rate([truncated_json_error()] * panel_runner.PANEL_MAX_RETRIES)

    assert result.status == "truncated"
    assert calls.calls == panel_runner.PANEL_MAX_RETRIES
    assert "EOF while parsing" in result.error
    assert result.value is None


def test_an_empty_response_body_is_retried():
    """The SDK raises from inside itself when choices/message is None."""
    result, calls = rate(
        [TypeError("'NoneType' object is not iterable"), response(parsed=rating(-1))]
    )

    assert result.status == "ok"
    assert result.value == {"rating": -1}
    assert calls.calls == 2


def test_an_empty_response_that_never_recovers_is_labelled_as_such():
    result, _ = rate([TypeError("'NoneType' object is not iterable")] * 3)

    assert result.status == "empty_response"


def test_a_refusal_is_recorded_as_a_refusal_and_not_retried():
    result, calls = rate([response(parsed=None, refusal="I cannot help with that.")])

    assert result.status == "refusal"
    assert result.error == "I cannot help with that."
    assert calls.calls == 1


def test_a_moderation_block_is_terminal_and_named():
    result, calls = rate([openai.ContentFilterFinishReasonError()])

    assert result.status == "content_filter"
    # Retrying identical input reproduces it; spending three calls to learn that is waste.
    assert calls.calls == 1


def test_an_empty_parse_without_a_refusal_is_a_parse_error():
    result, _ = rate([response(parsed=None, refusal=None)])

    assert result.status == "parse_error"
    assert "no parsed output" in result.error


def test_a_rating_outside_the_scale_is_rejected_rather_than_coerced():
    result, calls = rate([response(parsed=rating(5))])

    assert result.status == "parse_error"
    assert "outside the -2..+2 scale" in result.error
    assert result.value is None
    # The reasoning is still kept: it is evidence about why the model went off-scale.
    assert result.rationale == "Two sentences of justification."
    assert calls.calls == 1


def test_a_4xx_gives_up_immediately():
    """An unknown model id fails identically for all 15 seats. Burning three retries
    each turns a configuration error into a slow one."""
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    error = openai.APIStatusError(
        "not found", response=httpx.Response(404, request=request), body=None
    )

    result, calls = rate([error])

    assert result.status == "api_error"
    assert "HTTP 404" in result.error
    assert calls.calls == 1


def test_a_transient_api_error_is_retried_then_reported():
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    timeout = openai.APITimeoutError(request=request)

    result, calls = rate([timeout, timeout, timeout])

    assert result.status == "api_error"
    assert calls.calls == 3


def test_a_rate_limit_that_clears_produces_a_rating():
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    limited = openai.RateLimitError(
        "slow down", response=httpx.Response(429, request=request), body=None
    )

    result, calls = rate([limited, response(parsed=rating(0))])

    assert result.status == "ok"
    assert calls.calls == 2


def test_every_call_carries_the_schema_and_the_reasoning_effort():
    """Both are load-bearing: the strict schema is what makes the output parseable at
    all, and reasoning effort is the setting the research group specified (ADR-014)."""
    _, calls = rate([response(parsed=rating(1))])
    kwargs = calls.kwargs[0]

    assert kwargs["response_format"] is OracleRatingStructured
    assert (
        kwargs["extra_body"]["reasoning"]["effort"]
        == panel_runner.ORACLE_REASONING_EFFORT
    )
    # Without this, a provider that ignores the schema can be routed to silently.
    assert kwargs["extra_body"]["provider"]["require_parameters"] is True
    assert kwargs["model"] == "openai/gpt-5.6-sol"


def test_the_persona_and_the_item_both_reach_the_model():
    _, calls = rate([response(parsed=rating(1))])
    system, user = calls.kwargs[0]["messages"]

    assert PANELIST.persona in system["content"]
    # The shared rater instruction rides with every persona, not just the first.
    assert "Weigh the cost and risk of commission" in system["content"]
    assert "blinded context" in user["content"]
    assert "rendered item" in user["content"]
    assert "diagnosis has been withheld" in user["content"]


def test_a_failing_panelist_never_raises_into_the_run():
    """One bad seat must not take the other fourteen with it."""
    result, _ = rate([RuntimeError("something nobody anticipated")])

    assert result.status == "api_error"
    assert result.panelist_index == 0
