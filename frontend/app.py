import json
import os
import uuid

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


# --- Final Orders (script concordance items) ---
#
# Up to five per case, and zero is the normal case: no Final Orders means the case has no
# script concordance item and no Oracle panel runs for it.

MAX_FINAL_ORDERS = 5

# Rows carry a uid and widgets are keyed by it, not by list index. Index-keyed widgets
# break on per-row delete: Streamlit keeps widget state by key, so removing row 2 leaves
# row 3's text sitting in row 2's key and the author's edits silently shuffle up.
SIM_EDIT_KEYS = [
    "sim_rendered_content",
    "sim_custom_input",
    "sim_custom_evaluation",
    "sim_allow_orders",
    "sim_learner_tasks",
    "sim_image_links",
    "sim_final_orders",
    "sim_oracle_specialty",
    "sim_save_mode",
    "sim_copy_name",
    "final_order_candidates",
    "final_orders_notice",
    "oracle_result",
    "save_notices",
    # Widget keys, not state keys, and they have to be here too. For a keyed widget
    # Streamlit uses the stored value and ignores `value=`, so resetting only the state
    # key behind it changes nothing on screen: the previous case's specialty stayed in
    # the box, was read back into sim_oracle_specialty on the next run, and was saved
    # onto the case just loaded — driving the wrong subspecialist seat on its Oracle
    # roster. Same defect as the image-link leak, one layer down.
    "edit_oracle_specialty",
    "edit_copy_name",
]


@st.cache_data(ttl=300)
def _oracle_stems():
    """The backend's stem registry.

    Fetched rather than duplicated here: the stem is the measurement instrument, and a
    second copy in the UI would drift from the one that actually gets sent to the panel.
    """
    try:
        r = requests.get(f"{BACKEND_URL}/oracle/stems", timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception:
        return {}


def _rendered_learner_items(orders):
    """The exact items learners will see, rendered by the backend.

    This used to be built here, from the registry's anchors plus a local copy of
    `default_action_phrase`. The copy had already drifted: it added an article to labels
    the real function leaves alone, so the sentence an author reviewed was not always the
    sentence the panel and the learner were given. The stem is the measurement instrument
    (ADR-005), so it has one renderer.

    Returns a dict keyed by each order's `_uid`, not by list position: blank rows are not
    sent, so response position does not line up with the editor's rows, and two orders can
    share a label while the author is still typing. Returns {} when the backend is
    unreachable, so the caller shows nothing rather than a locally invented approximation.
    """
    sent = [o for o in orders if (o.get("order_text") or "").strip()]
    if not sent:
        return {}
    try:
        r = requests.post(
            f"{BACKEND_URL}/oracle/render-items",
            json={
                "orders": [
                    {
                        "order_text": o["order_text"],
                        "stem_action": (o.get("stem_action") or "").strip() or None,
                    }
                    for o in sent
                ]
            },
            timeout=10,
        )
        r.raise_for_status()
        items = r.json().get("items", [])
    except requests.exceptions.RequestException:
        return {}
    return {o["_uid"]: item for o, item in zip(sent, items, strict=False)}


def _blank_final_order():
    return {
        "_uid": uuid.uuid4().hex[:8],
        "order_text": "",
        "stem_action": "",
        "provenance": "author_entered",
        "suppress_results": True,
        "suppression_message": "Result pending",
        "suppression_synonyms": "",
    }


def _final_orders():
    if "sim_final_orders" not in st.session_state:
        st.session_state.sim_final_orders = []
    return st.session_state.sim_final_orders


def _add_final_order():
    orders = _final_orders()
    if len(orders) >= MAX_FINAL_ORDERS:
        st.session_state.final_orders_notice = (
            "warning",
            f"A case may have at most {MAX_FINAL_ORDERS} Final Orders.",
        )
        return
    orders.append(_blank_final_order())


def _delete_final_order(uid):
    orders = _final_orders()
    st.session_state.sim_final_orders = [o for o in orders if o.get("_uid") != uid]
    # Drop the deleted row's widget state so a later row cannot inherit it.
    for prefix in ("fo_text", "fo_action", "fo_syn", "fo_msg", "fo_suppress"):
        st.session_state.pop(f"{prefix}_{uid}", None)


def _propose_final_orders():
    """Ask the backend for candidate Final Orders. Writes nothing to the database."""
    case = st.session_state.get("generated_case") or {}
    payload = {"max_candidates": MAX_FINAL_ORDERS}
    if st.session_state.get("session_id"):
        payload["session_id"] = st.session_state.session_id
    else:
        payload["case_details"] = case.get("case_details")
        payload["primary_diagnosis"] = (
            (case.get("case_details") or {})
            .get("diagnostic_reasoning", {})
            .get("differential_diagnoses", "")
        )
    try:
        r = requests.post(
            f"{BACKEND_URL}/final-orders/propose",
            json=payload,
            headers=get_auth_header(),
            timeout=300,
        )
        if r.status_code == 200:
            data = r.json()
            st.session_state.final_order_candidates = data.get("candidates", [])
            st.session_state.final_orders_notice = (
                "success",
                f"{len(st.session_state.final_order_candidates)} candidate(s) proposed. "
                "Nothing is saved until you accept one.",
            )
        else:
            st.session_state.final_orders_notice = (
                "error",
                f"Could not propose Final Orders: {r.text[:300]}",
            )
    except requests.exceptions.RequestException as e:
        st.session_state.final_orders_notice = ("error", f"Connection error: {e}")


def _accept_candidate(position):
    """Copy a proposed candidate into the author's list, recording its provenance."""
    candidates = st.session_state.get("final_order_candidates") or []
    if position >= len(candidates):
        return
    orders = _final_orders()
    if len(orders) >= MAX_FINAL_ORDERS:
        st.session_state.final_orders_notice = (
            "warning",
            f"A case may have at most {MAX_FINAL_ORDERS} Final Orders.",
        )
        return
    candidate = candidates[position]
    row = _blank_final_order()
    row.update(
        {
            "order_text": candidate.get("order_text", ""),
            "stem_action": candidate.get("stem_action", ""),
            # Recorded so a reviewer can ask whether model-proposed orders behave
            # differently from author-written ones.
            "provenance": "llm_suggested_accepted",
            "suppression_synonyms": ", ".join(
                candidate.get("suggested_synonyms") or []
            ),
        }
    )
    orders.append(row)
    st.session_state.final_orders_notice = (
        "success",
        f"Accepted **{row['order_text']}**. Edit it freely — it is yours now.",
    )


def _final_orders_payload():
    """The author's Final Orders in the shape the API expects."""
    payload = []
    for position, order in enumerate(_final_orders(), start=1):
        text = (order.get("order_text") or "").strip()
        if not text:
            continue
        synonyms = [
            s.strip()
            for s in (order.get("suppression_synonyms") or "").split(",")
            if s.strip()
        ]
        payload.append(
            {
                "order_text": text,
                "display_order": position,
                "stem_action": (order.get("stem_action") or "").strip() or None,
                "provenance": order.get("provenance", "author_entered"),
                "suppress_results": bool(order.get("suppress_results", True)),
                "suppression_message": (
                    order.get("suppression_message") or "Result pending"
                ),
                "suppression_synonyms": synonyms,
            }
        )
    return payload


def _load_final_orders_from_db(case_id):
    """Populate the editor from a saved case. Returns the resolved specialty."""
    try:
        r = requests.get(
            f"{BACKEND_URL}/sim-ready/case/{case_id}/final-orders", timeout=30
        )
        if r.status_code != 200:
            return None
        data = r.json()
        rows = []
        for order in data.get("final_orders", []):
            row = _blank_final_order()
            row.update(
                {
                    "order_text": order.get("order_text", ""),
                    "stem_action": order.get("stem_action") or "",
                    "provenance": order.get("provenance", "author_entered"),
                    "suppress_results": bool(order.get("suppress_results", True)),
                    "suppression_message": (
                        order.get("suppression_message") or "Result pending"
                    ),
                    "suppression_synonyms": ", ".join(
                        order.get("suppression_synonyms") or []
                    ),
                }
            )
            rows.append(row)
        st.session_state.sim_final_orders = rows
        return data.get("oracle_specialty")
    except requests.exceptions.RequestException:
        return None


def _load_persisted_analysis(case_id):
    """Framework, LRs, and the structured record for a saved case, from the database.

    Returns `(payload, note)`. `payload` is None when nothing could be read at all, and
    `note` is a sentence for the author explaining what is missing and why, so they can
    tell which copy they are exporting instead of guessing.

    Two endpoints because the analysis and the clinical record are separate concerns:
    `/analysis` carries the framework and LRs, `/structured` carries the case record. A
    case can legitimately have the second and not the first — cases adopted under ADR-019
    were reconstructed from markdown and their original analysis is gone — so a missing
    `/analysis` is not treated as a missing case.
    """
    payload = {}
    notes = []
    try:
        r = requests.get(f"{BACKEND_URL}/sim-ready/case/{case_id}/analysis", timeout=30)
        if r.status_code == 200:
            payload.update(r.json())
        elif r.status_code == 404:
            notes.append(
                "This case has no stored analysis; it predates the authoring record or "
                "was adopted from existing markdown."
            )
        elif r.status_code == 503:
            notes.append("Authoring persistence is unavailable on this backend.")
    except requests.exceptions.RequestException:
        notes.append("Could not reach the backend for the stored analysis.")

    try:
        r = requests.get(
            f"{BACKEND_URL}/sim-ready/case/{case_id}/structured", timeout=30
        )
        if r.status_code == 200:
            record = r.json()
            payload["content_structured"] = record.get("content_structured", {})
            payload.setdefault("version", record.get("version"))
    except requests.exceptions.RequestException:
        pass

    return (payload or None), (" ".join(notes) or None)


def _run_oracle(case_id, override_key=None):
    """Queue the Oracle panel.

    Sends a leak-audit override only when the author actually typed a reason, so the
    default path stays fail-closed.
    """
    reason = (st.session_state.get(override_key) or "").strip() if override_key else ""
    try:
        r = requests.post(
            f"{BACKEND_URL}/sim-ready/case/{case_id}/oracle/run",
            json={"leak_override_reason": reason or None},
            headers=get_auth_header(),
            timeout=60,
        )
        if r.status_code == 200:
            data = r.json()
            st.session_state.oracle_result = (
                "success",
                f"Oracle panel queued: ~{data.get('estimated_calls')} calls. This takes "
                "3-5 minutes; use Refresh to check progress."
                + (
                    " Ran with a recorded leak-audit override."
                    if data.get("leak_override_applied")
                    else ""
                ),
            )
        else:
            st.session_state.oracle_result = (
                "error",
                f"Could not start: {r.text[:400]}",
            )
    except requests.exceptions.RequestException as e:
        st.session_state.oracle_result = ("error", f"Connection error: {e}")


_FLAG_RENDERER = {"info": st.info, "caution": st.warning, "warning": st.error}


def _histogram_bars(aggregate):
    """Text bars for the five rating bins.

    Deliberately text rather than a chart: five ordinal bins with a meaningful zero read
    better as aligned bars than as a plot with a re-sorted axis, and it needs no extra
    dependency in the frontend.
    """
    histogram = aggregate.get("histogram") or {}
    realized = aggregate.get("realized_n") or 0
    labels = {
        "-2": "-2 clearly inappropriate",
        "-1": "-1 probably inappropriate",
        "0": " 0 equally appropriate or not",
        "1": "+1 probably appropriate",
        "2": "+2 clearly appropriate",
    }
    lines = []
    for key in ("-2", "-1", "0", "1", "2"):
        count = int(histogram.get(key, 0) or 0)
        share = (count / realized) if realized else 0
        lines.append(
            f"{labels[key]:<32} {'█' * count}{'·' * max(0, realized - count)} "
            f"{count:>2} ({share:.0%})"
        )
    return "\n".join(lines)


def _adopt_case(case_id, diagnosis_key, specialty_key=None):
    """Give a pre-authoring-record case its first version, read from its markdown."""
    diagnosis = (st.session_state.get(diagnosis_key) or "").strip()
    if not diagnosis:
        st.session_state.oracle_result = (
            "error",
            "Enter the case's primary diagnosis first. The Oracle's leak audit searches "
            "the blinded context for it and its synonyms, and with nothing to search for "
            "it would pass without checking anything.",
        )
        return
    payload = {"primary_diagnosis": diagnosis}
    specialty = (
        (st.session_state.get(specialty_key) or "").strip() if specialty_key else ""
    )
    if specialty:
        payload["oracle_specialty"] = specialty
    try:
        r = requests.post(
            f"{BACKEND_URL}/sim-ready/case/{case_id}/adopt",
            json=payload,
            headers=get_auth_header(),
            timeout=300,
        )
        if r.status_code == 200:
            d = r.json()
            st.session_state.oracle_result = (
                "success",
                f"Authoring record created (family {d.get('case_family_id')}, version "
                f"{d.get('version')}). You can add Final Orders and run the Oracle now. "
                f"{d.get('note', '')}",
            )
        else:
            st.session_state.oracle_result = (
                "error",
                f"Could not create the authoring record: {r.text[:300]}",
            )
    except requests.exceptions.RequestException as e:
        st.session_state.oracle_result = ("error", f"Connection error: {e}")


def _resync_case(case_id):
    """Rebuild the structured record from the edited markdown so the Oracle can run."""
    try:
        r = requests.post(
            f"{BACKEND_URL}/sim-ready/case/{case_id}/resync",
            headers=get_auth_header(),
            timeout=300,
        )
        if r.status_code == 200:
            d = r.json()
            st.session_state.oracle_result = (
                "success",
                f"Re-read the case content into version {d.get('version')}. "
                f"{d.get('final_orders_carried_forward', 0)} Final Order(s) carried "
                "forward. You can run the Oracle panel now.",
            )
        else:
            st.session_state.oracle_result = (
                "error",
                f"Re-sync failed: {r.text[:300]}",
            )
    except requests.exceptions.RequestException as e:
        st.session_state.oracle_result = ("error", f"Connection error: {e}")


def _render_oracle_section(case_id, key_prefix="view"):
    """Oracle distributions and item-quality flags for a saved case.

    `key_prefix` namespaces the widget keys. Streamlit renders every tab on each script
    run, so calling this from two tabs without it collides on duplicate keys.
    """
    if not case_id:
        return

    st.write("---")
    st.subheader("Oracle Reference Distributions")
    st.caption(
        "Model-derived reference distributions, not an expert consensus. Fifteen blinded "
        "raters with different practice perspectives; the spread is what tells you whether "
        "an item will discriminate between learners."
    )

    notice = st.session_state.pop("oracle_result", None)
    if notice:
        getattr(st, notice[0])(notice[1])

    try:
        r = requests.get(f"{BACKEND_URL}/sim-ready/case/{case_id}/oracle", timeout=30)
    except requests.exceptions.RequestException as e:
        st.error(f"Connection error: {e}")
        return

    if r.status_code == 503:
        st.warning(
            "Final Orders and the Oracle are unavailable on this backend — the shared "
            "database is missing the Phase 2/3 tables. Run `alembic upgrade head` in the "
            "direct-sim repo."
        )
        return
    if r.status_code == 404:
        st.info(
            "This case predates the authoring record, so it has no structured record "
            "behind it — which is what Final Orders attach to and what the Oracle reads. "
            "Reading its content into one fixes that. It costs one model call and does "
            "not change what the simulator serves."
        )
        st.caption(
            "Two things to know first. The diagnostic framework and likelihood ratios "
            "were never stored for cases of this vintage, so this record starts without "
            "them. And any clinical detail the document does not state is reconstructed "
            "by the model, so read the case through afterwards."
        )
        st.text_input(
            "Primary diagnosis for this case",
            key=f"{key_prefix}_adopt_dx_{case_id}",
            placeholder="posterior circulation stroke",
            help="Withheld from the panel. The leak audit searches the blinded context "
            "for this term and its synonyms — without it the audit has nothing to check "
            "and would pass vacuously, which is why it is required here.",
        )
        st.text_input(
            "Applicable specialty for the Oracle panel (optional)",
            key=f"{key_prefix}_adopt_spec_{case_id}",
            placeholder="otolaryngologist",
        )
        st.button(
            "Read this case's content into an authoring record",
            key=f"{key_prefix}_adopt_{case_id}",
            on_click=_adopt_case,
            args=(
                case_id,
                f"{key_prefix}_adopt_dx_{case_id}",
                f"{key_prefix}_adopt_spec_{case_id}",
            ),
        )
        return
    if r.status_code != 200:
        st.error(f"Could not load Oracle data: {r.text[:300]}")
        return

    data = r.json()
    items = data.get("items") or []
    if not items:
        st.info(
            "No Final Orders on this case, so there is no script concordance item and no "
            "Oracle panel to run. That is a supported configuration, not a gap."
        )
        return

    col_run, col_refresh, _ = st.columns([1, 1, 3])
    with col_run:
        st.button(
            "Run Oracle panel",
            key=f"{key_prefix}_run_oracle_{case_id}",
            on_click=_run_oracle,
            args=(case_id,),
        )
    with col_refresh:
        # A plain button re-runs the script, which re-fetches. No st.rerun() here: that
        # would eject the user back to the first tab.
        st.button("Refresh", key=f"{key_prefix}_refresh_oracle_{case_id}")

    with st.expander(
        "What the panel sees (blinded context + leak audit)", expanded=False
    ):
        try:
            pre = requests.get(
                f"{BACKEND_URL}/sim-ready/case/{case_id}/oracle/preflight",
                headers=get_auth_header(),
                timeout=60,
            )
            if pre.status_code == 200:
                preflight = pre.json()
                audit = preflight.get("leak_audit") or {}

                parity = preflight.get("content_parity") or {}
                if parity and not parity.get("in_parity"):
                    st.error(
                        "**Blocking — the panel would rate a different case than the "
                        f"learner sees.** {parity.get('message', '')}"
                    )
                    if parity.get("reason") in ("content_drift", "render_detached"):
                        st.caption(
                            "Re-reading the case content rebuilds the structured record "
                            "from the document the simulator now serves, which restores "
                            "parity and lets the panel run. It costs one model call, "
                            "creates a new version, and carries your Final Orders "
                            "forward. Any detail the document does not state is "
                            "reconstructed, so review the case afterwards."
                        )
                        st.button(
                            "Re-read case content and create a new version",
                            key=f"{key_prefix}_resync_{case_id}",
                            on_click=_resync_case,
                            args=(case_id,),
                        )
                elif parity.get("in_parity"):
                    st.success(
                        "Content parity confirmed — the case content the simulator "
                        "serves matches the record the panel reads."
                    )

                if preflight.get("ready"):
                    st.success(
                        "Leak audit passed — the diagnosis and its known synonyms do not "
                        f"appear in the blinded context ({len(audit.get('terms_checked', []))} "
                        "terms checked)."
                    )
                else:
                    st.error(
                        "**Blocking:** the diagnosis or a known synonym appears in the "
                        "blinded context. The panel will not run until this is fixed, or "
                        "until you record why the hit is benign."
                    )
                    for hit in audit.get("hits", []):
                        st.markdown(
                            f"- **{hit.get('term')}** ({hit.get('kind')}, in "
                            f"_{hit.get('section', 'unknown section')}_): "
                            f"`{hit.get('snippet', '')}`"
                        )
                    st.caption(
                        "A hit under Family History is usually a relative's condition "
                        "rather than this patient's diagnosis. If that is the case here, "
                        "say so and run anyway — the reason is stored on the run."
                    )
                    st.text_input(
                        "Reason for overriding the leak audit",
                        key=f"{key_prefix}_leak_override_{case_id}",
                        placeholder="CVA appears only as the father's history, not this patient's",
                    )
                    st.button(
                        "Run anyway with this reason",
                        key=f"{key_prefix}_run_oracle_override_{case_id}",
                        on_click=_run_oracle,
                        args=(case_id, f"{key_prefix}_leak_override_{case_id}"),
                    )
                st.caption(
                    f"Stem: {preflight.get('stem_version')} · roster "
                    f"{preflight.get('panel_roster_version')} · specialty seat: "
                    f"{preflight.get('roster_specialty')} · ~"
                    f"{preflight.get('estimated_calls')} calls"
                )
                if preflight.get("suppressed_tests"):
                    st.caption(
                        "Withheld from the panel's available-tests list: "
                        + ", ".join(preflight["suppressed_tests"])
                    )
                st.code(preflight.get("blinded_context", ""), language="markdown")
            else:
                st.warning(f"Preflight unavailable: {pre.text[:300]}")
        except requests.exceptions.RequestException as e:
            st.warning(f"Preflight unavailable: {e}")

    for item in items:
        order = item.get("final_order") or {}
        run = item.get("run")
        aggregate = item.get("aggregate")

        st.markdown(f"#### {order.get('order_text', 'Final Order')}")

        if run is None:
            st.info("No Oracle panel has run for this order yet.")
            continue

        status = run.get("status")
        if status in ("pending", "running"):
            st.info(
                f"Panel {status}. 15 calls per order takes 3-5 minutes; press Refresh."
            )
            continue
        if status == "failed":
            st.error(f"Panel failed: {run.get('error') or 'unknown error'}")
            continue

        if item.get("stale"):
            st.warning(
                "**Stale.** The case content has changed since this panel ran, so this "
                "distribution describes a version the learner will not see. Re-run it."
            )

        if not aggregate:
            continue

        m1, m2, m3, m4 = st.columns(4)
        modal = aggregate.get("modal_rating")
        m1.metric("Mode", f"{modal:+d}" if isinstance(modal, int) else "—")
        m2.metric(
            "Agreement",
            f"{aggregate['modal_proportion']:.0%}"
            if aggregate.get("modal_proportion") is not None
            else "—",
        )
        m3.metric(
            "Entropy",
            f"{aggregate['entropy']:.2f}"
            if aggregate.get("entropy") is not None
            else "—",
            help="0 = unanimous, 2.32 = maximum disagreement across the five bins.",
        )
        m4.metric(
            "Panel",
            f"{aggregate.get('realized_n', 0)}/{aggregate.get('requested_n', 0)}",
            help="Usable ratings over requested. Failed calls are excluded from every "
            "proportion rather than counted as anything.",
        )

        st.code(_histogram_bars(aggregate), language="text")

        for flag in aggregate.get("flags") or []:
            _FLAG_RENDERER.get(flag.get("severity"), st.info)(flag.get("message", ""))

        if aggregate.get("null_outcomes"):
            st.caption(f"Excluded calls: {aggregate['null_outcomes']}")

        with st.expander("Panelist reasoning", expanded=False):
            st.caption(
                f"Model {run.get('model')} · effort {run.get('reasoning_effort')} · stem "
                f"{run.get('stem_version')} · roster {run.get('panel_roster_version')}"
            )
            for rating in run.get("ratings") or []:
                value = (rating.get("value") or {}).get("rating")
                head = (
                    f"**{rating.get('persona_id')}** — "
                    f"{f'{value:+d}' if isinstance(value, int) else rating.get('status')}"
                )
                st.markdown(head)
                if rating.get("rationale"):
                    st.caption(rating["rationale"])
                if rating.get("top_concerns"):
                    st.caption("Concerns: " + ", ".join(rating["top_concerns"]))
                if rating.get("error"):
                    st.caption(f"Error: {rating['error']}")


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
                f"Editing existing case **ID {st.session_state.editing_existing_case_id}**. "
                "Make your changes, then choose how the save is recorded at the bottom of "
                "this tab — a new version by default, so the edit is on the record and "
                "the case's history stays intact."
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

            # --- Final Orders (script concordance items) ---
            st.subheader("Final Orders (Script Concordance)")

            fo_supported = bool(_backend_status().get("final_orders"))
            if not fo_supported:
                st.warning(
                    "The backend reports Final Orders as unavailable — the shared database "
                    "is missing `case_final_orders` / `panel_runs` / `panel_ratings`. Run "
                    "`alembic upgrade head` in the direct-sim repo. Orders entered here "
                    "would not be saved."
                )

            st.caption(
                "Up to five clinical actions whose **appropriateness** the learner rates "
                "from -2 to +2 after the encounter. Leaving this empty is normal and fully "
                "supported: a case with no Final Orders has no script concordance item, and "
                "no Oracle panel runs for it."
            )

            notice = st.session_state.pop("final_orders_notice", None)
            if notice:
                getattr(st, notice[0])(notice[1])

            with st.expander("Suggest candidates with AI", expanded=False):
                st.caption(
                    "Suggestions only — nothing is written until you accept one. Accepted "
                    "candidates are recorded as model-proposed so the effect of that can be "
                    "tested later."
                )
                st.button(
                    "Propose Final Orders",
                    key="propose_final_orders",
                    on_click=_propose_final_orders,
                    disabled=not st.session_state.get("session_id")
                    and not case.get("case_details"),
                )

                for position, candidate in enumerate(
                    st.session_state.get("final_order_candidates") or []
                ):
                    st.markdown(
                        f"**{position + 1}. {candidate.get('order_text', '')}**"
                    )
                    st.caption(
                        f"Why it discriminates: {candidate.get('debatability', '')}"
                    )
                    if candidate.get("learner_item_preview"):
                        st.code(candidate["learner_item_preview"], language="text")
                    if candidate.get("suggested_synonyms"):
                        st.caption(
                            "Suggested suppression synonyms: "
                            + ", ".join(candidate["suggested_synonyms"])
                        )
                    st.button(
                        "Accept",
                        key=f"accept_candidate_{position}",
                        on_click=_accept_candidate,
                        args=(position,),
                    )
                    st.write("---")

            orders = _final_orders()
            if not orders:
                st.info("No Final Orders on this case.")

            # One render call for every order, before the row loop. Read through
            # st.session_state rather than the stored dicts: on this rerun those still
            # hold the previous run's values, because the loop below is what copies the
            # widgets back into them. Using them here would show the author the item for
            # what they typed one keystroke ago.
            rendered_items = _rendered_learner_items(
                [
                    {
                        "_uid": o["_uid"],
                        "order_text": st.session_state.get(
                            f"fo_text_{o['_uid']}", o.get("order_text", "")
                        ),
                        "stem_action": st.session_state.get(
                            f"fo_action_{o['_uid']}", o.get("stem_action", "")
                        ),
                    }
                    for o in orders
                ]
            )

            for order in orders:
                uid = order["_uid"]
                label = order.get("order_text") or "New Final Order"
                with st.expander(f"{label}", expanded=not order.get("order_text")):
                    tag = (
                        "AI-proposed, accepted by you"
                        if order.get("provenance") == "llm_suggested_accepted"
                        else "Entered by you"
                    )
                    st.caption(f"Provenance: {tag}")

                    order["order_text"] = st.text_input(
                        "Order",
                        value=order.get("order_text", ""),
                        key=f"fo_text_{uid}",
                        placeholder="Brain MRI",
                        help="Short label. Also used by the simulator to recognise the order.",
                    )
                    order["stem_action"] = st.text_input(
                        "Phrasing inside the rating item",
                        value=order.get("stem_action", ""),
                        key=f"fo_action_{uid}",
                        placeholder="ordering a brain MRI",
                        help="Leave blank for tests and treatments. Set it for activations "
                        "and consults, where 'ordering <label>' reads wrong.",
                    )

                    preview = rendered_items.get(uid)
                    if preview:
                        st.caption("The learner will read:")
                        st.code(preview["learner_item"], language="text")

                    order["suppress_results"] = st.checkbox(
                        "Withhold the result during the encounter",
                        value=bool(order.get("suppress_results", True)),
                        key=f"fo_suppress_{uid}",
                        help="Uncertainty has to survive until the rating is collected, or "
                        "the rating measures nothing.",
                    )
                    order["suppression_synonyms"] = st.text_input(
                        "Suppression synonyms (comma-separated)",
                        value=order.get("suppression_synonyms", ""),
                        key=f"fo_syn_{uid}",
                        placeholder="MRI, MRI brain, MR brain, magnetic resonance",
                        help="Alternate phrasings a learner might type. Be specific — a "
                        "broad term like 'imaging' would suppress unrelated orders, and a "
                        "brain-MRI entry must not suppress 'MRI lumbar spine'.",
                    )
                    order["suppression_message"] = st.text_input(
                        "Message shown instead of the result",
                        value=order.get("suppression_message", "Result pending"),
                        key=f"fo_msg_{uid}",
                    )

                    st.button(
                        "Delete this Final Order",
                        key=f"fo_del_{uid}",
                        on_click=_delete_final_order,
                        args=(uid,),
                    )

            col_add_fo, col_spec = st.columns([1, 2])
            with col_add_fo:
                st.button(
                    "Add Final Order",
                    key="add_final_order",
                    on_click=_add_final_order,
                    disabled=len(orders) >= MAX_FINAL_ORDERS,
                )
            with col_spec:
                st.session_state.sim_oracle_specialty = st.text_input(
                    "Applicable specialty for the Oracle panel",
                    value=st.session_state.get("sim_oracle_specialty") or "",
                    key="edit_oracle_specialty",
                    placeholder="otolaryngologist",
                    help="Fills the specialty-surgeon / subspecialist seat on the 15-role "
                    "panel. Leave blank for a generalist reading of that seat.",
                )

            if orders:
                st.checkbox(
                    "Run the Oracle panel after saving",
                    key="run_oracle_on_save",
                    help="15 blinded raters per order, 3-5 minutes in the background. You "
                    "can also start it from here once the case is saved.",
                )

            # For a case already in the database, the Oracle lives here too. Sending an
            # author to another tab to run the panel on the orders they are editing makes
            # the loop harder to close than it needs to be.
            if st.session_state.get("editing_existing_case_id"):
                _render_oracle_section(
                    st.session_state.editing_existing_case_id, key_prefix="edit"
                )

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

        # How an edit to a saved case is recorded. Explicit because the old behaviour —
        # always overwrite — bypassed versioning (ADR-003) and gave no sign it had: the
        # edit left no record, and the Oracle silently started rating the pre-edit case.
        save_mode = "new_version"
        if is_loaded_from_db:
            st.markdown("**How should this save be recorded?**")
            save_mode = st.radio(
                "Save mode",
                options=["new_version", "new_case", "in_place"],
                format_func=lambda m: {
                    "new_version": "New version of this case (recommended)",
                    "new_case": "New case — a separate case, forked from this one",
                    "in_place": "Correction — overwrite, record no version",
                }[m],
                key="sim_save_mode",
                label_visibility="collapsed",
            )
            if save_mode == "new_version":
                st.caption(
                    "The simulator keeps this case's ID and link, and the edit is "
                    "recorded as a new version with lineage back to the current one. "
                    "Learner runs and Oracle panels stay pinned to the version they "
                    "actually saw. If you changed the case content, it is re-read into "
                    "the structured record so the Oracle rates what the learner sees — "
                    "one model call."
                )
            elif save_mode == "new_case":
                st.caption(
                    "Creates a separate case with its own ID and its own version "
                    "history, leaving this one untouched. For a genuine variant, not for "
                    "an edit: performance across versions of one case is comparable, "
                    "across a fork it is not."
                )
                # Widget keyed separately from the state key it feeds, matching
                # sim_oracle_specialty above. Seeding a widget's own key with `value=`
                # and then clearing that key after a save is two ways Streamlit refuses
                # to let you touch a live widget's state.
                st.session_state.sim_copy_name = st.text_input(
                    "Name for the new case",
                    value=st.session_state.get("sim_copy_name")
                    or f"{case.get('saved_name', 'Case')} (copy)",
                    key="edit_copy_name",
                )
            else:
                st.caption(
                    "Overwrites the saved case with no version recorded. For typos and "
                    "formatting. If you changed the case content, the structured record "
                    "is left behind and the Oracle will refuse to run until the content "
                    "is re-read."
                )

        col1, col2, col3 = st.columns(3)

        with col1:
            if st.button("Save Edits", type="primary"):
                st.success("Edits saved to session!")

        with col2:
            if st.button("Regenerate AI Content"):
                st.info("Feature coming soon: Regenerate specific sections")

        with col3:
            save_label = (
                {
                    "new_version": "Save as New Version",
                    "new_case": "Save as New Case",
                    "in_place": "Overwrite Case",
                }[save_mode]
                if is_loaded_from_db
                else "Finalize & Save to Database"
            )
            if st.button(save_label, type="primary"):
                # Anything drawn here is lost: this block ends in st.rerun(), which
                # re-executes the script from the top. Outcomes are stashed and rendered
                # by the confirmation block instead.
                save_notices = []
                try:
                    if is_loaded_from_db:
                        # UPDATE path: PUT /sim-ready/case/{id}, or POST .../copy for a
                        # fork. Both write a new case_version; only the fork writes a new
                        # simulator row.
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
                        if save_mode == "new_case":
                            update_payload["saved_name"] = (
                                st.session_state.get("sim_copy_name") or ""
                            ).strip()
                            save_response = requests.post(
                                f"{BACKEND_URL}/sim-ready/case/{existing_id}/copy",
                                json=update_payload,
                                headers=get_auth_header(),
                                timeout=300,
                            )
                        else:
                            update_payload["save_mode"] = save_mode
                            save_response = requests.put(
                                f"{BACKEND_URL}/sim-ready/case/{existing_id}",
                                json=update_payload,
                                headers=get_auth_header(),
                                timeout=300,
                            )

                        # Final Orders live on the authoring record, not on case_details,
                        # so they are a second call. Done unconditionally: an empty list
                        # is the author saying "this case has no Final Orders", and
                        # skipping the call would leave deleted orders attached. It has to
                        # follow the save, because the version it writes to is the one the
                        # save just created.
                        if is_sim_ready and save_response.status_code == 200:
                            saved = save_response.json()
                            if saved.get("note"):
                                save_notices.append(("info", saved["note"]))
                            if saved.get("version") and save_mode != "in_place":
                                save_notices.append(
                                    (
                                        "success",
                                        f"Recorded as version {saved['version']}"
                                        + (
                                            f", forked from version "
                                            f"{saved.get('parent_version_id')}"
                                            if save_mode == "new_case"
                                            else ""
                                        )
                                        + ". "
                                        + f"{saved.get('final_orders_carried_forward', 0)} "
                                        "Final Order(s) carried forward.",
                                    )
                                )
                            if saved.get("structured_resynced"):
                                save_notices.append(
                                    (
                                        "info",
                                        "The edited content was re-read into the "
                                        "structured record, so the Oracle rates the case "
                                        "you just saved.",
                                    )
                                )
                            elif saved.get("parity_broken"):
                                save_notices.append(
                                    (
                                        "warning",
                                        "The case content changed but the structured "
                                        "record was not re-read, so the Oracle will "
                                        "refuse to run until it is. Use **Re-read case "
                                        "content** in the Oracle section.",
                                    )
                                )
                            # A fork's orders belong to the new case, not the source.
                            fo_target = saved.get("case_id", existing_id)
                            fo_response = requests.put(
                                f"{BACKEND_URL}/sim-ready/case/{fo_target}/final-orders",
                                json={
                                    "final_orders": _final_orders_payload(),
                                    "oracle_specialty": st.session_state.get(
                                        "sim_oracle_specialty"
                                    )
                                    or None,
                                    "run_oracle": bool(
                                        st.session_state.get("run_oracle_on_save")
                                    ),
                                },
                                headers=get_auth_header(),
                            )
                            if fo_response.status_code == 404:
                                save_notices.append(
                                    (
                                        "warning",
                                        "Case content saved, but this case has no "
                                        "authoring record, so **Final Orders were not "
                                        "attached**. Open the Oracle section in this tab "
                                        "and read the case content into an authoring "
                                        "record first, then save again.",
                                    )
                                )
                            elif fo_response.status_code != 200:
                                save_notices.append(
                                    (
                                        "warning",
                                        "Case content saved, but Final Orders failed: "
                                        f"{fo_response.text[:300]}",
                                    )
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
                            finalize_payload["final_orders"] = _final_orders_payload()
                            finalize_payload["oracle_specialty"] = (
                                st.session_state.get("sim_oracle_specialty") or None
                            )
                            finalize_payload["run_oracle"] = bool(
                                st.session_state.get("run_oracle_on_save")
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
                        # Only the plain state key. `edit_copy_name` is the live widget's
                        # own key and Streamlit rejects touching it after instantiation;
                        # SIM_EDIT_KEYS clears it at load time instead, when the widget
                        # is not on screen.
                        st.session_state.pop("sim_copy_name", None)
                        if final_case.get("final_orders_saved"):
                            save_notices.append(
                                (
                                    "info",
                                    f"{final_case['final_orders_saved']} Final Order(s) "
                                    "saved."
                                    + (
                                        " The Oracle panel is running in the background "
                                        "— see the View Final Case tab in a few minutes."
                                        if final_case.get("oracle_started")
                                        else ""
                                    ),
                                )
                            )
                        st.session_state.save_notices = save_notices
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
        # Stashed by the save handler, which ends in st.rerun() and so cannot render them
        # itself. Popped before the banner regardless of format, so a stale notice cannot
        # survive into a later save. Warnings here are load-bearing: "Final Orders were
        # not attached" under a green success banner is exactly how the authoring-record
        # gap went unnoticed.
        notices = st.session_state.pop("save_notices", [])
        if is_sim_ready:
            st.success(
                f"**{case.get('saved_name', 'Case')}** saved to the simulator database (ID: {case_id})."
            )
            for level, text in notices:
                getattr(st, level)(text)
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
            # Clear sim-ready editing state. Driven off one list so a new derived key
            # cannot be forgotten here — that omission is what leaked one case's image
            # links into another.
            for key in SIM_EDIT_KEYS:
                st.session_state.pop(key, None)
            st.session_state.pop("run_oracle_on_save", None)
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
                            for key in SIM_EDIT_KEYS:
                                st.session_state.pop(key, None)
                            st.session_state.pop("run_oracle_on_save", None)

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

                            # Same hazard for Final Orders, with a worse consequence: the
                            # previous case's orders would attach to this one and drive
                            # its suppression and its Oracle panel.
                            st.session_state.pop("sim_final_orders", None)
                            specialty = _load_final_orders_from_db(sim_case["id"])
                            st.session_state.sim_oracle_specialty = specialty or ""

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

            _render_oracle_section(case.get("case_id"))
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
                # Persisted first, session state only as a fallback. This data has been in
                # the database since ADR-001; reading it from `st.session_state` meant a
                # refresh or a reopened case lost exports that were sitting in Postgres.
                analysis, note = _load_persisted_analysis(case_id)
                stored = analysis or {}
                framework_data = stored.get("diagnostic_framework") or gen_case.get(
                    "diagnostic_framework", []
                )
                lr_data = stored.get("feature_likelihood_ratios") or gen_case.get(
                    "feature_likelihood_ratios", []
                )
                case_details_data = stored.get("content_structured") or gen_case.get(
                    "case_details", {}
                )

                if stored.get("diagnostic_framework"):
                    st.caption(
                        f"Loaded from the case record (version {stored.get('version')}). "
                        "This no longer depends on the generation session."
                    )
                elif framework_data or lr_data:
                    st.caption(
                        "Loaded from this session, not the case record. " + (note or "")
                    )

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
                        "No diagnostic framework or likelihood ratio data is available "
                        "for this case. "
                        + (note or "It is in neither the case record nor this session.")
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
