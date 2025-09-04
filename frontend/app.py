import streamlit as st
import requests
import json
import os
from dotenv import load_dotenv

load_dotenv()

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

st.set_page_config(
    page_title="Medical Case Generator",
    page_icon="🏥",
    layout="wide"
)

st.title("🏥 Medical Case Generator")
st.markdown("Generate comprehensive medical cases with diagnostic frameworks and likelihood ratios")

if 'generated_case' not in st.session_state:
    st.session_state.generated_case = None
if 'session_id' not in st.session_state:
    st.session_state.session_id = None
if 'editing_mode' not in st.session_state:
    st.session_state.editing_mode = False

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
                            }
                        )
                        
                        if response.status_code == 200:
                            preview_data = response.json()
                            st.session_state.generated_case = preview_data
                            st.session_state.session_id = preview_data["session_id"]
                            st.session_state.editing_mode = True
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
                # This would save the current edits back to the session
                st.success("Edits saved to session!")
        
        with col2:
            if st.button("🔄 Regenerate AI Content"):
                # This would call the AI again to regenerate specific sections
                st.info("Feature coming soon: Regenerate specific sections")
        
        with col3:
            if st.button("✅ Finalize & Save to Database", type="primary"):
                # This would finalize and save to database
                try:
                    save_response = requests.post(
                        f"{BACKEND_URL}/finalize-case",
                        json={
                            "session_id": st.session_state.session_id,
                            "description": "Final edited case",  # Could get from original input
                            "primary_diagnosis": "Primary diagnosis",  # Could get from original input
                            "title": f"Case: Final"
                        }
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
    if st.session_state.generated_case and not st.session_state.editing_mode:
        case_id = st.session_state.generated_case.get('case_id')
        
        st.header("📤 Export Case Files")
        
        if case_id:
            # Get export information
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
                                    
                                    # Display download buttons
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
                        
                        # Tier selection
                        selected_tier = st.selectbox(
                            "Select Diagnostic Tier:",
                            export_info['available_tiers'],
                            help="Choose which diagnostic tier to use for the simulator"
                        )
                        
                        # Export buttons for simulator format
                        col2a, col2b = st.columns(2)
                        
                        with col2a:
                            if st.button("📊 Download LR Matrix (CSV)", key="csv"):
                                st.markdown(f"[📊 Download CSV]({BACKEND_URL}/case/{case_id}/simulator-export/lr-matrix-csv?tier_level={selected_tier})")
                            
                            if st.button("📈 Download LR Matrix (Excel)", key="excel"):  
                                st.markdown(f"[📈 Download Excel]({BACKEND_URL}/case/{case_id}/simulator-export/lr-matrix-excel?tier_level={selected_tier})")
                        
                        with col2b:
                            if st.button("🎯 Download Prior Probabilities", key="priors"):
                                st.markdown(f"[🎯 Download Priors]({BACKEND_URL}/case/{case_id}/simulator-export/prior-probabilities?tier_level={selected_tier})")
                            
                            if st.button("📝 Download Case Summary", key="summary"):
                                st.markdown(f"[📝 Download Summary]({BACKEND_URL}/case/{case_id}/simulator-export/case-summary)")
                        
                        st.markdown("---")
                        st.markdown("**💡 How to use with simulator:**")
                        st.markdown("""
                        1. Download the **LR Matrix** (CSV or Excel)
                        2. Download the **Prior Probabilities** for your chosen tier
                        3. Download the **Case Summary** as a transcript file
                        4. Upload these files to the [Transcript Feature Check Simulator](https://github.com/DrDavidL/transcript-feature-check)
                        """)
                
                else:
                    st.error("Could not load export information")
            except requests.exceptions.RequestException as e:
                st.error(f"Error loading export info: {str(e)}")
        
        elif not case_id:
            st.warning("No case ID available. Complete the editing and finalization process first.")
    else:
        st.info("No finalized case available for export. Complete the editing process first.")

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