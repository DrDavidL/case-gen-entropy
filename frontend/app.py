import json
import os

import requests
import streamlit as st
from auth import check_authentication, get_auth_header, logout
from dotenv import load_dotenv

load_dotenv()

# Check authentication first
if not check_authentication():
    st.stop()

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

st.set_page_config(page_title="Medical Case Generator", page_icon="🏥", layout="wide")

col1, col2 = st.columns([4, 1])
with col1:
    st.title("🏥 Medical Case Generator")
    st.markdown(
        "Generate comprehensive medical cases with diagnostic frameworks and likelihood ratios"
    )
with col2:
    if st.button("🚪 Logout"):
        logout()


def _regenerate_lrs():
    """Re-run LR generation and swap the result into session state.

    An on_click callback so the refreshed list renders in the same pass. The
    st.rerun() this replaces ejected the user from the Edit tab back to Generate,
    because st.tabs loses its client-side selection on a server-forced rerun.
    Outcome is stashed for the script body to render -- st.success/st.error inside
    a callback would draw before the tab is laid out.
    """
    case = st.session_state.get("generated_case") or {}
    try:
        r = requests.post(
            f"{BACKEND_URL}/regenerate-lrs",
            json={
                "session_id": st.session_state.session_id,
                "case_details": case.get("case_details"),
                "diagnostic_framework": case.get("diagnostic_framework"),
            },
            headers=get_auth_header(),
            timeout=300,
        )
        if r.status_code == 200:
            case["feature_likelihood_ratios"] = r.json()["feature_likelihood_ratios"]
            st.session_state.generated_case = case
            st.session_state.regen_result = (
                "success",
                f"Regenerated {len(case['feature_likelihood_ratios'])} likelihood ratios.",
            )
        else:
            st.session_state.regen_result = ("error", f"Regeneration failed: {r.text}")
    except requests.exceptions.RequestException as e:
        st.session_state.regen_result = ("error", f"Connection error: {e}")


def _add_image_link():
    """Append a blank image-link row.

    Used as an on_click callback rather than mutate-then-st.rerun(): callbacks run
    before the script re-executes, so the new row renders in the same pass. The
    st.rerun() this replaces reset the active tab, because st.tabs keeps its
    selection client-side and a server-forced rerun discards it.
    """
    st.session_state.sim_image_links.append({"Test Name": "", "Test Link": ""})


def _remove_image_link():
    if len(st.session_state.get("sim_image_links", [])) > 1:
        st.session_state.sim_image_links.pop()


def _as_dict(value, default):
    """Coerce a case_details JSON field to a dict.

    Mirrors backend.utils.sim_ready_transform.coerce_json_field. The shared
    case_details table holds these as JSON strings on most existing rows.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return dict(default)
    return value if isinstance(value, dict) else dict(default)


def _norm_links(links):
    """Normalize Image Links to [{"Test Name", "Test Link"}], accepting bare URLs."""
    if not isinstance(links, list):
        return []
    out = []
    for item in links:
        if isinstance(item, dict):
            name = str(item.get("Test Name") or "").strip()
            url = str(item.get("Test Link") or "").strip()
        else:
            name, url = "", str(item or "").strip()
        if name or url:
            out.append({"Test Name": name, "Test Link": url})
    return out


@st.cache_data(ttl=60)
def _backend_status():
    """Backend build identity. Cached briefly so it refreshes after a deploy."""
    try:
        r = requests.get(f"{BACKEND_URL}/", timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"error": str(e)[:120]}


def render_build_footer():
    """Show frontend AND backend build stamps.

    Both, deliberately. A `deploy-aca.sh redeploy` no-op left the backend on a
    four-month-old image while the frontend moved on, and nothing on screen
    disagreed. A mismatch between these two is the signal that a deploy only
    half-landed.
    """
    fe_sha = os.getenv("GIT_SHA", "unknown")
    fe_built = os.getenv("BUILD_TIME", "unknown")
    status = _backend_status()

    st.divider()
    if "error" in status:
        st.caption(
            f"Frontend `{fe_sha}` · built {fe_built}  \n"
            f":red[Backend unreachable — {status['error']}]"
        )
        return

    build = status.get("build", {})
    be_sha = build.get("git_sha", "unknown")
    be_built = build.get("build_time", "unknown")

    line = (
        f"Frontend `{fe_sha}` · built {fe_built}  \n"
        f"Backend `{be_sha}` · built {be_built}"
    )
    if be_sha != fe_sha and "unknown" not in (be_sha, fe_sha):
        line += "  \n:orange[⚠ Frontend and backend are running different builds.]"
    if status.get("authoring_persistence") is False:
        line += "  \n:orange[⚠ Authoring persistence disabled — framework/LR data is not being saved.]"
    st.caption(line)


if "generated_case" not in st.session_state:
    st.session_state.generated_case = None
if "session_id" not in st.session_state:
    st.session_state.session_id = None
if "editing_mode" not in st.session_state:
    st.session_state.editing_mode = False
if "output_format" not in st.session_state:
    st.session_state.output_format = "sim_ready"
if "editing_existing_case_id" not in st.session_state:
    st.session_state.editing_existing_case_id = None

tab1, tab2, tab3, tab4 = st.tabs(
    ["Generate Case", "Edit Case", "View Final Case", "Export Files"]
)

with tab1:
    st.header("Create New Case")

    # Prominent banner when a case was just generated
    if st.session_state.generated_case and st.session_state.editing_mode:
        st.success(
            "Case preview is ready! Switch to the **Edit Case** tab above to review, edit, and finalize."
        )

    with st.form("case_generation_form"):
        description = st.text_area(
            "Brief Case Description",
            placeholder="Enter a brief description of the medical case...",
            height=100,
        )

        primary_diagnosis = st.text_input(
            "Primary Diagnosis", placeholder="e.g., Acute Myocardial Infarction"
        )

        # The Sim-Ready / Beta choice is retired (Decisions.md ADR-001). It was never a
        # choice between two formats -- it was a choice between two disconnected systems,
        # in separate Neon projects that could not be joined. Sim-Ready now persists the
        # diagnostic framework and full LR matrix too, so Beta offers nothing it lacks
        # while writing somewhere the simulator cannot read.
        #
        # The backend still honours output_format, and existing Beta cases remain readable
        # via /cases and the export endpoints. Only the choice is gone.
        output_format = "sim_ready"

        preview_button = st.form_submit_button("Generate Preview", type="primary")

        if preview_button:
            if description and primary_diagnosis:
                with st.spinner(
                    "Generating case preview with AI... This may take a few minutes."
                ):
                    try:
                        response = requests.post(
                            f"{BACKEND_URL}/preview-case",
                            json={
                                "description": description,
                                "primary_diagnosis": primary_diagnosis,
                                "output_format": output_format,
                            },
                            headers=get_auth_header(),
                        )

                        if response.status_code == 200:
                            preview_data = response.json()
                            st.session_state.generated_case = preview_data
                            st.session_state.session_id = preview_data["session_id"]
                            st.session_state.editing_mode = True
                            st.session_state.output_format = output_format
                            st.success(
                                "Case preview generated! Go to the 'Edit Case' tab to review and modify."
                            )
                            st.rerun()
                        else:
                            st.error(f"Error generating case: {response.text}")

                    except requests.exceptions.RequestException as e:
                        st.error(f"Connection error: {e!s}")
            else:
                st.error("Please fill in both fields")

with tab2:
    if st.session_state.generated_case and st.session_state.editing_mode:
        case = st.session_state.generated_case
        is_sim_ready = st.session_state.output_format == "sim_ready"

        st.header("Edit Case Content")
        if st.session_state.get("editing_existing_case_id"):
            st.info(
                f"Editing existing case **ID {st.session_state.editing_existing_case_id}**. Make changes and click **Update Case in Database**."
            )
        else:
            st.info(
                f"Output format: **{'Sim-Ready' if is_sim_ready else 'Beta'}**. Review and modify before saving."
            )

        # Editable case name
        if is_sim_ready:
            edited_title = st.text_input(
                "Case Name",
                value=case.get("case_details", {}).get("case_title", "Case"),
                key="edit_case_title",
            )
            case["case_details"]["case_title"] = edited_title

        # --- Sim-Ready: editable content and sim fields ---
        if is_sim_ready:
            DOOR_CHART_DELIMITER = "## PATIENT DOOR CHART and Learner Instructions"

            # Split content into Clinical Dashboard and Door Chart
            full_content = st.session_state.get(
                "sim_rendered_content", case.get("rendered_content", "")
            )
            if DOOR_CHART_DELIMITER in full_content:
                parts = full_content.split(DOOR_CHART_DELIMITER, 1)
                clinical_part = parts[0].rstrip()
                door_chart_part = DOOR_CHART_DELIMITER + parts[1]
            else:
                clinical_part = full_content
                door_chart_part = ""

            st.subheader("Case Content")
            st.info(
                "Edit the markdown below. Keep the `backtick` markers and `- **Label**:` formatting intact "
                "so the simulator can parse the content correctly. Only change the text values."
            )

            with st.expander("Clinical Dashboard (Edit)", expanded=True):
                edited_clinical = st.text_area(
                    "Clinical Dashboard Markdown",
                    value=clinical_part,
                    height=500,
                    key="edit_clinical_content",
                )

            with st.expander("Door Chart (Edit)", expanded=True):
                st.warning(
                    "**Do not remove or rename the section header, field labels, or backtick delimiters below.** "
                    "The simulator parses this section by its exact heading and label format. "
                    "Only change the values inside the backticks."
                )
                edited_door_chart = st.text_area(
                    "Door Chart Markdown",
                    value=door_chart_part,
                    height=300,
                    key="edit_door_chart_content",
                )

            # Recombine and store
            combined = edited_clinical.rstrip() + "\n\n" + edited_door_chart.lstrip()
            st.session_state.sim_rendered_content = combined

            with st.expander("Preview Rendered Content", expanded=False):
                st.markdown(combined)

            st.subheader("Simulator Fields")
            with st.expander(
                "Custom Input (Prespecified Results & Image Links)", expanded=False
            ):
                default_ci = case.get(
                    "default_custom_input",
                    {"Prespecified Results": "", "Image Links": []},
                )
                # Belt-and-braces: the backend normalises these now, but this
                # table is shared with the simulator and most existing rows hold
                # custom_input as a JSON *string*. Calling .get() on one raises
                # AttributeError, which is exactly how editing an existing case
                # used to crash.
                ci_value = _as_dict(
                    st.session_state.get("sim_custom_input", default_ci), default_ci
                )

                prespecified_results = st.text_area(
                    "Prespecified Results",
                    value=ci_value.get("Prespecified Results", ""),
                    height=100,
                    key="edit_prespecified_results",
                    help="Pre-filled lab/imaging results the simulator should return.",
                )

                # Image Links — name + URL per row. Most existing cases store
                # {"Test Name": ..., "Test Link": ...}, and the simulator renders
                # "the test name with a clickable link", so the name is not
                # decoration. A URL-only editor would silently discard it on save.
                existing_links = _norm_links(ci_value.get("Image Links"))
                if "sim_image_links" not in st.session_state:
                    st.session_state.sim_image_links = existing_links or [
                        {"Test Name": "", "Test Link": ""}
                    ]

                st.caption("Image Links — test name and URL")
                updated_links = []
                for idx, link in enumerate(st.session_state.sim_image_links):
                    lc1, lc2 = st.columns([1, 2])
                    with lc1:
                        name_val = st.text_input(
                            f"Test Name {idx + 1}",
                            value=link.get("Test Name", ""),
                            key=f"img_name_{idx}",
                            label_visibility="collapsed",
                            placeholder="CXR",
                        )
                    with lc2:
                        link_val = st.text_input(
                            f"Test Link {idx + 1}",
                            value=link.get("Test Link", ""),
                            key=f"img_link_{idx}",
                            label_visibility="collapsed",
                            placeholder="https://example.com/image.png",
                        )
                    updated_links.append({"Test Name": name_val, "Test Link": link_val})

                col_add, col_remove, _ = st.columns([1, 1, 3])
                with col_add:
                    st.button("Add Link", key="add_img_link", on_click=_add_image_link)
                with col_remove:
                    if len(st.session_state.sim_image_links) > 1:
                        st.button(
                            "Remove Last",
                            key="rm_img_link",
                            on_click=_remove_image_link,
                        )

                clean_links = [
                    lk
                    for lk in updated_links
                    if lk["Test Name"].strip() or lk["Test Link"].strip()
                ]
                st.session_state.sim_custom_input = {
                    "Prespecified Results": prespecified_results,
                    "Image Links": clean_links,
                }

            with st.expander(
                "Custom Evaluation (Additional Instructions)", expanded=False
            ):
                default_ce = case.get(
                    "default_custom_evaluation", {"Additional Instructions": ""}
                )
                ce_value = _as_dict(
                    st.session_state.get("sim_custom_evaluation", default_ce),
                    default_ce,
                )

                additional_instructions = st.text_area(
                    "Additional Instructions",
                    value=ce_value.get("Additional Instructions", ""),
                    height=100,
                    key="edit_additional_instructions",
                    help="Instructions for how the simulated patient should behave.",
                )
                st.session_state.sim_custom_evaluation = {
                    "Additional Instructions": additional_instructions,
                }

            allow_orders = st.checkbox(
                "Allow Orders",
                value=st.session_state.get("sim_allow_orders", True),
                key="edit_allow_orders",
                help="Whether the simulator allows ordering diagnostic tests.",
            )
            st.session_state.sim_allow_orders = allow_orders

            default_tasks = case.get("default_learner_tasks", "")
            learner_tasks = st.text_area(
                "Learner Tasks",
                value=st.session_state.get("sim_learner_tasks", default_tasks),
                height=200,
                key="edit_learner_tasks",
                help="Markdown-formatted tasks for the learner.",
            )
            st.session_state.sim_learner_tasks = learner_tasks

        # --- Common: Case details editing (beta shows all, sim-ready shows LR pipeline fields) ---
        # Hide LR pipeline sections when editing an existing case loaded from DB
        # (structured data was not persisted for sim-ready cases)
        is_loaded_from_db = st.session_state.get("editing_existing_case_id") is not None

        if not is_sim_ready:
            st.subheader("Case Details")
            with st.expander("Edit Case Presentation", expanded=True):
                presentation = st.text_area(
                    "Case Presentation",
                    value=case["case_details"]["presentation"],
                    height=200,
                    key="edit_presentation",
                )

                personality = st.text_area(
                    "Patient Personality",
                    value=case["case_details"]["patient_personality"],
                    height=100,
                    key="edit_personality",
                )

        # History Questions, Physical Exam, Diagnostic Framework, Feature LRs
        # Only shown for newly generated cases (not when editing existing DB cases)
        if not is_loaded_from_db:
            # History Questions Editing (both formats — needed for LR pipeline)
            st.subheader("History Questions (LR Pipeline)")
            with st.expander("Edit History Questions", expanded=False):
                history_questions = []
                for i, hq in enumerate(case["case_details"]["history_questions"]):
                    col1, col2 = st.columns(2)
                    with col1:
                        question = st.text_input(
                            f"Question {i + 1}", value=hq["question"], key=f"hq_{i}"
                        )
                    with col2:
                        answer = st.text_input(
                            f"Expected Answer {i + 1}",
                            value=hq["expected_answer"],
                            key=f"ha_{i}",
                        )
                    history_questions.append(
                        {"question": question, "expected_answer": answer}
                    )

                if st.button("Add History Question"):
                    history_questions.append({"question": "", "expected_answer": ""})

            # Physical Exam Editing
            st.subheader("Physical Examination (LR Pipeline)")
            with st.expander("Edit Physical Exam Findings", expanded=False):
                physical_exams = []
                for i, pe in enumerate(case["case_details"]["physical_exam_findings"]):
                    col1, col2 = st.columns(2)
                    with col1:
                        exam = st.text_input(
                            f"Examination {i + 1}",
                            value=pe["examination"],
                            key=f"pe_{i}",
                        )
                    with col2:
                        findings = st.text_input(
                            f"Findings {i + 1}", value=pe["findings"], key=f"pf_{i}"
                        )
                    physical_exams.append({"examination": exam, "findings": findings})

            # Diagnostic Framework Editing
            st.subheader("Diagnostic Framework")
            with st.expander("Edit Diagnostic Tiers and Probabilities", expanded=False):
                for tier_idx, tier in enumerate(case["diagnostic_framework"]):
                    st.write(f"**Tier {tier['tier_level']}**")

                    buckets = []
                    for bucket_idx, bucket in enumerate(tier["buckets"]):
                        col1, col2 = st.columns(2)
                        with col1:
                            name = st.text_input(
                                f"T{tier['tier_level']} Bucket {bucket_idx + 1} Name",
                                value=bucket["name"],
                                key=f"bucket_name_{tier_idx}_{bucket_idx}",
                            )
                        with col2:
                            desc = st.text_input(
                                f"T{tier['tier_level']} Bucket {bucket_idx + 1} Description",
                                value=bucket["description"],
                                key=f"bucket_desc_{tier_idx}_{bucket_idx}",
                            )
                        buckets.append({"name": name, "description": desc})

                    st.write("A Priori Probabilities:")
                    probs = {}
                    total_prob = 0
                    for bucket in buckets:
                        if bucket["name"]:
                            prob = st.number_input(
                                f"{bucket['name']} Probability",
                                min_value=0.0,
                                max_value=1.0,
                                value=tier["a_priori_probabilities"].get(
                                    bucket["name"], 0.0
                                ),
                                step=0.01,
                                key=f"prob_{tier_idx}_{bucket['name']}",
                            )
                            probs[bucket["name"]] = prob
                            total_prob += prob

                    if abs(total_prob - 1.0) > 0.01:
                        st.warning(
                            f"Tier {tier['tier_level']} probabilities sum to {total_prob:.3f}. Should sum to 1.0"
                        )

                    st.write("---")

            # Feature Likelihood Ratios Editing
            st.subheader("Feature Likelihood Ratios")
            with st.expander("Edit Likelihood Ratios", expanded=False):
                st.info(
                    "Likelihood Ratios: >1 increases probability, <1 decreases probability"
                )

                # Regenerate against the current framework. Useful after editing
                # bucket names -- LRs reference buckets by name, so a renamed
                # bucket orphans every LR pointing at the old one.
                regen_col, regen_msg = st.columns([1, 3])
                with regen_col:
                    st.button(
                        "Regenerate LRs",
                        on_click=_regenerate_lrs,
                        help=(
                            "Re-runs likelihood-ratio generation against the current "
                            "diagnostic framework, using exact bucket names. Replaces "
                            "the list below. Costs one LLM call."
                        ),
                    )
                with regen_msg:
                    st.caption(
                        "Run this after renaming diagnostic buckets, so the LRs "
                        "point at the buckets that now exist."
                    )
                _regen = st.session_state.pop("regen_result", None)
                if _regen:
                    (st.success if _regen[0] == "success" else st.error)(_regen[1])

                categories = {}
                for lr in case["feature_likelihood_ratios"]:
                    cat = lr["feature_category"]
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
                                value=lr["feature_name"],
                                key=f"lr_feature_{category}_{lr_idx}",
                            )
                        with col2:
                            st.text_input(
                                "Diagnostic Bucket",
                                value=lr["diagnostic_bucket"],
                                key=f"lr_bucket_{category}_{lr_idx}",
                            )
                        with col3:
                            st.number_input(
                                "Tier",
                                min_value=1,
                                max_value=3,
                                value=lr.get("tier_level", 1),
                                key=f"lr_tier_{category}_{lr_idx}",
                            )
                        with col4:
                            st.number_input(
                                "LR",
                                min_value=0.01,
                                max_value=50.0,
                                value=lr["likelihood_ratio"],
                                step=0.1,
                                key=f"lr_value_{category}_{lr_idx}",
                            )

        # Save buttons
        st.write("---")
        col1, col2, col3 = st.columns(3)

        with col1:
            if st.button("Save Edits", type="primary"):
                st.success("Edits saved to session!")

        with col2:
            if st.button("Regenerate AI Content"):
                st.info("Feature coming soon: Regenerate specific sections")

        with col3:
            save_label = (
                "Update Case in Database"
                if is_loaded_from_db
                else "Finalize & Save to Database"
            )
            if st.button(save_label, type="primary"):
                try:
                    if is_loaded_from_db:
                        # UPDATE path: PUT to /sim-ready/case/{id}
                        existing_id = st.session_state.editing_existing_case_id
                        update_payload = {
                            "saved_name": case.get("case_details", {}).get(
                                "case_title", "Case"
                            ),
                            "content": st.session_state.get("sim_rendered_content", ""),
                            "custom_input": st.session_state.get("sim_custom_input"),
                            "custom_evaluation": st.session_state.get(
                                "sim_custom_evaluation"
                            ),
                            "allow_orders": st.session_state.get(
                                "sim_allow_orders", True
                            ),
                            "learner_tasks": st.session_state.get(
                                "sim_learner_tasks", ""
                            ),
                        }
                        save_response = requests.put(
                            f"{BACKEND_URL}/sim-ready/case/{existing_id}",
                            json=update_payload,
                            headers=get_auth_header(),
                        )
                    else:
                        # CREATE path: POST /finalize-case
                        finalize_payload = {
                            "session_id": st.session_state.session_id,
                            "description": case.get("case_details", {}).get(
                                "paragraph_summary", "Generated case"
                            ),
                            "primary_diagnosis": case.get("case_details", {})
                            .get("diagnostic_reasoning", {})
                            .get("differential_diagnoses", "Unknown")
                            if is_sim_ready
                            else "Primary diagnosis",
                            "title": case.get("case_details", {}).get(
                                "case_title", "Case"
                            ),
                            "output_format": st.session_state.output_format,
                        }
                        if is_sim_ready:
                            finalize_payload["allow_orders"] = st.session_state.get(
                                "sim_allow_orders", True
                            )
                            finalize_payload["learner_tasks"] = st.session_state.get(
                                "sim_learner_tasks", ""
                            )
                            finalize_payload["custom_input"] = st.session_state.get(
                                "sim_custom_input"
                            )
                            finalize_payload["custom_evaluation"] = (
                                st.session_state.get("sim_custom_evaluation")
                            )
                            if "sim_rendered_content" in st.session_state:
                                finalize_payload["rendered_content"] = (
                                    st.session_state.sim_rendered_content
                                )

                        save_response = requests.post(
                            f"{BACKEND_URL}/finalize-case",
                            json=finalize_payload,
                            headers=get_auth_header(),
                        )

                    if save_response.status_code == 200:
                        final_case = save_response.json()
                        merged = dict(st.session_state.generated_case)
                        merged.update(final_case)
                        st.session_state.generated_case = merged
                        st.session_state.editing_mode = False
                        st.session_state.editing_existing_case_id = None
                        st.success(
                            f"Case saved to database with ID: {final_case['case_id']}"
                        )
                        st.rerun()
                    else:
                        st.error(f"Error saving case: {save_response.text}")

                except requests.exceptions.RequestException as e:
                    st.error(f"Connection error: {e!s}")

    elif st.session_state.generated_case and not st.session_state.editing_mode:
        # Case was just saved — show confirmation
        case = st.session_state.generated_case
        case_id = case.get("case_id", "")
        is_sim_ready = st.session_state.output_format == "sim_ready"

        st.header("Case Saved Successfully")
        if is_sim_ready:
            st.success(
                f"**{case.get('saved_name', 'Case')}** saved to the simulator database (ID: {case_id})."
            )
            st.info(
                "Go to **View Final Case** to see the full content, or **Export Files** to download."
            )
        else:
            st.success(f"Case saved to database (ID: {case_id}).")
            st.info(
                "Go to **View Final Case** to review, or **Export Files** to download."
            )

        if st.button("Generate Another Case", type="primary"):
            st.session_state.generated_case = None
            st.session_state.session_id = None
            st.session_state.editing_mode = False
            st.session_state.editing_existing_case_id = None
            # Clear sim-ready editing state
            for key in [
                "sim_rendered_content",
                "sim_custom_input",
                "sim_custom_evaluation",
                "sim_allow_orders",
                "sim_learner_tasks",
                "sim_image_links",
            ]:
                st.session_state.pop(key, None)
            st.rerun()
    else:
        st.info(
            "No case in editing mode. Generate a case preview first, or load an existing case below."
        )

        st.subheader("Load Existing Sim-Ready Case for Editing")
        try:
            cases_resp = requests.get(f"{BACKEND_URL}/sim-ready/cases")
            if cases_resp.status_code == 200:
                sim_cases = cases_resp.json()
                if sim_cases:
                    case_options = {
                        f"ID {c['id']}: {c['saved_name']}": c["id"] for c in sim_cases
                    }
                    selected = st.selectbox(
                        "Select a case to edit:", list(case_options.keys())
                    )

                    if st.button("Load for Editing", type="primary"):
                        selected_id = case_options[selected]
                        case_resp = requests.get(
                            f"{BACKEND_URL}/sim-ready/case/{selected_id}"
                        )
                        if case_resp.status_code == 200:
                            sim_case = case_resp.json()

                            # Clear any previous editing state
                            for key in [
                                "sim_rendered_content",
                                "sim_custom_input",
                                "sim_custom_evaluation",
                                "sim_allow_orders",
                                "sim_learner_tasks",
                                "sim_image_links",
                            ]:
                                st.session_state.pop(key, None)

                            # Populate session state with DB data
                            st.session_state.generated_case = {
                                "case_details": {
                                    "case_title": sim_case["saved_name"],
                                    "paragraph_summary": "",
                                    "presentation": "",
                                    "patient_personality": "",
                                    "history_questions": [],
                                    "physical_exam_findings": [],
                                    "diagnostic_workup": [],
                                    "diagnostic_reasoning": {
                                        "differential_diagnoses": ""
                                    },
                                },
                                "diagnostic_framework": [],
                                "feature_likelihood_ratios": [],
                                "rendered_content": sim_case["content"],
                                "default_custom_input": sim_case.get("custom_input")
                                or {"Prespecified Results": "", "Image Links": []},
                                "default_custom_evaluation": sim_case.get(
                                    "custom_evaluation"
                                )
                                or {"Additional Instructions": ""},
                                "default_learner_tasks": sim_case.get("learner_tasks")
                                or "",
                                "case_id": sim_case["id"],
                                "saved_name": sim_case["saved_name"],
                            }
                            st.session_state.sim_rendered_content = sim_case["content"]
                            st.session_state.sim_custom_input = sim_case.get(
                                "custom_input"
                            ) or {"Prespecified Results": "", "Image Links": []}
                            st.session_state.sim_custom_evaluation = sim_case.get(
                                "custom_evaluation"
                            ) or {"Additional Instructions": ""}
                            st.session_state.sim_allow_orders = sim_case.get(
                                "allow_orders", True
                            )
                            st.session_state.sim_learner_tasks = (
                                sim_case.get("learner_tasks") or ""
                            )
                            st.session_state.output_format = "sim_ready"
                            st.session_state.editing_mode = True
                            st.session_state.session_id = None
                            st.session_state.editing_existing_case_id = sim_case["id"]
                            # sim_image_links is derived once ("if not in session_state")
                            # and never refreshed, so without this the previously loaded
                            # case's image links persist into this one -- and get saved
                            # onto it. Drop it so the editor re-derives from the case
                            # just loaded.
                            st.session_state.pop("sim_image_links", None)

                            st.success(
                                f"Loaded **{sim_case['saved_name']}** (ID: {sim_case['id']}) for editing."
                            )
                            st.rerun()
                        else:
                            st.error(f"Error loading case: {case_resp.text}")
                else:
                    st.info("No sim-ready cases found in the database.")
            else:
                st.warning("Could not connect to the backend to list cases.")
        except requests.exceptions.RequestException as e:
            st.error(f"Connection error: {e!s}")

with tab3:
    if st.session_state.generated_case and not st.session_state.editing_mode:
        case = st.session_state.generated_case
        is_sim_ready_view = st.session_state.output_format == "sim_ready"

        st.header(f"Final Case (ID: {case.get('case_id', 'Preview')})")

        if is_sim_ready_view:
            st.success(
                f"Sim-ready case saved: **{case.get('saved_name', '')}** (ID: {case.get('case_id', '')})"
            )

            # Fetch and display the full case from the sim-ready DB
            try:
                sim_resp = requests.get(
                    f"{BACKEND_URL}/sim-ready/case/{case.get('case_id')}"
                )
                if sim_resp.status_code == 200:
                    sim_case = sim_resp.json()

                    with st.expander("Case Content", expanded=True):
                        st.markdown(sim_case.get("content", ""))

                    col_a, col_b = st.columns(2)
                    with col_a:
                        with st.expander("Custom Input", expanded=False):
                            st.json(sim_case.get("custom_input", {}))
                        with st.expander("Custom Evaluation", expanded=False):
                            st.json(sim_case.get("custom_evaluation", {}))
                    with col_b:
                        with st.expander("Learner Tasks", expanded=False):
                            st.markdown(sim_case.get("learner_tasks", ""))
                        st.write(
                            f"**Allow Orders:** {sim_case.get('allow_orders', True)}"
                        )
                else:
                    st.error(f"Could not load case: {sim_resp.text}")
            except requests.exceptions.RequestException as e:
                st.error(f"Connection error: {e!s}")
        else:
            col1, col2 = st.columns([2, 1])

            with col1:
                st.subheader("Case Presentation")
                st.write(case["case_details"]["presentation"])

                st.subheader("Patient Personality")
                st.write(case["case_details"]["patient_personality"])

                st.subheader("History Questions")
                for i, question in enumerate(
                    case["case_details"]["history_questions"], 1
                ):
                    with st.expander(f"Question {i}: {question['question']}"):
                        st.write(f"**Expected Answer:** {question['expected_answer']}")

                st.subheader("Physical Exam Findings")
                for finding in case["case_details"]["physical_exam_findings"]:
                    with st.expander(f"{finding['examination']}"):
                        st.write(finding["findings"])

                st.subheader("Diagnostic Workup")
                for test in case["case_details"]["diagnostic_workup"]:
                    with st.expander(f"{test['test']}"):
                        st.write(f"**Rationale:** {test['rationale']}")

            with col2:
                st.subheader("Diagnostic Framework")

                for tier in case["diagnostic_framework"]:
                    st.write(f"**Tier {tier['tier_level']}**")

                    buckets_df = []
                    for bucket in tier["buckets"]:
                        prob = tier["a_priori_probabilities"].get(bucket["name"], 0)
                        buckets_df.append(
                            {
                                "Bucket": bucket["name"],
                                "Probability": f"{prob:.3f}",
                                "Description": bucket["description"],
                            }
                        )

                    st.table(buckets_df)
                    st.write("---")

                st.subheader("Feature Likelihood Ratios")

                categories = {}
                for lr in case["feature_likelihood_ratios"]:
                    cat = lr["feature_category"]
                    if cat not in categories:
                        categories[cat] = []
                    categories[cat].append(lr)

                for category, features in categories.items():
                    with st.expander(f"{category.replace('_', ' ').title()}"):
                        for feature in features:
                            st.write(f"**{feature['feature_name']}**")
                            st.write(
                                f"- {feature['diagnostic_bucket']}: {feature['likelihood_ratio']:.2f}"
                            )
    else:
        st.info("No finalized case available. Complete the editing process first.")

with tab4:
    if st.session_state.generated_case and not st.session_state.editing_mode:
        case_id = st.session_state.generated_case.get("case_id")
        is_sim_ready_export = st.session_state.output_format == "sim_ready"

        st.header("Export Case Files")

        if not case_id:
            st.warning(
                "No case ID available. Complete the editing and finalization process first."
            )
        elif is_sim_ready_export:
            # --- Sim-Ready export ---
            st.info(
                f"Sim-ready case **ID {case_id}**. This case is saved in the simulator database."
            )

            gen_case = st.session_state.generated_case
            col1, col2 = st.columns([1, 1])

            with col1:
                st.subheader("Simulator Case Files")
                try:
                    sim_case_resp = requests.get(
                        f"{BACKEND_URL}/sim-ready/case/{case_id}"
                    )
                    if sim_case_resp.status_code == 200:
                        sim_case = sim_case_resp.json()

                        st.download_button(
                            label="Download Content (Markdown)",
                            data=sim_case.get("content", ""),
                            file_name=f"sim_ready_case_{case_id}_content.md",
                            mime="text/markdown",
                        )

                        st.download_button(
                            label="Download Custom Input (JSON)",
                            data=json.dumps(sim_case.get("custom_input", {}), indent=2),
                            file_name=f"sim_ready_case_{case_id}_custom_input.json",
                            mime="application/json",
                        )

                        st.download_button(
                            label="Download Custom Evaluation (JSON)",
                            data=json.dumps(
                                sim_case.get("custom_evaluation", {}), indent=2
                            ),
                            file_name=f"sim_ready_case_{case_id}_custom_evaluation.json",
                            mime="application/json",
                        )

                        st.download_button(
                            label="Download Learner Tasks (Markdown)",
                            data=sim_case.get("learner_tasks", ""),
                            file_name=f"sim_ready_case_{case_id}_learner_tasks.md",
                            mime="text/markdown",
                        )

                        st.download_button(
                            label="Download Full Case (JSON)",
                            data=json.dumps(sim_case, indent=2),
                            file_name=f"sim_ready_case_{case_id}_full.json",
                            mime="application/json",
                        )
                    else:
                        st.error(f"Could not load sim-ready case: {sim_case_resp.text}")
                except requests.exceptions.RequestException as e:
                    st.error(f"Connection error: {e!s}")

            with col2:
                st.subheader("Diagnostic Data Files")
                # LR and framework data from session (generated but not persisted to beta DB)
                framework_data = gen_case.get("diagnostic_framework", [])
                lr_data = gen_case.get("feature_likelihood_ratios", [])
                case_details_data = gen_case.get("case_details", {})

                if framework_data or lr_data:
                    st.download_button(
                        label="Download Case Details (JSON)",
                        data=json.dumps(case_details_data, indent=2),
                        file_name=f"sim_ready_case_{case_id}_case_details.json",
                        mime="application/json",
                    )

                    st.download_button(
                        label="Download Diagnostic Framework (JSON)",
                        data=json.dumps(framework_data, indent=2),
                        file_name=f"sim_ready_case_{case_id}_diagnostic_framework.json",
                        mime="application/json",
                    )

                    st.download_button(
                        label="Download Likelihood Ratios (JSON)",
                        data=json.dumps(lr_data, indent=2),
                        file_name=f"sim_ready_case_{case_id}_likelihood_ratios.json",
                        mime="application/json",
                    )

                    # Build a priori probabilities export (same structure as beta)
                    a_priori = {}
                    for tier in framework_data:
                        tier_key = f"tier_{tier.get('tier_level', '?')}"
                        a_priori[tier_key] = {
                            "buckets": [b["name"] for b in tier.get("buckets", [])],
                            "probabilities": tier.get("a_priori_probabilities", {}),
                        }
                    st.download_button(
                        label="Download A Priori Probabilities (JSON)",
                        data=json.dumps(a_priori, indent=2),
                        file_name=f"sim_ready_case_{case_id}_a_priori_probabilities.json",
                        mime="application/json",
                    )
                else:
                    st.warning(
                        "Diagnostic framework and likelihood ratio data is only available "
                        "for export immediately after case generation. If you navigated away, "
                        "this data is no longer in memory."
                    )
        else:
            # --- Beta export: original LR/framework export ---
            try:
                export_info_response = requests.get(
                    f"{BACKEND_URL}/case/{case_id}/simulator-exports"
                )
                if export_info_response.status_code == 200:
                    export_info = export_info_response.json()

                    col1, col2 = st.columns([1, 1])

                    with col1:
                        st.subheader("Original JSON Files")
                        st.markdown("Standard case generator outputs:")

                        if st.button("Generate JSON Export Files", type="primary"):
                            try:
                                response = requests.get(
                                    f"{BACKEND_URL}/case/{case_id}/output-files"
                                )

                                if response.status_code == 200:
                                    files = response.json()

                                    st.success("JSON files generated successfully!")

                                    st.download_button(
                                        label="Download case_details.json",
                                        data=json.dumps(
                                            files["case_details_json"], indent=2
                                        ),
                                        file_name=f"case_{case_id}_details.json",
                                        mime="application/json",
                                    )

                                    st.download_button(
                                        label="Download a_priori_probabilities.json",
                                        data=json.dumps(
                                            files["a_priori_probabilities_json"],
                                            indent=2,
                                        ),
                                        file_name=f"case_{case_id}_a_priori_probabilities.json",
                                        mime="application/json",
                                    )

                                    st.download_button(
                                        label="Download feature_likelihood_ratios.json",
                                        data=json.dumps(
                                            files["feature_likelihood_ratios_json"],
                                            indent=2,
                                        ),
                                        file_name=f"case_{case_id}_feature_likelihood_ratios.json",
                                        mime="application/json",
                                    )
                                else:
                                    st.error(
                                        f"Error retrieving export files: {response.text}"
                                    )
                            except requests.exceptions.RequestException as e:
                                st.error(f"Connection error: {e!s}")

                    with col2:
                        st.subheader("Simulator App Files")
                        st.markdown(
                            "Formatted for the transcript feature check simulator:"
                        )

                        st.info(f"""
                        **Available for Case ID: {export_info["case_id"]}**
                        - {export_info["total_features"]} clinical features
                        - {export_info["total_diagnostic_buckets"]} diagnostic categories
                        - {len(export_info["available_tiers"])} diagnostic tiers
                        """)

                        # Default to tier 2 where it exists. Tier 1 is broad
                        # (cardiovascular / respiratory / ...) and tier 3 very
                        # specific; tier 2 is the level most cases are authored
                        # around. Still fully selectable -- re-export at tier 1
                        # by changing this and re-downloading.
                        _tiers = export_info["available_tiers"]
                        _default_idx = _tiers.index(2) if 2 in _tiers else 0
                        selected_tier = st.selectbox(
                            "Diagnostic Tier for export",
                            _tiers,
                            index=_default_idx,
                            help=(
                                "Which tier the LR matrix and priors are built from. "
                                "Defaults to tier 2; change it and re-download to export "
                                "another tier."
                            ),
                        )

                        col2a, col2b = st.columns(2)

                        with col2a:
                            if st.button("Download LR Matrix (CSV)", key="csv"):
                                st.markdown(
                                    f"[Download CSV]({BACKEND_URL}/case/{case_id}/simulator-export/lr-matrix-csv?tier_level={selected_tier})"
                                )

                            if st.button("Download LR Matrix (Excel)", key="excel"):
                                st.markdown(
                                    f"[Download Excel]({BACKEND_URL}/case/{case_id}/simulator-export/lr-matrix-excel?tier_level={selected_tier})"
                                )

                        with col2b:
                            if st.button("Download Prior Probabilities", key="priors"):
                                st.markdown(
                                    f"[Download Priors]({BACKEND_URL}/case/{case_id}/simulator-export/prior-probabilities?tier_level={selected_tier})"
                                )

                            if st.button("Download Case Summary", key="summary"):
                                st.markdown(
                                    f"[Download Summary]({BACKEND_URL}/case/{case_id}/simulator-export/case-summary)"
                                )

                        st.markdown("---")
                        st.markdown("**How to use with simulator:**")
                        st.markdown("""
                        1. Download the **LR Matrix** (CSV or Excel)
                        2. Download the **Prior Probabilities** for your chosen tier
                        3. Download the **Case Summary** as a transcript file
                        4. Upload these files to the [Transcript Feature Check Simulator](https://github.com/DrDavidL/transcript-feature-check)
                        """)

                else:
                    st.error("Could not load export information")
            except requests.exceptions.RequestException as e:
                st.error(f"Error loading export info: {e!s}")
    else:
        st.info(
            "No finalized case available for export. Complete the editing process first."
        )

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
    except Exception:
        st.error("❌ Backend Unavailable")

    if st.button("View All Cases"):
        try:
            cases_response = requests.get(f"{BACKEND_URL}/cases")
            if cases_response.status_code == 200:
                cases = cases_response.json()
                st.write("**Existing Cases:**")
                for case in cases:
                    st.write(f"- ID {case['id']}: {case['primary_diagnosis']}")
        except Exception:
            st.error("Could not load cases")

render_build_footer()
