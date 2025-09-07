import pandas as pd
import numpy as np
from typing import Dict, List, Any, Optional, Iterable
from io import StringIO, BytesIO
import re
import difflib
import logging

def create_feature_lr_matrix(
    case_details: Dict[str, Any],
    diagnostic_framework: List[Dict[str, Any]],
    feature_likelihood_ratios: List[Dict[str, Any]],
    tier_level: Optional[int] = None,
    strict: bool = True,
) -> pd.DataFrame:
    logger = logging.getLogger(__name__)
    """
    Create a feature-LR matrix in the format expected by the simulator app.
    
    Format expected:
    - First column: Clinical features (e.g., "Patient Has: chest pain")
    - Remaining columns: Diagnostic categories with LR values
    """
    
    # Determine diagnostic buckets to include
    diagnostic_buckets_display: List[str] = []
    if tier_level is not None:
        # Use only the buckets from the requested tier
        selected_tier = next((t for t in diagnostic_framework if t.get('tier_level') == tier_level), None)
        if not selected_tier and diagnostic_framework:
            # Fallback to first tier if requested not found
            selected_tier = diagnostic_framework[0]
        if selected_tier:
            diagnostic_buckets_display = [b.get('name') for b in selected_tier.get('buckets', []) if b.get('name')]
    # If no tier specified or no buckets found, fall back to union of all tiers
    if not diagnostic_buckets_display:
        seen = set()
        for tier in diagnostic_framework:
            for bucket in tier.get('buckets', []):
                name = bucket.get('name')
                if name and name not in seen:
                    seen.add(name)
                    diagnostic_buckets_display.append(name)

    # Build normalization map for robust matching (case/whitespace-insensitive)
    def _norm(s: str) -> str:
        """Normalize bucket names for robust matching.
        - lowercase
        - trim
        - strip optional 'tier X:' prefixes
        - collapse internal whitespace
        """
        s = (s or "").strip().lower()
        # remove leading 'tier <num>:' pattern
        s = re.sub(r"^tier\s*\d+\s*:\s*", "", s)
        # collapse multiple spaces
        s = re.sub(r"\s+", " ", s)
        return s

    bucket_norm_to_display = {_norm(name): name for name in diagnostic_buckets_display}

    # Also build a union map across all tiers for cross-tier projection if needed
    union_bucket_norm_to_display: Dict[str, str] = {}
    for tier in diagnostic_framework:
        for b in tier.get('buckets', []):
            name = b.get('name') if isinstance(b, dict) else None
            if name:
                union_bucket_norm_to_display[_norm(name)] = name

    def _closest(target: str, candidates: Iterable[str], cutoff: float = 0.5) -> Optional[str]:
        """Return closest candidate key to target using fuzzy and token overlap."""
        cand_list = list(candidates)
        if not cand_list:
            return None
        # First try difflib
        best = difflib.get_close_matches(target, cand_list, n=1, cutoff=cutoff)
        if best:
            return best[0]
        # Token overlap (Jaccard)
        tset = set(target.split())
        best_key = None
        best_score = 0.0
        for c in cand_list:
            cset = set(c.split())
            if not tset or not cset:
                continue
            score = len(tset & cset) / len(tset | cset)
            if score > best_score:
                best_score = score
                best_key = c
        return best_key if best_score >= cutoff else None
    
    # Create feature mapping based on actual LR data, not case details
    # This ensures we only include features that have LR values
    feature_lr_map = {}
    
    # Optionally filter LR entries to the selected tier to reduce mismatches
    # Only filter by tier_level if LR entries actually carry that field
    if tier_level is not None and any('tier_level' in lr for lr in feature_likelihood_ratios):
        flrs_iter = [lr for lr in feature_likelihood_ratios if lr.get('tier_level') == tier_level]
    else:
        flrs_iter = feature_likelihood_ratios

    # Build the feature-LR mapping from the actual LR data
    for lr in flrs_iter:
        feature_name = lr['feature_name']
        diagnostic_bucket = lr['diagnostic_bucket']
        lr_value = lr['likelihood_ratio']
        
        # Create a standardized feature name
        if lr['feature_category'] == 'history':
            standardized_feature = f"Patient Has: {feature_name.lower().replace('history:', '').replace('question:', '').strip()}"
        elif lr['feature_category'] == 'physical_exam':
            standardized_feature = f"Physical Finding: {feature_name.lower().replace('physical exam:', '').replace('examination:', '').replace('physical:', '').strip()}"
        elif lr['feature_category'] == 'diagnostic_workup':
            standardized_feature = f"Test Result: {feature_name.lower().replace('diagnostic test:', '').replace('test:', '').replace('diagnostic:', '').strip()}"
        else:
            standardized_feature = f"Clinical Feature: {feature_name.strip()}"
        
        # Initialize feature if not exists
        if standardized_feature not in feature_lr_map:
            feature_lr_map[standardized_feature] = {}
            # Initialize all buckets to 1.0 for this feature
            for bucket_name in diagnostic_buckets_display:
                feature_lr_map[standardized_feature][bucket_name] = 1.0
        
        # Set the actual LR value
        norm_bucket = _norm(diagnostic_bucket)
        display_bucket = None
        if norm_bucket in bucket_norm_to_display:
            display_bucket = bucket_norm_to_display[norm_bucket]
            # Optional: debug exact match
            logger.debug(
                "LR bucket exact match", extra={
                    "diagnostic_bucket": diagnostic_bucket,
                    "matched_bucket": display_bucket,
                }
            )
        else:
            # Fuzzy match to closest bucket name within the selected tier's buckets
            if not strict and bucket_norm_to_display:
                ck = _closest(norm_bucket, bucket_norm_to_display.keys(), cutoff=0.5)
                if ck:
                    display_bucket = bucket_norm_to_display[ck]
                    logger.info(
                        "Fuzzy-mapped LR bucket (within tier)",
                        extra={
                            "original_bucket": diagnostic_bucket,
                            "normalized": norm_bucket,
                            "mapped_bucket": display_bucket,
                        },
                    )
            # Cross-tier projection: map to closest bucket across all tiers, then project to selected tier
            if not strict and not display_bucket and union_bucket_norm_to_display:
                uk = _closest(norm_bucket, union_bucket_norm_to_display.keys(), cutoff=0.5)
                if uk:
                    union_display = union_bucket_norm_to_display[uk]
                    # If the union bucket exists in selected set, use directly
                    if _norm(union_display) in bucket_norm_to_display:
                        display_bucket = bucket_norm_to_display[_norm(union_display)]
                    else:
                        # Project union bucket to the closest selected bucket
                        proj_key = _closest(_norm(union_display), bucket_norm_to_display.keys(), cutoff=0.4)
                        if proj_key:
                            display_bucket = bucket_norm_to_display[proj_key]
                            logger.info(
                                "Cross-tier mapped LR bucket",
                                extra={
                                    "original_bucket": diagnostic_bucket,
                                    "normalized": norm_bucket,
                                    "union_match": union_display,
                                    "projected_bucket": display_bucket,
                                },
                            )
            if not display_bucket:
                logger.warning(
                    "Unmatched LR bucket; leaving defaults at 1.0",
                    extra={
                        "original_bucket": diagnostic_bucket,
                        "normalized": norm_bucket,
                        "available_buckets": list(bucket_norm_to_display.values()),
                    },
                )
        if display_bucket:
            feature_lr_map[standardized_feature][display_bucket] = round(float(lr_value), 2)
    
    # Convert to DataFrame format
    matrix_data = []
    for feature, lr_values in feature_lr_map.items():
        row = {'Feature': feature}
        for bucket_name in diagnostic_buckets_display:
            row[bucket_name] = lr_values.get(bucket_name, 1.0)
        matrix_data.append(row)
    
    # Create DataFrame
    df = pd.DataFrame(matrix_data)
    # Ensure expected columns exist even if there are no features
    if df.empty:
        df = pd.DataFrame(columns=['Feature', *diagnostic_buckets_display])
    
    # Ensure all LR values are positive (replace any 0s or negatives with minimum)
    for col in diagnostic_buckets_display:
        df[col] = df[col].apply(lambda x: max(x, 0.01))  # Minimum LR of 0.01
    
    # Sort by feature name for consistency
    df = df.sort_values('Feature').reset_index(drop=True)
    
    return df

def create_prior_probabilities_file(diagnostic_framework: List[Dict[str, Any]], 
                                   tier_level: int = 1) -> Dict[str, float]:
    """
    Create prior probabilities file for a specific tier.
    Format: {diagnostic_category: probability}
    """
    
    # Find the specified tier
    target_tier = None
    for tier in diagnostic_framework:
        if tier['tier_level'] == tier_level:
            target_tier = tier
            break
    
    if not target_tier:
        # Default to first tier if specified tier not found
        target_tier = diagnostic_framework[0] if diagnostic_framework else None
    
    if not target_tier:
        return {}
    
    return target_tier['a_priori_probabilities']

def export_to_csv(df: pd.DataFrame) -> str:
    """Export DataFrame to CSV string"""
    return df.to_csv(index=False)

def export_to_excel(df: pd.DataFrame) -> bytes:
    """Export DataFrame to Excel bytes"""
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Feature_LR_Matrix', index=False)
    output.seek(0)
    return output.getvalue()

def create_case_summary_for_simulator(case_details: Dict[str, Any],
                                     primary_diagnosis: str,
                                     case_id: int = None) -> str:
    """
    Create a case summary text file for the simulator app to use as a transcript.
    This simulates what a medical student might document during a case encounter.
    """
    
    summary_parts = []
    
    # Header
    if case_id:
        summary_parts.append(f"CASE ID: {case_id}")
    summary_parts.append(f"PRIMARY DIAGNOSIS: {primary_diagnosis}")
    summary_parts.append("")
    
    # Case presentation
    summary_parts.append("CASE PRESENTATION:")
    summary_parts.append(case_details.get('presentation', ''))
    summary_parts.append("")
    
    # Patient personality
    summary_parts.append("PATIENT COMMUNICATION STYLE:")
    summary_parts.append(case_details.get('patient_personality', ''))
    summary_parts.append("")
    
    # History
    summary_parts.append("HISTORY FINDINGS:")
    for hq in case_details.get('history_questions', []):
        summary_parts.append(f"Question: {hq['question']}")
        summary_parts.append(f"Patient Response: {hq['expected_answer']}")
        summary_parts.append("")
    
    # Physical Exam
    summary_parts.append("PHYSICAL EXAMINATION FINDINGS:")
    for pe in case_details.get('physical_exam_findings', []):
        summary_parts.append(f"{pe['examination']}: {pe['findings']}")
    summary_parts.append("")
    
    # Diagnostic Workup
    summary_parts.append("DIAGNOSTIC WORKUP:")
    for dw in case_details.get('diagnostic_workup', []):
        summary_parts.append(f"{dw['test']}: {dw['rationale']}")
    summary_parts.append("")
    
    return "\n".join(summary_parts)

def validate_lr_matrix_for_simulator(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Validate that the LR matrix meets simulator app requirements
    """
    validation_results = {
        "valid": True,
        "errors": [],
        "warnings": []
    }
    
    # Check that first column is 'Feature'
    if df.columns[0] != 'Feature':
        validation_results["errors"].append("First column must be named 'Feature'")
        validation_results["valid"] = False
    
    # Check that all LR values are positive
    numeric_cols = df.select_dtypes(include=[np.number]).columns
    for col in numeric_cols:
        if (df[col] <= 0).any():
            validation_results["errors"].append(f"Column '{col}' contains non-positive values")
            validation_results["valid"] = False
    
    # Check for missing values
    if df.isnull().any().any():
        validation_results["warnings"].append("Matrix contains missing values")
    
    # Check reasonable LR ranges
    for col in numeric_cols:
        if df[col].max() > 50:
            validation_results["warnings"].append(f"Column '{col}' has very high LR values (>50)")
        if df[col].min() < 0.1:
            validation_results["warnings"].append(f"Column '{col}' has very low LR values (<0.1)")
    
    return validation_results
