import streamlit as st
import requests
import json
import os
from dotenv import load_dotenv
from auth import check_authentication, get_auth_header, logout

load_dotenv()

# Helpers to gather current edits from Streamlit widgets
def gather_current_case(case_dict):
    """Build a full case payload from current UI widget values.
    Falls back to existing values if a widget wasn't modified.
    """
    # Case details
    presentation = st.session_state.get("edit_presentation", case_dict['case_details'].get('presentation', ''))
    personality = st.session_state.get("edit_personality", case_dict['case_details'].get('patient_personality', ''))

    # History questions
    history_questions = []
    for i, hq in enumerate(case_dict['case_details'].get('history_questions', [])):
        q = st.session_state.get(f"hq_{i}", hq.get('question', ''))
        a = st.session_state.get(f"ha_{i}", hq.get('expected_answer', ''))
        history_questions.append({"question": q, "expected_answer": a})

    # Physical exam findings
    physical_exam_findings = []
    for i, pe in enumerate(case_dict['case_details'].get('physical_exam_findings', [])):
        exam = st.session_state.get(f"pe_{i}", pe.get('examination', ''))
        findings = st.session_state.get(f"pf_{i}", pe.get('findings', ''))
        physical_exam_findings.append({"examination": exam, "findings": findings})

    # Diagnostic workup (no edit UI yet; pass-through)
    diagnostic_workup = case_dict['case_details'].get('diagnostic_workup', [])

    case_details = {
        "presentation": presentation,
        "patient_personality": personality,
        "history_questions": history_questions,
        "physical_exam_findings": physical_exam_findings,
        "diagnostic_workup": diagnostic_workup,
    }

    # Diagnostic framework
    diagnostic_framework = []
    for tier_idx, tier in enumerate(case_dict.get('diagnostic_framework', [])):
        # Buckets: read possibly edited names/descriptions
        buckets = []
        for bucket_idx, bucket in enumerate(tier.get('buckets', [])):
            name = st.session_state.get(f"bucket_name_{tier_idx}_{bucket_idx}", bucket.get('name', ''))
            desc = st.session_state.get(f"bucket_desc_{tier_idx}_{bucket_idx}", bucket.get('description', ''))
            if name:
                buckets.append({"name": name, "description": desc})

        # Probabilities: keyed by bucket name
        probs = {}
        for b in buckets:
            key = f"prob_{tier_idx}_{b['name']}"
            default_val = tier.get('a_priori_probabilities', {}).get(b['name'], 0.0)
            probs[b['name']] = float(st.session_state.get(key, default_val) or 0.0)

        diagnostic_framework.append({
            "tier_level": tier.get('tier_level', 1),
            "buckets": buckets,
            "a_priori_probabilities": probs,
        })

    # Feature likelihood ratios, grouped by category
    feature_likelihood_ratios = []
    categories = {}
    for lr in case_dict.get('feature_likelihood_ratios', []):
        cat = lr.get('feature_category', 'history')
        categories.setdefault(cat, []).append(lr)
    for category, lrs in categories.items():
        for lr_idx, lr in enumerate(lrs):
            feature_name = st.session_state.get(f"lr_feature_{category}_{lr_idx}", lr.get('feature_name', ''))
            diagnostic_bucket = st.session_state.get(f"lr_bucket_{category}_{lr_idx}", lr.get('diagnostic_bucket', ''))
            tier_level = int(st.session_state.get(f"lr_tier_{category}_{lr_idx}", lr.get('tier_level', 1)) or 1)
            lr_value = float(st.session_state.get(f"lr_value_{category}_{lr_idx}", lr.get('likelihood_ratio', 1.0)) or 1.0)
            feature_likelihood_ratios.append({
                "feature_name": feature_name,
                "feature_category": category,
                "diagnostic_bucket": diagnostic_bucket,
                "tier_level": tier_level,
                "likelihood_ratio": lr_value,
            })

    return case_details, diagnostic_framework, feature_likelihood_ratios

# Check authentication first
if not check_authentication():
    st.stop()

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

st.set_page_config(
    page_title="Medical Case Generator",
    page_icon="🏥",
    layout="wide"
)

# Model selection
selected_model = st.sidebar.selectbox(
    "Select AI Model",
    ["gpt-4o-mini", "gpt-4o", "gpt-5-mini", "gpt-5"],
    index=0
)

# Set temperature based on model
temperature = 0.7 if "gpt-5" not in selected_model else 1.0

col1, col2 = st.columns([4, 1])
with col1:
    st.title("🏥 Medical Case Generator")
    st.markdown("Generate comprehensive medical cases with diagnostic frameworks and likelihood ratios")
with col2:
    if st.button("🚪 Logout"):
        logout()

if 'generated_case' not in st.session_state:
    st.session_state.generated_case = None
if 'session_id' not in st.session_state:
    st.session_state.session_id = None
if 'editing_mode' not in st.session_state:
    st.session_state.editing_mode = False
if 'primary_diagnosis_input' not in st.session_state:
    st.session_state.primary_diagnosis_input = ''
if 'case_title_input' not in st.session_state:
    st.session_state.case_title_input = ''
if 'case_description_input' not in st.session_state:
    st.session_state.case_description_input = ''

tab1, tab2, tab3, tab4 = st.tabs(["Generate Case", "Edit Case", "View Final Case", "Export Files"])

with tab1:
    st.header("Create New Case")
    
    with st.form("case_generation_form"):
        description = st.text_area(
            "Brief Case Description",
            placeholder="Enter a brief description of the medical case...",
            height=100
        )
        
        primary_diagnosis = st.text_input(
            "Primary Diagnosis",
            placeholder="e.g., Acute Myocardial Infarction"
        )
        
        preview_button = st.form_submit_button("Generate Preview", type="primary")
        
        if preview_button:
            if description and primary_diagnosis:
                with st.spinner("Generating case preview with AI... This may take a few minutes."):
                    try:
                        response = requests.post(
                            f"{BACKEND_URL}/preview-case",
                            json={
                                "description": description,
                                "primary_diagnosis": primary_diagnosis
                            },
                            headers=get_auth_header()
                        )
                        
                        if response.status_code == 200:
                            preview_data = response.json()
                            st.session_state.generated_case = preview_data
                            st.session_state.session_id = preview_data["session_id"]
                            st.session_state.editing_mode = True
                            # Persist initial metadata for later finalize
                            st.session_state.primary_diagnosis_input = primary_diagnosis
                            st.session_state.case_description_input = description or "Final edited case"
                            st.session_state.case_title_input = f"Case: {primary_diagnosis}" if primary_diagnosis else "Case: Final"
                            st.success("Case preview generated! Go to the 'Edit Case' tab to review and modify.")
                            st.rerun()
                        else:
                            st.error(f"Error generating case: {response.text}")
                    
                    except requests.exceptions.RequestException as e:
                        st.error(f"Connection error: {str(e)}")
            else:
                st.error("Please fill in both fields")

with tab2:
    if st.session_state.generated_case and st.session_state.editing_mode:
        case = st.session_state.generated_case
        
        st.header("📝 Edit Case Content")
        st.info("Review and modify the generated content before saving to database.")

        # Basic metadata
        st.subheader("Case Metadata")
        meta_col1, meta_col2, meta_col3 = st.columns([2, 2, 2])
        with meta_col1:
            st.session_state.case_title_input = st.text_input(
                "Case Title",
                value=st.session_state.case_title_input or f"Case: {st.session_state.primary_diagnosis_input}" if st.session_state.primary_diagnosis_input else "Case: Final",
                key="edit_case_title",
            )
        with meta_col2:
            st.session_state.primary_diagnosis_input = st.text_input(
                "Primary Diagnosis",
                value=st.session_state.primary_diagnosis_input or "",
                key="edit_primary_diagnosis",
            )
        with meta_col3:
            st.session_state.case_description_input = st.text_input(
                "Short Description",
                value=st.session_state.case_description_input or "Final edited case",
                key="edit_case_description",
            )
        
        # Case Details Editing
        st.subheader("Case Details")
        with st.expander("Edit Case Presentation", expanded=True):
            presentation = st.text_area(
                "Case Presentation",
                value=case['case_details']['presentation'],
                height=200,
                key="edit_presentation"
            )
            
            personality = st.text_area(
                "Patient Personality",
                value=case['case_details']['patient_personality'],
                height=100,
                key="edit_personality"
            )
        
        # History Questions Editing
        st.subheader("History Questions")
        with st.expander("Edit History Questions", expanded=False):
            history_questions = []
            for i, hq in enumerate(case['case_details']['history_questions']):
                col1, col2 = st.columns(2)
                with col1:
                    question = st.text_input(f"Question {i+1}", value=hq['question'], key=f"hq_{i}")
                with col2:
                    answer = st.text_input(f"Expected Answer {i+1}", value=hq['expected_answer'], key=f"ha_{i}")
                history_questions.append({"question": question, "expected_answer": answer})
            
            if st.button("Add History Question"):
                history_questions.append({"question": "", "expected_answer": ""})
        
        # Physical Exam Editing
        st.subheader("Physical Examination")
        with st.expander("Edit Physical Exam Findings", expanded=False):
            physical_exams = []
            for i, pe in enumerate(case['case_details']['physical_exam_findings']):
                col1, col2 = st.columns(2)
                with col1:
                    exam = st.text_input(f"Examination {i+1}", value=pe['examination'], key=f"pe_{i}")
                with col2:
                    findings = st.text_input(f"Findings {i+1}", value=pe['findings'], key=f"pf_{i}")
                physical_exams.append({"examination": exam, "findings": findings})
        
        # Diagnostic Framework Editing
        st.subheader("Diagnostic Framework")
        st.warning("Editing diagnostic bucket names after LR generation may invalidate mappings. Use 'Regenerate LRs' below to refresh likelihood ratios based on your current buckets.")
        with st.expander("Edit Diagnostic Tiers and Probabilities", expanded=False):
            for tier_idx, tier in enumerate(case['diagnostic_framework']):
                st.write(f"**Tier {tier['tier_level']}**")
                
                # Edit buckets
                buckets = []
                for bucket_idx, bucket in enumerate(tier['buckets']):
                    col1, col2 = st.columns(2)
                    with col1:
                        name = st.text_input(
                            f"T{tier['tier_level']} Bucket {bucket_idx+1} Name", 
                            value=bucket['name'], 
                            key=f"bucket_name_{tier_idx}_{bucket_idx}"
                        )
                    with col2:
                        desc = st.text_input(
                            f"T{tier['tier_level']} Bucket {bucket_idx+1} Description", 
                            value=bucket['description'], 
                            key=f"bucket_desc_{tier_idx}_{bucket_idx}"
                        )
                    buckets.append({"name": name, "description": desc})
                
                # Edit probabilities
                st.write("A Priori Probabilities:")
                probs = {}
                total_prob = 0
                for bucket in buckets:
                    if bucket['name']:
                        prob = st.number_input(
                            f"{bucket['name']} Probability",
                            min_value=0.0,
                            max_value=1.0,
                            value=tier['a_priori_probabilities'].get(bucket['name'], 0.0),
                            step=0.01,
                            key=f"prob_{tier_idx}_{bucket['name']}"
                        )
                        probs[bucket['name']] = prob
                        total_prob += prob
                
                if abs(total_prob - 1.0) > 0.01:
                    st.warning(f"Tier {tier['tier_level']} probabilities sum to {total_prob:.3f}. Should sum to 1.0")
                
                st.write("---")
        
        # Feature Likelihood Ratios Editing
        st.subheader("Feature Likelihood Ratios")
        with st.expander("Edit Likelihood Ratios", expanded=False):
            st.info("Likelihood Ratios: >1 increases probability, <1 decreases probability")
            
            # Group by category for easier editing
            categories = {}
            for lr in case['feature_likelihood_ratios']:
                cat = lr['feature_category']
                if cat not in categories:
                    categories[cat] = []
                categories[cat].append(lr)
            
            for category, lrs in categories.items():
                st.write(f"**{category.replace('_', ' ').title()}**")
                for lr_idx, lr in enumerate(lrs):
                    col1, col2, col3, col4 = st.columns(4)
                    with col1:
                        st.text_input(
                            "Feature", 
                            value=lr['feature_name'], 
                            key=f"lr_feature_{category}_{lr_idx}"
                        )
                    with col2:
                        st.text_input(
                            "Diagnostic Bucket", 
                            value=lr['diagnostic_bucket'], 
                            key=f"lr_bucket_{category}_{lr_idx}"
                        )
                    with col3:
                        st.number_input(
                            "Tier", 
                            min_value=1, 
                            max_value=3, 
                            value=lr.get('tier_level', 1), 
                            key=f"lr_tier_{category}_{lr_idx}"
                        )
                    with col4:
                        st.number_input(
                            "LR", 
                            min_value=0.01, 
                            max_value=50.0, 
                            value=lr['likelihood_ratio'], 
                            step=0.1,
                            key=f"lr_value_{category}_{lr_idx}"
                        )
        
        # Save buttons
        st.write("---")
        col1, col2, col3 = st.columns(3)
        
        with col1:
            if st.button("💾 Save Edits", type="primary"):
                try:
                    updated_case_details, updated_framework, updated_lrs = gather_current_case(case)
                    payload = {
                        "session_id": st.session_state.session_id,
                        "case_details": updated_case_details,
                        "diagnostic_framework": updated_framework,
                        "feature_likelihood_ratios": updated_lrs,
                        # Include draft metadata so backend stores in session
                        "title": st.session_state.case_title_input or None,
                        "description": st.session_state.case_description_input or None,
                        "primary_diagnosis": st.session_state.primary_diagnosis_input or None,
                    }
                    resp = requests.put(f"{BACKEND_URL}/edit-case", json=payload, headers=get_auth_header())
                    if resp.status_code == 200:
                        # Update local copy with latest edits for preview rendering
                        case['case_details'] = updated_case_details
                        case['diagnostic_framework'] = updated_framework
                        case['feature_likelihood_ratios'] = updated_lrs
                        case['title'] = st.session_state.case_title_input
                        case['description'] = st.session_state.case_description_input
                        case['primary_diagnosis'] = st.session_state.primary_diagnosis_input
                        st.session_state.generated_case = case
                        st.success("Edits saved to session!")
                    else:
                        st.error(f"Failed to save edits: {resp.text}")
                except requests.exceptions.RequestException as e:
                    st.error(f"Connection error: {str(e)}")
        
        with col2:
            if st.button("🔄 Regenerate LRs (strict)"):
                try:
                    updated_case_details, updated_framework, _ = gather_current_case(case)
                    payload = {
                        "session_id": st.session_state.session_id,
                        "case_details": updated_case_details,
                        "diagnostic_framework": updated_framework,
                    }
                    resp = requests.post(f"{BACKEND_URL}/regenerate-lrs", json=payload, headers=get_auth_header(), timeout=120)
                    if resp.status_code == 200:
                        data = resp.json()
                        case['feature_likelihood_ratios'] = data.get('feature_likelihood_ratios', [])
                        st.session_state.generated_case = case
                        st.success("Likelihood ratios regenerated successfully.")
                    else:
                        st.error(f"Failed to regenerate LRs: {resp.text}")
                except requests.exceptions.RequestException as e:
                    st.error(f"Connection error: {str(e)}")
        
        with col3:
            if st.button("✅ Finalize & Save to Database", type="primary"):
                try:
                    updated_case_details, updated_framework, updated_lrs = gather_current_case(case)
                    save_payload = {
                        "session_id": st.session_state.session_id,
                        "description": st.session_state.case_description_input or "Final edited case",
                        "primary_diagnosis": st.session_state.primary_diagnosis_input or "Primary diagnosis",
                        "title": st.session_state.case_title_input or "Case: Final",
                        # Include full payload for backend fallback when Redis is unavailable
                        "case_details": updated_case_details,
                        "diagnostic_framework": updated_framework,
                        "feature_likelihood_ratios": updated_lrs,
                    }
                    save_response = requests.post(
                        f"{BACKEND_URL}/finalize-case",
                        json=save_payload,
                        headers=get_auth_header(),
                    )
                    if save_response.status_code == 200:
                        final_case = save_response.json()
                        st.session_state.generated_case = final_case
                        st.session_state.editing_mode = False
                        st.success(f"✅ Case saved to database with ID: {final_case['case_id']}")
                        st.rerun()
                    else:
                        st.error(f"Error saving case: {save_response.text}")
                except requests.exceptions.RequestException as e:
                    st.error(f"Connection error: {str(e)}")

with tab1:
    # If a case is already generated and we're in editing mode, show a persistent reminder
    if st.session_state.session_id and st.session_state.editing_mode:
        st.info("A case preview is ready. Switch to the 'Edit Case' tab to make changes and save.")
    
    else:
        st.info("No case in editing mode. Generate a case preview first.")

with tab3:
    if st.session_state.generated_case and not st.session_state.editing_mode:
        case = st.session_state.generated_case
        
        st.header(f"✅ Final Case (ID: {case.get('case_id', 'Preview')})")
        
        col1, col2 = st.columns([2, 1])
        
        with col1:
            st.subheader("Case Presentation")
            st.write(case['case_details']['presentation'])
            
            st.subheader("Patient Personality")
            st.write(case['case_details']['patient_personality'])
            
            st.subheader("History Questions")
            for i, question in enumerate(case['case_details']['history_questions'], 1):
                with st.expander(f"Question {i}: {question['question']}"):
                    st.write(f"**Expected Answer:** {question['expected_answer']}")
            
            st.subheader("Physical Exam Findings")
            for finding in case['case_details']['physical_exam_findings']:
                with st.expander(f"{finding['examination']}"):
                    st.write(finding['findings'])
            
            st.subheader("Diagnostic Workup")
            for test in case['case_details']['diagnostic_workup']:
                with st.expander(f"{test['test']}"):
                    st.write(f"**Rationale:** {test['rationale']}")
        
        with col2:
            st.subheader("Diagnostic Framework")
            
            for tier in case['diagnostic_framework']:
                st.write(f"**Tier {tier['tier_level']}**")
                
                buckets_df = []
                for bucket in tier['buckets']:
                    prob = tier['a_priori_probabilities'].get(bucket['name'], 0)
                    buckets_df.append({
                        'Bucket': bucket['name'],
                        'Probability': f"{prob:.3f}",
                        'Description': bucket['description']
                    })
                
                st.table(buckets_df)
                st.write("---")
            
            st.subheader("Feature Likelihood Ratios")
            
            categories = {}
            for lr in case['feature_likelihood_ratios']:
                cat = lr['feature_category']
                if cat not in categories:
                    categories[cat] = []
                categories[cat].append(lr)
            
            for category, features in categories.items():
                with st.expander(f"{category.replace('_', ' ').title()}"):
                    for feature in features:
                        st.write(f"**{feature['feature_name']}**")
                        st.write(f"- {feature['diagnostic_bucket']}: {feature['likelihood_ratio']:.2f}")
    else:
        st.info("No finalized case available. Complete the editing process first.")

with tab4:
    st.header("📤 Export Case Files")
    # Determine export mode: DB-backed (finalized) or Session-backed (draft)
    case_id = None
    if st.session_state.generated_case and not st.session_state.editing_mode:
        case_id = st.session_state.generated_case.get('case_id')

    if case_id:
        # DB-backed exports
        try:
            export_info_response = requests.get(f"{BACKEND_URL}/case/{case_id}/simulator-exports")
            if export_info_response.status_code == 200:
                export_info = export_info_response.json()

                col1, col2 = st.columns([1, 1])

                with col1:
                    st.subheader("🧬 Original JSON Files")
                    st.markdown("Standard case generator outputs:")

                    if st.button("📊 Generate JSON Export Files", type="primary"):
                        try:
                            response = requests.get(f"{BACKEND_URL}/case/{case_id}/output-files")
                            if response.status_code == 200:
                                files = response.json()
                                st.success("JSON files generated successfully!")
                                st.download_button(
                                    label="📋 Download case_details.json",
                                    data=json.dumps(files['case_details_json'], indent=2),
                                    file_name=f"case_{case_id}_details.json",
                                    mime="application/json"
                                )
                                st.download_button(
                                    label="🎯 Download a_priori_probabilities.json",
                                    data=json.dumps(files['a_priori_probabilities_json'], indent=2),
                                    file_name=f"case_{case_id}_a_priori_probabilities.json",
                                    mime="application/json"
                                )
                                st.download_button(
                                    label="📊 Download feature_likelihood_ratios.json",
                                    data=json.dumps(files['feature_likelihood_ratios_json'], indent=2),
                                    file_name=f"case_{case_id}_feature_likelihood_ratios.json",
                                    mime="application/json"
                                )
                            else:
                                st.error(f"Error retrieving export files: {response.text}")
                        except requests.exceptions.RequestException as e:
                            st.error(f"Connection error: {str(e)}")

                with col2:
                    st.subheader("🎮 Simulator App Files")
                    st.markdown("Formatted for the transcript feature check simulator:")
                    st.info(f"""
                    **Available for Case ID: {export_info['case_id']}**
                    - 🔢 {export_info['total_features']} clinical features
                    - 🎯 {export_info['total_diagnostic_buckets']} diagnostic categories  
                    - 📊 {len(export_info['available_tiers'])} diagnostic tiers
                    """)
                    st.session_state.setdefault('sim_selected_tier', None)
                    prev_tier = st.session_state.sim_selected_tier
                    selected_tier = st.selectbox(
                        "Select Diagnostic Tier:",
                        export_info['available_tiers'],
                        index=0 if st.session_state.sim_selected_tier is None else max(0, export_info['available_tiers'].index(st.session_state.sim_selected_tier)) if st.session_state.sim_selected_tier in export_info['available_tiers'] else 0,
                        help="Choose which diagnostic tier to use for the simulator"
                    )
                    if st.session_state.sim_selected_tier is None:
                        st.session_state.sim_selected_tier = selected_tier
                    if selected_tier != prev_tier:
                        st.session_state.sim_selected_tier = selected_tier
                        for k in ['sim_csv_bytes', 'sim_excel_bytes', 'sim_priors_bytes', 'sim_summary_bytes']:
                            st.session_state.pop(k, None)

                    def fetch_sim_files():
                        urls = {
                            'csv': f"{BACKEND_URL}/case/{case_id}/simulator-export/lr-matrix-csv?tier_level={st.session_state.sim_selected_tier}",
                            'excel': f"{BACKEND_URL}/case/{case_id}/simulator-export/lr-matrix-excel?tier_level={st.session_state.sim_selected_tier}",
                            'priors': f"{BACKEND_URL}/case/{case_id}/simulator-export/prior-probabilities?tier_level={st.session_state.sim_selected_tier}",
                            'summary': f"{BACKEND_URL}/case/{case_id}/simulator-export/case-summary",
                        }
                        try:
                            csv_resp = requests.get(urls['csv'], timeout=60)
                            excel_resp = requests.get(urls['excel'], timeout=60)
                            priors_resp = requests.get(urls['priors'], timeout=60)
                            summary_resp = requests.get(urls['summary'], timeout=60)
                            st.session_state.pop('sim_fetch_errors', None)
                            errs = []
                            if csv_resp.status_code == 200:
                                st.session_state.sim_csv_bytes = csv_resp.content
                            else:
                                errs.append(f"CSV {csv_resp.status_code}: {csv_resp.text[:200]}")
                            if excel_resp.status_code == 200:
                                st.session_state.sim_excel_bytes = excel_resp.content
                            else:
                                errs.append(f"Excel {excel_resp.status_code}: {excel_resp.text[:200]}")
                            if priors_resp.status_code == 200:
                                st.session_state.sim_priors_bytes = priors_resp.content
                            else:
                                errs.append(f"Priors {priors_resp.status_code}: {priors_resp.text[:200]}")
                            if summary_resp.status_code == 200:
                                st.session_state.sim_summary_bytes = summary_resp.content
                            else:
                                errs.append(f"Summary {summary_resp.status_code}: {summary_resp.text[:200]}")
                            if errs:
                                st.session_state.sim_fetch_errors = errs
                        except requests.exceptions.RequestException as e:
                            st.error(f"Error generating simulator files: {str(e)}")

                    if st.button("📦 Generate Simulator Files", key="gen_sim_all"):
                        with st.spinner("Generating simulator files..."):
                            fetch_sim_files()

                    col_dl1, col_dl2 = st.columns(2)
                    with col_dl1:
                        if 'sim_csv_bytes' in st.session_state:
                            st.download_button("📊 Download LR Matrix (CSV)", data=st.session_state.sim_csv_bytes, file_name=f"case_{case_id}_lr_matrix.csv", mime="text/csv", key="dl_sim_csv")
                        else:
                            st.caption("CSV not ready yet")
                        if 'sim_priors_bytes' in st.session_state:
                            st.download_button("🎯 Download Prior Probabilities", data=st.session_state.sim_priors_bytes, file_name=f"case_{case_id}_tier_{st.session_state.sim_selected_tier}_priors.json", mime="application/json", key="dl_sim_priors")
                        else:
                            st.caption("Priors not ready yet")
                    with col_dl2:
                        if 'sim_excel_bytes' in st.session_state:
                            st.download_button("📈 Download LR Matrix (Excel)", data=st.session_state.sim_excel_bytes, file_name=f"case_{case_id}_lr_matrix.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="dl_sim_excel")
                        else:
                            st.caption("Excel not ready yet")
                        if 'sim_summary_bytes' in st.session_state:
                            st.download_button("📝 Download Case Summary", data=st.session_state.sim_summary_bytes, file_name=f"case_{case_id}_summary.txt", mime="text/plain", key="dl_sim_summary")
                        else:
                            st.caption("Summary not ready yet")

                    if 'sim_fetch_errors' in st.session_state and st.session_state.sim_fetch_errors:
                        st.warning("Some files failed to generate:")
                        for e in st.session_state.sim_fetch_errors:
                            st.code(e)

                    st.markdown("---")
                    st.markdown("**💡 How to use with simulator:**")
                    st.markdown("""
                    1. Click "Generate Simulator Files" to fetch all artifacts
                    2. Download the LR Matrix (CSV or Excel)
                    3. Download the Prior Probabilities for the selected tier
                    4. Download the Case Summary transcript
                    5. Upload these files to the [Transcript Feature Check Simulator](https://github.com/DrDavidL/transcript-feature-check)
                    """)

            else:
                st.error("Could not load export information")
        except requests.exceptions.RequestException as e:
            st.error(f"Error loading export info: {str(e)}")
    else:
        # Session-backed exports (drafts)
        if not st.session_state.session_id:
            st.warning("No session available. Generate a preview first.")
        else:
            session_id = st.session_state.session_id
            try:
                export_info_response = requests.get(f"{BACKEND_URL}/session/{session_id}/simulator-exports")
                if export_info_response.status_code == 200:
                    export_info = export_info_response.json()
                    col1, col2 = st.columns([1, 1])

                    with col1:
                        st.subheader("🧬 Original JSON Files (Draft)")
                        st.markdown("Standard case generator outputs from current session:")
                        if st.button("📊 Generate JSON Export Files", key="draft_json"):
                            try:
                                # Leverage DB-style endpoint by synthesizing the JSON via session-backed call
                                # Build files locally in the UI using current session data
                                # Fetch session data
                                sess = requests.get(f"{BACKEND_URL}/session/{session_id}").json()
                                files = {
                                    'case_details_json': {
                                        'case_id': None,
                                        'title': st.session_state.case_title_input or f"Case: {st.session_state.primary_diagnosis_input}",
                                        'description': st.session_state.case_description_input or "",
                                        'primary_diagnosis': st.session_state.primary_diagnosis_input or "",
                                        'presentation': sess['case_details'].get('presentation'),
                                        'patient_personality': sess['case_details'].get('patient_personality'),
                                        'history_questions': sess['case_details'].get('history_questions', []),
                                        'physical_exam_findings': sess['case_details'].get('physical_exam_findings', []),
                                        'diagnostic_workup': sess['case_details'].get('diagnostic_workup', []),
                                    },
                                    'a_priori_probabilities_json': {
                                        f"tier_{t['tier_level']}": {
                                            'buckets': t['buckets'],
                                            'probabilities': t['a_priori_probabilities']
                                        } for t in st.session_state.generated_case['diagnostic_framework']
                                    },
                                    'feature_likelihood_ratios_json': {}
                                }
                                # Build FLR JSON grouped by category
                                flrs = st.session_state.generated_case.get('feature_likelihood_ratios', [])
                                flr_json = {'history': {}, 'physical_exam': {}, 'diagnostic_workup': {}}
                                for lr in flrs:
                                    cat = lr['feature_category']
                                    name = lr['feature_name']
                                    bucket = lr['diagnostic_bucket']
                                    val = lr['likelihood_ratio']
                                    flr_json.setdefault(cat, {}).setdefault(name, {})[bucket] = val
                                files['feature_likelihood_ratios_json'] = flr_json

                                st.download_button(
                                    "⬇️ case_details.json",
                                    data=json.dumps(files['case_details_json'], indent=2),
                                    file_name="case_details.json",
                                    mime="application/json"
                                )
                                st.download_button(
                                    "⬇️ a_priori_probabilities.json",
                                    data=json.dumps(files['a_priori_probabilities_json'], indent=2),
                                    file_name="a_priori_probabilities.json",
                                    mime="application/json"
                                )
                                st.download_button(
                                    "⬇️ feature_likelihood_ratios.json",
                                    data=json.dumps(files['feature_likelihood_ratios_json'], indent=2),
                                    file_name="feature_likelihood_ratios.json",
                                    mime="application/json"
                                )
                            except requests.exceptions.RequestException as e:
                                st.error(f"Connection error: {str(e)}")

                    with col2:
                        st.subheader("🎮 Simulator App Files (Draft)")
                        st.markdown("Formatted from current session")

                        # Maintain and react to selected tier in session state
                        st.session_state.setdefault('sim_selected_tier', None)
                        prev_tier = st.session_state.sim_selected_tier
                        tiers = export_info['available_tiers']
                        selected_tier = st.selectbox(
                            "Select Diagnostic Tier:",
                            tiers,
                            index=0 if st.session_state.sim_selected_tier is None else max(0, tiers.index(st.session_state.sim_selected_tier)) if st.session_state.sim_selected_tier in tiers else 0,
                            help="Choose which diagnostic tier to use for the simulator"
                        )
                        if st.session_state.sim_selected_tier is None or selected_tier != prev_tier:
                            st.session_state.sim_selected_tier = selected_tier
                            for k in ['sim_csv_bytes', 'sim_excel_bytes', 'sim_priors_bytes', 'sim_summary_bytes']:
                                st.session_state.pop(k, None)

                        def fetch_sim_files_session():
                            base = f"{BACKEND_URL}/session/{session_id}/simulator-export"
                            urls = {
                                'csv': f"{base}/lr-matrix-csv?tier_level={st.session_state.sim_selected_tier}",
                                'excel': f"{base}/lr-matrix-excel?tier_level={st.session_state.sim_selected_tier}",
                                'priors': f"{base}/prior-probabilities?tier_level={st.session_state.sim_selected_tier}",
                                'summary': f"{base}/case-summary",
                            }
                            try:
                                st.session_state.pop('sim_fetch_errors', None)
                                errs = []
                                csv_resp = requests.get(urls['csv'], timeout=60)
                                excel_resp = requests.get(urls['excel'], timeout=60)
                                priors_resp = requests.get(urls['priors'], timeout=60)
                                summary_resp = requests.get(urls['summary'], timeout=60)
                                if csv_resp.status_code == 200:
                                    st.session_state.sim_csv_bytes = csv_resp.content
                                else:
                                    errs.append(f"CSV {csv_resp.status_code}: {csv_resp.text[:200]}")
                                if excel_resp.status_code == 200:
                                    st.session_state.sim_excel_bytes = excel_resp.content
                                else:
                                    errs.append(f"Excel {excel_resp.status_code}: {excel_resp.text[:200]}")
                                if priors_resp.status_code == 200:
                                    st.session_state.sim_priors_bytes = priors_resp.content
                                else:
                                    errs.append(f"Priors {priors_resp.status_code}: {priors_resp.text[:200]}")
                                if summary_resp.status_code == 200:
                                    st.session_state.sim_summary_bytes = summary_resp.content
                                else:
                                    errs.append(f"Summary {summary_resp.status_code}: {summary_resp.text[:200]}")
                                if errs:
                                    st.session_state.sim_fetch_errors = errs
                            except requests.exceptions.RequestException as e:
                                st.error(f"Error generating simulator files: {str(e)}")

                        if st.button("📦 Generate Simulator Files (Draft)", key="gen_sim_all_draft"):
                            with st.spinner("Generating draft simulator files..."):
                                fetch_sim_files_session()

                        col_dl1, col_dl2 = st.columns(2)
                        with col_dl1:
                            if 'sim_csv_bytes' in st.session_state:
                                st.download_button("📊 Download LR Matrix (CSV)", data=st.session_state.sim_csv_bytes, file_name=f"session_{session_id}_lr_matrix.csv", mime="text/csv", key="dl_sim_csv_d")
                            else:
                                st.caption("CSV not ready yet")
                            if 'sim_priors_bytes' in st.session_state:
                                st.download_button("🎯 Download Prior Probabilities", data=st.session_state.sim_priors_bytes, file_name=f"session_{session_id}_tier_{st.session_state.sim_selected_tier}_priors.json", mime="application/json", key="dl_sim_priors_d")
                            else:
                                st.caption("Priors not ready yet")
                        with col_dl2:
                            if 'sim_excel_bytes' in st.session_state:
                                st.download_button("📈 Download LR Matrix (Excel)", data=st.session_state.sim_excel_bytes, file_name=f"session_{session_id}_lr_matrix.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="dl_sim_excel_d")
                            else:
                                st.caption("Excel not ready yet")
                            if 'sim_summary_bytes' in st.session_state:
                                st.download_button("📝 Download Case Summary", data=st.session_state.sim_summary_bytes, file_name=f"session_{session_id}_summary.txt", mime="text/plain", key="dl_sim_summary_d")
                            else:
                                st.caption("Summary not ready yet")

                        if 'sim_fetch_errors' in st.session_state and st.session_state.sim_fetch_errors:
                            st.warning("Some files failed to generate:")
                            for e in st.session_state.sim_fetch_errors:
                                st.code(e)
                else:
                    st.error("Could not load draft export information")
            except requests.exceptions.RequestException as e:
                st.error(f"Error loading export info: {str(e)}")

with st.sidebar:
    st.header("Navigation")
    st.markdown("**Current Features:**")
    st.markdown("- 🧠 AI-powered case generation with editing")
    st.markdown("- 🎯 Multi-tier diagnostic frameworks")  
    st.markdown("- 📊 Evidence-based likelihood ratios")
    st.markdown("- 📁 JSON + CSV/Excel exports")
    st.markdown("- 🎮 **Simulator app compatibility**")
    
    st.header("System Status")
    try:
        health_response = requests.get(f"{BACKEND_URL}/", timeout=5)
        if health_response.status_code == 200:
            st.success("✅ Backend Connected")
        else:
            st.error("❌ Backend Error")
    except:
        st.error("❌ Backend Unavailable")
    
    if st.button("View All Cases"):
        try:
            cases_response = requests.get(f"{BACKEND_URL}/cases")
            if cases_response.status_code == 200:
                cases = cases_response.json()
                st.write("**Existing Cases:**")
                for case in cases:
                    st.write(f"- ID {case['id']}: {case['primary_diagnosis']}")
        except:
            st.error("Could not load cases")
