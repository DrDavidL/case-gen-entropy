"""One place that decides which provider every LLM call goes to.

Both call paths — case generation and the Oracle panel — build their client here so the
provider is a single decision rather than a setting duplicated in two modules that can
drift apart.

**OpenRouter is the default and there is deliberately no silent fallback to the OpenAI
API.** The OpenRouter key carries zero-data-retention, which the direct OpenAI key does
not. A fallback that quietly switched providers when a variable was missing would
silently change the retention posture of student-adjacent content, and it would do it at
exactly the moment nobody was watching — a fresh deploy. Missing configuration fails
loudly instead.

Set `LLM_PROVIDER=openai` to deliberately use the OpenAI API directly. That is an
explicit choice, made once, and visible in the environment.
"""

import logging
import os

import openai
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openrouter").strip().lower()
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

# Model ids are provider-prefixed on OpenRouter ("openai/gpt-4o-2024-08-06"). The bare
# OpenAI ids do not resolve there, so these defaults change with the provider.
CASE_GEN_MODEL = os.getenv("CASE_GEN_MODEL", "openai/gpt-4o-2024-08-06")

# Verified present on OpenRouter 2026-07-30, with structured_outputs and reasoning
# support. Note that a bare "openai/gpt-5.6" does NOT exist — the 5.6 line is published
# as -luna / -sol / -terra variants.
ORACLE_MODEL = os.getenv("ORACLE_MODEL", "openai/gpt-5.6-sol")


def _openrouter_headers() -> dict[str, str]:
    """Attribution headers. Optional for OpenRouter, useful on its usage dashboard."""
    return {
        "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "https://github.com/DrDavidL"),
        "X-Title": os.getenv("OPENROUTER_APP_TITLE", "Medical Case Generator"),
    }


def build_client(timeout: int) -> openai.OpenAI:
    """An OpenAI-SDK client pointed at the configured provider.

    OpenRouter is wire-compatible with the Chat Completions API, so the same SDK and the
    same `.parse()` structured-output helper work against both.
    """
    if LLM_PROVIDER == "openrouter":
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError(
                "OPENROUTER_API_KEY is required when LLM_PROVIDER=openrouter (the "
                "default). Set it, or set LLM_PROVIDER=openai to deliberately use the "
                "OpenAI API directly — note that the direct key does not carry the "
                "zero-data-retention setting configured on the OpenRouter key."
            )
        return openai.OpenAI(
            api_key=api_key,
            base_url=OPENROUTER_BASE_URL,
            timeout=timeout,
            default_headers=_openrouter_headers(),
        )

    if LLM_PROVIDER == "openai":
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
        return openai.OpenAI(api_key=api_key, timeout=timeout)

    raise ValueError(
        f"LLM_PROVIDER must be 'openrouter' or 'openai', got {LLM_PROVIDER!r}"
    )


def provider_name() -> str:
    return LLM_PROVIDER


def describe_provider() -> dict[str, str]:
    """Provider identity for health checks and for stamping onto panel runs."""
    return {
        "provider": LLM_PROVIDER,
        "base_url": OPENROUTER_BASE_URL if LLM_PROVIDER == "openrouter" else "openai",
        "case_gen_model": CASE_GEN_MODEL,
        "oracle_model": ORACLE_MODEL,
    }
