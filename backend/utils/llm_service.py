import asyncio
import logging
import os

import openai
from dotenv import load_dotenv

from backend.models.structured_outputs import (
    CaseDetailsStructured,
    DiagnosticFrameworkStructured,
    FeatureLikelihoodRatiosStructured,
    SimReadyCaseDetailsStructured,
)

load_dotenv()

logger = logging.getLogger(__name__)

LLM_REQUEST_TIMEOUT = int(os.getenv("LLM_REQUEST_TIMEOUT", "120"))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))
LLM_RETRY_BASE_DELAY = float(os.getenv("LLM_RETRY_BASE_DELAY", "2.0"))


class LLMService:
    def __init__(self):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable is required")
        self.client = openai.OpenAI(
            api_key=api_key,
            timeout=LLM_REQUEST_TIMEOUT,
        )

    def _call_with_retry(self, parse_fn, description: str):
        """Call an OpenAI parse function with retry and exponential backoff."""
        last_exception = None
        for attempt in range(LLM_MAX_RETRIES):
            try:
                result = parse_fn()
                logger.info(
                    "LLM call succeeded: %s (attempt %d)", description, attempt + 1
                )
                return result
            except openai.RateLimitError as e:
                last_exception = e
                wait = LLM_RETRY_BASE_DELAY * (2**attempt)
                logger.warning(
                    "Rate limited on %s (attempt %d/%d), retrying in %.1fs",
                    description,
                    attempt + 1,
                    LLM_MAX_RETRIES,
                    wait,
                )
                import time

                time.sleep(wait)
            except openai.APITimeoutError as e:
                last_exception = e
                wait = LLM_RETRY_BASE_DELAY * (2**attempt)
                logger.warning(
                    "Timeout on %s (attempt %d/%d), retrying in %.1fs",
                    description,
                    attempt + 1,
                    LLM_MAX_RETRIES,
                    wait,
                )
                import time

                time.sleep(wait)
            except openai.APIConnectionError as e:
                last_exception = e
                wait = LLM_RETRY_BASE_DELAY * (2**attempt)
                logger.warning(
                    "Connection error on %s (attempt %d/%d), retrying in %.1fs",
                    description,
                    attempt + 1,
                    LLM_MAX_RETRIES,
                    wait,
                )
                import time

                time.sleep(wait)
            except openai.APIStatusError as e:
                # 5xx errors are retryable, 4xx (except 429) are not
                if e.status_code >= 500:
                    last_exception = e
                    wait = LLM_RETRY_BASE_DELAY * (2**attempt)
                    logger.warning(
                        "Server error %d on %s (attempt %d/%d), retrying in %.1fs",
                        e.status_code,
                        description,
                        attempt + 1,
                        LLM_MAX_RETRIES,
                        wait,
                    )
                    import time

                    time.sleep(wait)
                else:
                    logger.error(
                        "Non-retryable API error on %s: %s", description, str(e)
                    )
                    raise
            except Exception as e:
                logger.error("Unexpected error on %s: %s", description, str(e))
                raise

        logger.error("All %d retries exhausted for %s", LLM_MAX_RETRIES, description)
        raise last_exception

    def generate_case_details(
        self, description: str, primary_diagnosis: str
    ) -> CaseDetailsStructured:
        prompt = f"""
        Based on the following brief case description and primary diagnosis, generate a comprehensive medical case.

        Brief Description: {description}
        Primary Diagnosis: {primary_diagnosis}

        Create a realistic and educationally valuable case for emergency medicine training. Include:
        - A detailed case presentation with patient demographics, chief complaint, and initial presentation
        - Patient personality and communication style
        - At least 5-7 relevant history questions with expected patient responses
        - At least 5-6 physical examination findings
        - At least 4-5 diagnostic tests with clinical rationale
        """

        def _call():
            response = self.client.beta.chat.completions.parse(
                model="gpt-4o-2024-08-06",
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert emergency medicine physician and medical educator. Generate realistic, educational medical cases with proper clinical detail.",
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format=CaseDetailsStructured,
                temperature=0.7,
            )
            parsed = response.choices[0].message.parsed
            if parsed is None:
                raise ValueError("LLM returned empty parsed response for case details")
            return parsed

        return self._call_with_retry(_call, "generate_case_details")

    def generate_sim_ready_case_details(
        self, description: str, primary_diagnosis: str
    ) -> SimReadyCaseDetailsStructured:
        """Generate a simulator-ready case with rich clinical detail and legacy feature lists."""
        prompt = f"""
        Based on the following brief case description and primary diagnosis, generate a comprehensive
        simulator-ready medical case for emergency medicine training.

        Brief Description: {description}
        Primary Diagnosis: {primary_diagnosis}

        You must populate EVERY section described below:

        1. **Case Title** — a short descriptive title, e.g. "Chest Pain with ECG Changes".

        2. **Paragraph Summary** — a narrative paragraph summarizing the full case presentation
           including patient demographics, chief complaint, and clinical context.

        3. **Door Chart** — the information posted outside the patient's room:
           - Patient name, age, legal sex, chief complaint, clinical setting
           - Initial vital signs: blood pressure, pulse rate, respiratory rate,
             temperature (Celsius), and SpO2

        4. **History of Present Illness (HPI)** — use the OLDCARTS framework:
           Onset, Location, Duration, Character, Aggravating/Alleviating factors,
           Radiation, Timing, Severity, and any additional associated details.

        5. **Past Medical History (PMHx)** — active problems, inactive problems,
           hospitalizations, surgical history, immunizations.

        6. **Social History (SHx)** — tobacco, alcohol, substances, diet, exercise,
           sexual activity, home life/safety, mood, and contextual details.

        7. **Family History (FHx)** — parents and siblings.

        8. **Medications & Allergies** — current medications with dosages and known allergies.

        9. **Review of Systems (ROS)** — pertinent positive and negative findings.

        10. **Physical Exam Findings (narrative)** — a full narrative paragraph of physical exam findings.

        11. **Diagnostic Reasoning** — essential HPI details the learner should elicit,
            a comma-separated differential diagnosis list, and clinical rationale linking
            the presentation to the differential.

        12. **Teaching Points** — key learning objectives and educational content/clinical pearls.

        13. **Patient Approach** — the patient's education level, emotional response during
            the encounter, and communication style (for simulation acting guidance).

        14. **Structured Feature Lists for Downstream Bayesian Analysis** — these lists feed
            a likelihood-ratio pipeline and must be thorough:
            - **history_questions**: At least 5-7 specific history questions with the expected
              patient response for each.
            - **physical_exam_findings**: At least 5-6 physical examination maneuvers/components
              with expected findings.
            - **diagnostic_workup**: At least 4-5 diagnostic tests with clinical rationale for
              ordering each.

        Make the case realistic, clinically accurate, and educationally valuable.
        """

        def _call():
            response = self.client.beta.chat.completions.parse(
                model="gpt-4o-2024-08-06",
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert emergency medicine physician and medical educator. Generate realistic, simulator-ready medical cases with comprehensive clinical detail.",
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format=SimReadyCaseDetailsStructured,
                temperature=0.7,
            )
            parsed = response.choices[0].message.parsed
            if parsed is None:
                raise ValueError(
                    "LLM returned empty parsed response for sim-ready case details"
                )
            return parsed

        return self._call_with_retry(_call, "generate_sim_ready_case_details")

    def _sim_ready_to_case_details(
        self, sim_ready: SimReadyCaseDetailsStructured
    ) -> CaseDetailsStructured:
        """Adapter to allow existing framework/LR methods to work with sim-ready data."""
        return CaseDetailsStructured(
            presentation=sim_ready.paragraph_summary,
            patient_personality=sim_ready.patient_approach.communication_style,
            history_questions=sim_ready.history_questions,
            physical_exam_findings=sim_ready.physical_exam_findings,
            diagnostic_workup=sim_ready.diagnostic_workup,
        )

    def generate_diagnostic_framework(
        self, case_details: CaseDetailsStructured, primary_diagnosis: str
    ) -> DiagnosticFrameworkStructured:
        prompt = f"""
        Based on the following case details and primary diagnosis, create a tiered diagnostic framework with 3 tiers of progressively refined diagnostic categories.

        Primary Diagnosis: {primary_diagnosis}
        Case Presentation: {case_details.presentation}

        Generate 3 tiers of diagnostic buckets:
        - Tier 1: Broad categories (e.g., cardiovascular, respiratory, gastrointestinal, neurological, infectious)
        - Tier 2: More specific categories within the broad categories
        - Tier 3: Very specific diagnostic possibilities

        For each tier, provide realistic a priori probability distributions that sum to 1.0. The probabilities should reflect what an emergency physician might expect in a typical ED population, with the primary diagnosis having higher probability in the appropriate tier.

        Each tier should have 4-6 diagnostic buckets with meaningful clinical distinctions. For the a_priori_probabilities, create a list where each entry has the bucket_name matching exactly one of the bucket names, and its corresponding probability.
        """

        def _call():
            response = self.client.beta.chat.completions.parse(
                model="gpt-4o-2024-08-06",
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert emergency medicine physician with expertise in diagnostic reasoning and Bayesian probability. Create realistic diagnostic frameworks.",
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format=DiagnosticFrameworkStructured,
                temperature=0.7,
            )
            parsed = response.choices[0].message.parsed
            if parsed is None:
                raise ValueError(
                    "LLM returned empty parsed response for diagnostic framework"
                )
            return parsed

        return self._call_with_retry(_call, "generate_diagnostic_framework")

    def generate_feature_likelihood_ratios(
        self,
        case_details: CaseDetailsStructured,
        diagnostic_framework: DiagnosticFrameworkStructured,
    ) -> FeatureLikelihoodRatiosStructured:
        # Build feature list from case details
        features_summary = []
        for hq in case_details.history_questions:
            features_summary.append(f"History: {hq.question}")
        for pef in case_details.physical_exam_findings:
            features_summary.append(f"Physical: {pef.examination}")
        for dt in case_details.diagnostic_workup:
            features_summary.append(f"Diagnostic: {dt.test}")

        # Build diagnostic buckets summary
        buckets_summary = []
        for tier in diagnostic_framework.tiers:
            for bucket in tier.buckets:
                buckets_summary.append(f"Tier {tier.tier_level}: {bucket.name}")

        prompt = f"""
        Generate feature likelihood ratios for this medical case based on evidence-based medicine.

        Available Features:
        {chr(10).join(features_summary)}

        Available Diagnostic Buckets:
        {chr(10).join(buckets_summary)}

        For each feature, generate likelihood ratios for the most relevant diagnostic buckets across different tiers. Focus on:
        - Clinically meaningful likelihood ratios (avoid ratios too close to 1.0)
        - Evidence-based values when possible
        - Each feature should have LRs for 2-4 relevant diagnostic buckets
        - Include features from all categories: history, physical_exam, diagnostic_workup

        Use realistic likelihood ratios:
        - Strong positive predictors: LR 5-10+
        - Moderate positive predictors: LR 2-5
        - Weak positive predictors: LR 1.2-2
        - Weak negative predictors: LR 0.5-0.8
        - Strong negative predictors: LR 0.1-0.5
        """

        def _call():
            response = self.client.beta.chat.completions.parse(
                model="gpt-4o-2024-08-06",
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert emergency medicine physician with expertise in evidence-based diagnosis and likelihood ratios. Generate realistic LRs based on medical literature.",
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format=FeatureLikelihoodRatiosStructured,
                temperature=0.7,
            )
            parsed = response.choices[0].message.parsed
            if parsed is None:
                raise ValueError("LLM returned empty parsed response for feature LRs")
            return parsed

        return self._call_with_retry(_call, "generate_feature_likelihood_ratios")

    async def generate_case_details_async(
        self, description: str, primary_diagnosis: str
    ) -> CaseDetailsStructured:
        """Async wrapper for generate_case_details."""
        return await asyncio.to_thread(
            self.generate_case_details, description, primary_diagnosis
        )

    async def generate_sim_ready_case_details_async(
        self, description: str, primary_diagnosis: str
    ) -> SimReadyCaseDetailsStructured:
        """Async wrapper for generate_sim_ready_case_details."""
        return await asyncio.to_thread(
            self.generate_sim_ready_case_details, description, primary_diagnosis
        )

    async def generate_diagnostic_framework_async(
        self, case_details: CaseDetailsStructured, primary_diagnosis: str
    ) -> DiagnosticFrameworkStructured:
        """Async wrapper for generate_diagnostic_framework."""
        return await asyncio.to_thread(
            self.generate_diagnostic_framework, case_details, primary_diagnosis
        )

    async def generate_feature_likelihood_ratios_async(
        self,
        case_details: CaseDetailsStructured,
        diagnostic_framework: DiagnosticFrameworkStructured,
    ) -> FeatureLikelihoodRatiosStructured:
        """Async wrapper for generate_feature_likelihood_ratios."""
        return await asyncio.to_thread(
            self.generate_feature_likelihood_ratios, case_details, diagnostic_framework
        )
