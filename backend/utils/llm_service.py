import openai
import json
import os
from typing import Dict, List, Any
from dotenv import load_dotenv
from backend.models.structured_outputs import (
    CaseDetailsStructured, 
    DiagnosticFrameworkStructured, 
    FeatureLikelihoodRatiosStructured
)

load_dotenv()

class LLMService:
    def __init__(self):
        self.client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    
    def generate_case_details(self, description: str, primary_diagnosis: str) -> CaseDetailsStructured:
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
        
        response = self.client.chat.completions.parse(
            model="gpt-4o-2024-08-06",
            messages=[
                {"role": "system", "content": "You are an expert emergency medicine physician and medical educator. Generate realistic, educational medical cases with proper clinical detail."},
                {"role": "user", "content": prompt}
            ],
            response_format=CaseDetailsStructured,
            temperature=0.7
        )
        
        return response.choices[0].message.parsed
    
    def generate_diagnostic_framework(self, case_details: CaseDetailsStructured, primary_diagnosis: str) -> DiagnosticFrameworkStructured:
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
        
        response = self.client.chat.completions.parse(
            model="gpt-4o-2024-08-06",
            messages=[
                {"role": "system", "content": "You are an expert emergency medicine physician with expertise in diagnostic reasoning and Bayesian probability. Create realistic diagnostic frameworks."},
                {"role": "user", "content": prompt}
            ],
            response_format=DiagnosticFrameworkStructured,
            temperature=0.7
        )
        
        return response.choices[0].message.parsed
    
    def generate_feature_likelihood_ratios(self, case_details: CaseDetailsStructured, diagnostic_framework: DiagnosticFrameworkStructured) -> FeatureLikelihoodRatiosStructured:
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
        
        response = self.client.chat.completions.parse(
            model="gpt-4o-2024-08-06",
            messages=[
                {"role": "system", "content": "You are an expert emergency medicine physician with expertise in evidence-based diagnosis and likelihood ratios. Generate realistic LRs based on medical literature."},
                {"role": "user", "content": prompt}
            ],
            response_format=FeatureLikelihoodRatiosStructured,
            temperature=0.7
        )
        
        return response.choices[0].message.parsed