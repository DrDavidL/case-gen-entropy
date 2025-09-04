import pandas as pd
import numpy as np
from typing import Dict, List, Any
from io import StringIO, BytesIO

def create_feature_lr_matrix(case_details: Dict[str, Any], 
                           diagnostic_framework: List[Dict[str, Any]], 
                           feature_likelihood_ratios: List[Dict[str, Any]]) -> pd.DataFrame:
    """
    Create a feature-LR matrix in the format expected by the simulator app.
    
    Format expected:
    - First column: Clinical features (e.g., "Patient Has: chest pain")
    - Remaining columns: Diagnostic categories with LR values
    """
    
    # Extract all unique diagnostic buckets from all tiers
    diagnostic_buckets = set()
    for tier in diagnostic_framework:
        for bucket in tier['buckets']:
            diagnostic_buckets.add(bucket['name'])
    
    diagnostic_buckets = sorted(list(diagnostic_buckets))
    
    # Create feature mapping based on actual LR data, not case details
    # This ensures we only include features that have LR values
    feature_lr_map = {}
    
    # Build the feature-LR mapping from the actual LR data
    for lr in feature_likelihood_ratios:
        feature_name = lr['feature_name']
        diagnostic_bucket = lr['diagnostic_bucket']
        lr_value = lr['likelihood_ratio']
        
        # Create a standardized feature name
        if lr['feature_category'] == 'history':
            standardized_feature = f"Patient Has: {feature_name.lower().replace('history:', '').replace('question:', '').strip()}"
        elif lr['feature_category'] == 'physical_exam':
            standardized_feature = f"Physical Finding: {feature_name.lower().replace('physical exam:', '').replace('examination:', '').strip()}"
        elif lr['feature_category'] == 'diagnostic_workup':
            standardized_feature = f"Test Result: {feature_name.lower().replace('diagnostic test:', '').replace('test:', '').strip()}"
        else:
            standardized_feature = f"Clinical Feature: {feature_name.strip()}"
        
        # Initialize feature if not exists
        if standardized_feature not in feature_lr_map:
            feature_lr_map[standardized_feature] = {}
            # Initialize all buckets to 1.0 for this feature
            for bucket in diagnostic_buckets:
                feature_lr_map[standardized_feature][bucket] = 1.0
        
        # Set the actual LR value
        if diagnostic_bucket in diagnostic_buckets:
            feature_lr_map[standardized_feature][diagnostic_bucket] = round(lr_value, 2)
    
    # Convert to DataFrame format
    matrix_data = []
    for feature, lr_values in feature_lr_map.items():
        row = {'Feature': feature}
        for bucket in diagnostic_buckets:
            row[bucket] = lr_values.get(bucket, 1.0)
        matrix_data.append(row)
    
    # Create DataFrame
    df = pd.DataFrame(matrix_data)
    
    # Ensure all LR values are positive (replace any 0s or negatives with minimum)
    for col in diagnostic_buckets:
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