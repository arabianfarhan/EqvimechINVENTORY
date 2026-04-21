import os
import subprocess
import datetime as dt
import pandas as pd
import streamlit as st
from st_keyup import st_keyup

from db import (
    analyze_parts_import,
    bootstrap_parts_catalog,
    deposit_stock,
    get_conn,
    get_dashboard_metrics,
    get_machine_usage,
    get_part,
    get_parts,
    get_top_consumed_items,
    get_dashboard_metrics_with_category,
    get_top_consumed_items_by_category,
    get_machine_usage_by_category,
    import_parts_from_csv,
    auto_classify_parts,
    init_db,
    list_open_returnable_issues,
    list_returned_returnable_issues,
    list_transactions,
    low_stock_alerts,
    pick_material,
    return_issue_material,
    rows_to_dicts,
    save_master_table,
    DB_PATH,
    ITEMS_SNAPSHOT_CSV_PATH,
    sync_parts_snapshot_csv,
)

st.set_page_config(
    page_title="Eqvimech Inventory",
    page_icon="🏭",
    layout="centered",
    initial_sidebar_state="auto",
)

APP_DIR = os.path.dirname(os.path.abspath(__file__))
LOGO_PATH = os.path.join(APP_DIR, "assets", "eqvimech_logo.svg")
RESET_EMPTY_MARKER = ".reset_empty_app"
MASTER_SAVE_PASSWORD = "7089"
MASTER_TABLE_COLUMNS = [
    "id",
    "part_id",
    "name",
    "description",
    "unit",
    "quantity",
    "location",
    "min_level",
    "reorder_qty",
    "category",
    "active",
]
MASTER_CATEGORY_OPTIONS = ["Hardware", "Electronics", "Metals", "Others"]


def safe_rerun():
    getattr(st, "rerun", getattr(st, "experimental_rerun", lambda: None))()


def render_brand_logo(width=220):
    if os.path.exists(LOGO_PATH):
        st.image(LOGO_PATH, width=width)
    else:
        st.markdown("## EQVIMECH")


def live_search_input(label, placeholder, key):
    return st_keyup(
        label,
        key=key,
        placeholder=placeholder,
        label_visibility="collapsed",
        debounce=150,
    )


def next_import_upload_key():
    return f"im_upload_{st.session_state.get('im_upload_nonce', 0)}"


def clear_import_review_state(reset_uploader=False):
    st.session_state.pop("im_import_preview", None)
    if reset_uploader:
        st.session_state["im_upload_nonce"] = st.session_state.get("im_upload_nonce", 0) + 1


def format_import_summary(summary, prefix="Import complete"):
    parts = [
        f"{summary['inserted']} new",
        f"{summary['updated']} updated",
        f"{summary['unchanged']} unchanged",
    ]
    if summary.get("near_duplicate_rows"):
        parts.append(f"{summary['near_duplicate_rows']} possible duplicate(s) to review")
    if summary["duplicate_rows"]:
        parts.append(f"{summary['duplicate_rows']} duplicate row(s) skipped")
    if summary["invalid_rows"]:
        parts.append(f"{summary['invalid_rows']} invalid row(s)")
    return prefix + ": " + ", ".join(parts) + "."


def build_master_table_dataframe(conn):
    rows = rows_to_dicts(get_parts(conn, active_only=False))
    prepared = []
    for row in rows:
        prepared.append(
            {
                "id": int(row.get("id", 0)),
                "part_id": str(row.get("part_id", "") or "").strip(),
                "name": str(row.get("name", "") or "").strip(),
                "description": str(row.get("description", "") or "").strip(),
                "unit": str(row.get("unit", "Nos") or "Nos").strip() or "Nos",
                "quantity": int(row.get("quantity", 0) or 0),
                "location": str(row.get("location", "") or "").strip(),
                "min_level": int(row.get("min_level", 0) or 0),
                "reorder_qty": int(row.get("reorder_qty", 0) or 0),
                "category": str(row.get("category", "Others") or "Others").strip() or "Others",
                "active": bool(row.get("active", 1)),
            }
        )
    return pd.DataFrame(prepared, columns=MASTER_TABLE_COLUMNS)


def blank_master_table_row():
    return {
        "id": None,
        "part_id": "",
        "name": "",
        "description": "",
        "unit": "Nos",
        "quantity": 0,
        "location": "",
        "min_level": 0,
        "reorder_qty": 0,
        "category": "Others",
        "active": True,
    }


def _master_table_signature_from_dataframe(dataframe):
    normalized = dataframe.fillna("")
    return tuple(tuple(row) for row in normalized[MASTER_TABLE_COLUMNS].itertuples(index=False, name=None))


def sync_master_table_draft_from_db(conn, force=False):
    fresh_df = build_master_table_dataframe(conn)
    fresh_signature = _master_table_signature_from_dataframe(fresh_df)
    if force or st.session_state.get("im_master_db_signature") != fresh_signature:
        st.session_state["im_master_table_df"] = fresh_df
        st.session_state["im_master_db_signature"] = fresh_signature
        st.session_state.pop("im_master_editor", None)


def reset_master_table_draft(conn):
    sync_master_table_draft_from_db(conn, force=True)


def style_import_preview_dataframe(dataframe):
    def style_row(row):
        review_status = row.get("review_status", "")
        duplicate_alert = row.get("possible_duplicate", "")
        if review_status in {"Invalid", "Duplicate in CSV"}:
            return ["background-color: #fef2f2"] * len(row)
        if duplicate_alert:
            return ["background-color: #fffbeb"] * len(row)
        if review_status == "New":
            return ["background-color: #f0fdf4"] * len(row)
        if review_status == "Update":
            return ["background-color: #eff6ff"] * len(row)
        return [""] * len(row)

    return dataframe.style.apply(style_row, axis=1)


def import_preview_dataframe(preview):
    rows = []
    action_labels = {
        "insert": "New",
        "update": "Update",
        "unchanged": "Unchanged",
        "duplicate": "Duplicate in CSV",
        "invalid": "Invalid",
    }
    for entry in preview["rows"]:
        payload = entry.get("payload") or {}
        rows.append(
            {
                "csv_row": entry["row_number"],
                "review_status": action_labels.get(entry["action"], entry["action"]),
                "review_note": entry.get("note", ""),
                "possible_duplicate": entry.get("duplicate_alert", ""),
                "item_code": payload.get("part_id", ""),
                "name": payload.get("name", ""),
                "description": payload.get("description", ""),
                "unit": payload.get("unit", ""),
                "quantity": payload.get("quantity", ""),
                "location": payload.get("location", ""),
                "min_level": payload.get("min_level", ""),
                "reorder_qty": payload.get("reorder_qty", ""),
                "active": payload.get("active", ""),
            }
        )
    return pd.DataFrame(rows)


def inject_theme():
    st.markdown(
        """
        <style>
        :root {
            --glass-bg: rgba(255, 255, 255, 0.30);
            --glass-bg-strong: rgba(255, 255, 255, 0.46);
            --glass-bg-soft: rgba(255, 255, 255, 0.20);
            --glass-stroke: rgba(255, 255, 255, 0.45);
            --glass-stroke-strong: rgba(148, 163, 184, 0.28);
            --glass-shadow: 0 18px 45px rgba(15, 23, 42, 0.12);
            --glass-shadow-soft: 0 10px 24px rgba(15, 23, 42, 0.08);
            --glass-blur: blur(18px);
            --glass-text: #0f172a;
            --glass-muted: #475569;
            --glass-accent: #14b8a6;
            --glass-accent-strong: #0f766e;
        }

        [data-testid="stAppViewContainer"] {
            background:
                radial-gradient(circle at top left, rgba(34, 211, 238, 0.22), transparent 26%),
                radial-gradient(circle at top right, rgba(251, 191, 36, 0.20), transparent 22%),
                radial-gradient(circle at bottom left, rgba(59, 130, 246, 0.16), transparent 28%),
                linear-gradient(135deg, #eef6ff 0%, #edfdf8 48%, #f7fbff 100%) !important;
        }
        [data-testid="stHeader"] {
            background: rgba(255, 255, 255, 0.08) !important;
        }
        .main > div {
            background: transparent !important;
        }

        /* ── Layout ── */
        .block-container {
            padding-top: 2.5rem !important;
            padding-bottom: 4rem !important;
            max-width: 880px !important;
        }
        @media (min-width: 1100px) {
            .block-container {
                padding-top: 3.5rem !important;
            }
        }
        @media (max-width: 640px) {
            .block-container {
                padding-left: 0.75rem !important;
                padding-right: 0.75rem !important;
            }
            div[role="dialog"] .stCheckbox {
                margin-bottom: 0.15rem !important;
            }
            div[role="dialog"] .stButton > button[kind="primary"] {
                font-size: 0.9rem !important;
            }
            div[role="dialog"] .stButton > button[kind="secondary"] {
                justify-content: center !important;
                text-align: center !important;
            }
            div[role="dialog"] .stButton > button {
                min-height: 2.6rem !important;
            }
        }

        /* ── Sidebar ── */
        section[data-testid="stSidebar"] {
            background: rgba(255, 255, 255, 0.18) !important;
            backdrop-filter: var(--glass-blur) !important;
            -webkit-backdrop-filter: var(--glass-blur) !important;
            border-right: 1px solid var(--glass-stroke) !important;
            box-shadow: inset -1px 0 0 rgba(255, 255, 255, 0.22) !important;
        }
        section[data-testid="stSidebar"] > div {
            background: transparent !important;
        }

        /* ── Tabs ── */
        div[data-baseweb="tab-list"] {
            background: var(--glass-bg) !important;
            backdrop-filter: var(--glass-blur) !important;
            -webkit-backdrop-filter: var(--glass-blur) !important;
            border-radius: 18px !important;
            padding: 6px !important;
            gap: 6px !important;
            border: 1px solid var(--glass-stroke) !important;
            box-shadow: var(--glass-shadow-soft) !important;
            overflow-x: auto !important;
        }
        button[data-baseweb="tab"] {
            background: rgba(255, 255, 255, 0.10) !important;
            color: var(--glass-muted) !important;
            border-radius: 14px !important;
            font-weight: 700 !important;
            font-size: 0.82rem !important;
            padding: 0.44rem 0.82rem !important;
            border: 1px solid transparent !important;
            white-space: nowrap !important;
        }
        button[data-baseweb="tab"][aria-selected="true"] {
            background: linear-gradient(135deg, rgba(20, 184, 166, 0.88), rgba(14, 116, 144, 0.78)) !important;
            color: #ffffff !important;
            box-shadow: 0 10px 24px rgba(13, 148, 136, 0.20) !important;
        }
        div[data-baseweb="tab-panel"] { padding-top: 1rem !important; }
        div[data-baseweb="tab-highlight"] { display: none !important; }

        div[data-testid="stButtonGroup"] {
            background: var(--glass-bg) !important;
            backdrop-filter: var(--glass-blur) !important;
            -webkit-backdrop-filter: var(--glass-blur) !important;
            border: 1px solid var(--glass-stroke) !important;
            border-radius: 18px !important;
            padding: 0.4rem !important;
            box-shadow: var(--glass-shadow-soft) !important;
        }
        div[data-testid="stButtonGroup"] > div {
            width: 100% !important;
        }
        div[data-testid="stButtonGroup"] button[data-testid^="stBaseButton-pills"] {
            background: rgba(255, 255, 255, 0.22) !important;
            border: 1px solid rgba(255, 255, 255, 0.18) !important;
            color: var(--glass-muted) !important;
            font-weight: 700 !important;
            box-shadow: inset 0 1px 0 rgba(255,255,255,0.18) !important;
        }
        div[data-testid="stButtonGroup"] button[data-testid="stBaseButton-pillsActive"],
        div[data-testid="stButtonGroup"] button[kind="pillsActive"] {
            color: #ffffff !important;
        }
        div[class*="st-key-main_nav_"] {
            margin: 0.2rem 0 0.8rem 0 !important;
        }
        div[class*="st-key-main_nav_"] button[data-testid^="stBaseButton-pills"] {
            min-height: 3.55rem !important;
            border-radius: 999px !important;
            background: rgba(255, 255, 255, 0.26) !important;
            color: #475569 !important;
            border: 1px solid rgba(255, 255, 255, 0.24) !important;
            box-shadow: inset 0 1px 0 rgba(255,255,255,0.24) !important;
        }
        div[class*="st-key-main_nav_"] button[data-testid="stBaseButton-pillsActive"],
        div[class*="st-key-main_nav_"] button[kind="pillsActive"] {
            background: linear-gradient(135deg, rgba(20, 184, 166, 0.92), rgba(14, 116, 144, 0.82)) !important;
            color: #ffffff !important;
            border-color: rgba(255, 255, 255, 0.16) !important;
            box-shadow: 0 14px 30px rgba(13, 148, 136, 0.22) !important;
        }
        div[class*="st-key-main_nav_"] button[data-testid="stBaseButton-pillsActive"] *,
        div[class*="st-key-main_nav_"] button[kind="pillsActive"] * {
            color: #ffffff !important;
            fill: #ffffff !important;
        }
        div[class*="st-key-main_nav_"] button[data-testid^="stBaseButton-pills"]:not([data-testid="stBaseButton-pillsActive"]) *,
        div[class*="st-key-main_nav_"] button[data-testid^="stBaseButton-pills"]:not([kind="pillsActive"]) * {
            color: #475569 !important;
        }

        /* ── Pills ── */
        div[class*="st-key-pick_purpose_pills_"] button[data-testid^="stBaseButton-pills"],
        div[class*="st-key-pick_user_pills_"] button[data-testid^="stBaseButton-pills"],
        div[class*="st-key-pick_returnable_pill_"] button[data-testid^="stBaseButton-pills"] {
            border-radius: 999px !important;
            border: 1px solid rgba(255, 255, 255, 0.18) !important;
            background: rgba(255, 255, 255, 0.24) !important;
            color: var(--glass-muted) !important;
            font-weight: 700 !important;
            backdrop-filter: blur(10px) !important;
            -webkit-backdrop-filter: blur(10px) !important;
            transition: background 0.18s ease, color 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease !important;
        }
        div[class*="st-key-pick_purpose_pills_"] button[kind="pillsActive"],
        div[class*="st-key-pick_purpose_pills_"] button[data-testid="stBaseButton-pillsActive"],
        div[class*="st-key-pick_user_pills_"] button[kind="pillsActive"],
        div[class*="st-key-pick_user_pills_"] button[data-testid="stBaseButton-pillsActive"],
        div[class*="st-key-pick_returnable_pill_"] button[kind="pillsActive"],
        div[class*="st-key-pick_returnable_pill_"] button[data-testid="stBaseButton-pillsActive"] {
            color: #ffffff !important;
            border-color: transparent !important;
            box-shadow: 0 10px 24px rgba(15, 23, 42, 0.16) !important;
        }
        div[class*="st-key-pick_purpose_pills_"] button:nth-of-type(1)[kind="pillsActive"],
        div[class*="st-key-pick_purpose_pills_"] button:nth-of-type(1)[data-testid="stBaseButton-pillsActive"] { background: #ef4444 !important; }
        div[class*="st-key-pick_purpose_pills_"] button:nth-of-type(2)[kind="pillsActive"],
        div[class*="st-key-pick_purpose_pills_"] button:nth-of-type(2)[data-testid="stBaseButton-pillsActive"] { background: #f97316 !important; }
        div[class*="st-key-pick_purpose_pills_"] button:nth-of-type(3)[kind="pillsActive"],
        div[class*="st-key-pick_purpose_pills_"] button:nth-of-type(3)[data-testid="stBaseButton-pillsActive"] { background: #06b6d4 !important; }
        div[class*="st-key-pick_purpose_pills_"] button:nth-of-type(4)[kind="pillsActive"],
        div[class*="st-key-pick_purpose_pills_"] button:nth-of-type(4)[data-testid="stBaseButton-pillsActive"] { background: #8b5cf6 !important; }
        div[class*="st-key-pick_user_pills_"] button:nth-of-type(1)[kind="pillsActive"],
        div[class*="st-key-pick_user_pills_"] button:nth-of-type(1)[data-testid="stBaseButton-pillsActive"] { background: #ef4444 !important; }
        div[class*="st-key-pick_user_pills_"] button:nth-of-type(2)[kind="pillsActive"],
        div[class*="st-key-pick_user_pills_"] button:nth-of-type(2)[data-testid="stBaseButton-pillsActive"] { background: #f97316 !important; }
        div[class*="st-key-pick_user_pills_"] button:nth-of-type(3)[kind="pillsActive"],
        div[class*="st-key-pick_user_pills_"] button:nth-of-type(3)[data-testid="stBaseButton-pillsActive"] { background: #f59e0b !important; }
        div[class*="st-key-pick_user_pills_"] button:nth-of-type(4)[kind="pillsActive"],
        div[class*="st-key-pick_user_pills_"] button:nth-of-type(4)[data-testid="stBaseButton-pillsActive"] { background: #84cc16 !important; }
        div[class*="st-key-pick_user_pills_"] button:nth-of-type(5)[kind="pillsActive"],
        div[class*="st-key-pick_user_pills_"] button:nth-of-type(5)[data-testid="stBaseButton-pillsActive"] { background: #10b981 !important; }
        div[class*="st-key-pick_user_pills_"] button:nth-of-type(6)[kind="pillsActive"],
        div[class*="st-key-pick_user_pills_"] button:nth-of-type(6)[data-testid="stBaseButton-pillsActive"] { background: #06b6d4 !important; }
        div[class*="st-key-pick_user_pills_"] button:nth-of-type(7)[kind="pillsActive"],
        div[class*="st-key-pick_user_pills_"] button:nth-of-type(7)[data-testid="stBaseButton-pillsActive"] { background: #6366f1 !important; }
        div[class*="st-key-pick_user_pills_"] button:nth-of-type(8)[kind="pillsActive"],
        div[class*="st-key-pick_user_pills_"] button:nth-of-type(8)[data-testid="stBaseButton-pillsActive"] { background: #64748b !important; }
        div[class*="st-key-pick_returnable_pill_"] button[data-testid^="stBaseButton-pills"] {
            width: 100% !important;
            justify-content: center !important;
            text-align: center !important;
            min-height: 3rem !important;
            white-space: normal !important;
            line-height: 1.35 !important;
            font-size: 0.98rem !important;
        }
        div[class*="st-key-pick_returnable_pill_"] button[kind="pillsActive"],
        div[class*="st-key-pick_returnable_pill_"] button[data-testid="stBaseButton-pillsActive"] {
            background: #0f766e !important;
        }

        /* ── Inputs ── */
        .stTextInput input, .stTextArea textarea, .stNumberInput input {
            border: 1px solid var(--glass-stroke) !important;
            border-radius: 16px !important;
            background: var(--glass-bg-soft) !important;
            backdrop-filter: blur(12px) !important;
            -webkit-backdrop-filter: blur(12px) !important;
            color: var(--glass-text) !important;
            box-shadow: inset 0 1px 0 rgba(255,255,255,0.22), var(--glass-shadow-soft) !important;
            transition: border-color 0.15s, box-shadow 0.15s !important;
        }
        .stTextInput input:focus, .stTextArea textarea:focus, .stNumberInput input:focus {
            border-color: rgba(20, 184, 166, 0.55) !important;
            box-shadow: 0 0 0 3px rgba(45, 212, 191, 0.14), var(--glass-shadow-soft) !important;
            outline: none !important;
        }
        /* selectbox / multiselect borders */
        div[data-baseweb="select"] > div {
            border: 1px solid var(--glass-stroke) !important;
            border-radius: 16px !important;
            background: var(--glass-bg-soft) !important;
            backdrop-filter: blur(12px) !important;
            -webkit-backdrop-filter: blur(12px) !important;
            box-shadow: inset 0 1px 0 rgba(255,255,255,0.22), var(--glass-shadow-soft) !important;
        }
        div[data-baseweb="select"] > div:focus-within {
            border-color: rgba(20, 184, 166, 0.55) !important;
            box-shadow: 0 0 0 3px rgba(45, 212, 191, 0.14), var(--glass-shadow-soft) !important;
        }
        .stTextInput label, .stTextArea label, .stNumberInput label,
        .stSelectbox label, .stCheckbox label, .stRadio label {
            font-size: 0.78rem !important;
            font-weight: 700 !important;
            text-transform: uppercase !important;
            letter-spacing: 0.06em !important;
        }

        /* ── Buttons ── */
        .stButton > button[kind="primary"] {
            background: linear-gradient(135deg, rgba(20, 184, 166, 0.88), rgba(14, 116, 144, 0.76)) !important;
            color: #ffffff !important;
            border: 1px solid rgba(255, 255, 255, 0.22) !important;
            border-radius: 16px !important;
            backdrop-filter: blur(12px) !important;
            -webkit-backdrop-filter: blur(12px) !important;
            font-weight: 700 !important;
            font-size: 0.93rem !important;
            min-height: 2.75rem !important;
            width: 100% !important;
            transition: background 0.15s, box-shadow 0.15s !important;
            box-shadow: 0 14px 28px rgba(13,148,136,0.18) !important;
        }
        .stButton > button[kind="primary"]:hover {
            background: linear-gradient(135deg, rgba(15, 118, 110, 0.92), rgba(8, 145, 178, 0.84)) !important;
            box-shadow: 0 18px 36px rgba(13,148,136,0.22) !important;
        }
        .stButton > button[kind="secondary"] {
            background: var(--glass-bg-strong) !important;
            color: var(--glass-text) !important;
            border: 1px solid var(--glass-stroke) !important;
            border-radius: 16px !important;
            backdrop-filter: var(--glass-blur) !important;
            -webkit-backdrop-filter: var(--glass-blur) !important;
            font-weight: 600 !important;
            font-size: 0.92rem !important;
            min-height: 3rem !important;
            width: 100% !important;
            justify-content: flex-start !important;
            text-align: left !important;
            white-space: normal !important;
            line-height: 1.35 !important;
            box-shadow: var(--glass-shadow-soft) !important;
        }
        .stButton > button[kind="secondary"]:hover {
            border-color: rgba(20, 184, 166, 0.40) !important;
            color: var(--glass-accent-strong) !important;
            background: rgba(255, 255, 255, 0.56) !important;
        }
        .stFormSubmitButton > button {
            background: linear-gradient(135deg, rgba(20, 184, 166, 0.88), rgba(14, 116, 144, 0.76)) !important;
            color: #ffffff !important;
            border: 1px solid rgba(255, 255, 255, 0.22) !important;
            border-radius: 16px !important;
            font-weight: 700 !important;
            min-height: 2.75rem !important;
        }
        .stFormSubmitButton > button:hover { background: linear-gradient(135deg, rgba(15, 118, 110, 0.92), rgba(8, 145, 178, 0.84)) !important; }
        .stDownloadButton > button {
            background: var(--glass-bg-strong) !important;
            color: var(--glass-accent-strong) !important;
            border: 1px solid rgba(20, 184, 166, 0.28) !important;
            border-radius: 16px !important;
            backdrop-filter: var(--glass-blur) !important;
            -webkit-backdrop-filter: var(--glass-blur) !important;
            font-weight: 600 !important;
            box-shadow: var(--glass-shadow-soft) !important;
        }

        /* ── Item card ── */
        .item-card {
            background: var(--glass-bg) !important;
            border: 1px solid var(--glass-stroke) !important;
            backdrop-filter: var(--glass-blur) !important;
            -webkit-backdrop-filter: var(--glass-blur) !important;
            border-radius: 22px;
            padding: 1rem 1.1rem;
            margin-bottom: 0.8rem;
            box-shadow: var(--glass-shadow);
        }
        .item-name { color: var(--glass-text); font-size: 1rem; font-weight: 700; margin-bottom: 0.15rem; }
        .item-desc { color: var(--glass-muted); font-size: 0.85rem; margin-bottom: 0.65rem; line-height: 1.5; }
        .pill-row  { display: flex; flex-wrap: wrap; gap: 0.35rem; }
        .pill      { display: inline-block; padding: 0.22rem 0.62rem; border-radius: 999px; font-size: 0.73rem; font-weight: 700; border: 1px solid rgba(255,255,255,0.28); backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px); }
        .p-neutral { background: rgba(255, 255, 255, 0.30); color: #475569; }
        .p-ok      { background: rgba(34, 197, 94, 0.18); color: #166534; }
        .p-low     { background: rgba(249, 115, 22, 0.18); color: #9a3412; }
        .p-zero    { background: rgba(239, 68, 68, 0.18); color: #991b1b; }

        /* ── Metric card ── */
        .metrics-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 0.85rem;
            margin-bottom: 1.2rem;
        }
        .metric-card {
            background: var(--glass-bg) !important;
            border: 1px solid var(--glass-stroke) !important;
            border-radius: 22px;
            backdrop-filter: var(--glass-blur) !important;
            -webkit-backdrop-filter: var(--glass-blur) !important;
            padding: 1.3rem 1.4rem;
            box-shadow: var(--glass-shadow);
            display: flex;
            flex-direction: column;
            gap: 0.35rem;
        }
        .metric-card.mc-accent { border-left: 4px solid #0d9488; }
        .metric-card.mc-danger { border-left: 4px solid #dc2626; }
        .metric-card.mc-warn   { border-left: 4px solid #ea580c; }
        .metric-card.mc-issued { border-left: 4px solid #6366f1; }
        .metric-card.mc-deposit{ border-left: 4px solid #0ea5e9; }
        .metric-icon  { font-size: 1.4rem; line-height: 1; }
        .metric-label { color: #64748b; font-size: 0.7rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.09em; }
        .metric-value { color: var(--glass-text); font-size: 2rem; font-weight: 800; line-height: 1; }
        .m-accent { color: #0d9488 !important; }
        .m-warn   { color: #ea580c !important; }
        .m-danger { color: #dc2626 !important; }
        .m-issued { color: #6366f1 !important; }
        .m-deposit{ color: #0ea5e9 !important; }

        /* ── Low-stock banner ── */
        .low-stock-banner {
            background: rgba(255, 237, 213, 0.50);
            border: 1px solid rgba(251, 146, 60, 0.45);
            border-radius: 18px;
            backdrop-filter: var(--glass-blur);
            -webkit-backdrop-filter: var(--glass-blur);
            padding: 0.55rem 1rem;
            margin-bottom: 0.8rem;
            display: flex;
            align-items: center;
            gap: 0.5rem;
            font-size: 0.9rem;
            font-weight: 700;
            color: #c2410c;
            box-shadow: var(--glass-shadow-soft);
        }
        .section-label {
            color: #64748b;
            font-size: 0.72rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            margin: 1.2rem 0 0.5rem 0;
        }
        .brand-header {
            display: flex;
            align-items: center;
            gap: 0.9rem;
            margin-bottom: 0.8rem;
        }
        .brand-title {
            color: var(--glass-text);
            font-size: 1.7rem;
            font-weight: 800;
            line-height: 1.1;
            text-shadow: 0 1px 0 rgba(255,255,255,0.35);
        }
        .brand-subtitle {
            color: var(--glass-muted);
            font-size: 0.9rem;
            font-weight: 600;
            line-height: 1.3;
        }
        .role-card {
            border: 1px solid var(--glass-stroke);
            border-radius: 20px;
            padding: 0.9rem 0.9rem 0.8rem 0.9rem;
            background: var(--glass-bg);
            backdrop-filter: var(--glass-blur);
            -webkit-backdrop-filter: var(--glass-blur);
            min-height: 132px;
            margin-bottom: 0.45rem;
            box-shadow: var(--glass-shadow-soft);
        }
        .role-card-active {
            border-color: rgba(20, 184, 166, 0.42);
            box-shadow: 0 0 0 1px rgba(20,184,166,0.12), 0 16px 30px rgba(13,148,136,0.14);
            background: rgba(240, 253, 250, 0.34);
        }
        .role-card-title {
            color: var(--glass-text);
            font-size: 1rem;
            font-weight: 800;
            margin-bottom: 0.2rem;
        }
        .role-card-copy {
            color: var(--glass-muted);
            font-size: 0.82rem;
            line-height: 1.35;
        }
        .role-card-badge {
            display: inline-block;
            margin-top: 0.5rem;
            padding: 0.2rem 0.55rem;
            border-radius: 999px;
            background: rgba(255, 255, 255, 0.30);
            color: #334155;
            border: 1px solid rgba(255,255,255,0.28);
            font-size: 0.72rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.06em;
        }

        /* ── Divider ── */
        hr { margin: 0.9rem 0 !important; }

        /* ── Form container ── */
        div[data-testid="stForm"] {
            background: var(--glass-bg) !important;
            border: 1px solid var(--glass-stroke) !important;
            border-radius: 22px !important;
            padding: 1rem !important;
            backdrop-filter: var(--glass-blur) !important;
            -webkit-backdrop-filter: var(--glass-blur) !important;
            box-shadow: var(--glass-shadow-soft) !important;
        }

        /* ── Checkbox ── */
        .stCheckbox [data-baseweb="checkbox"] > div {
            border-color: rgba(148, 163, 184, 0.55) !important;
            background: rgba(255,255,255,0.22) !important;
        }

        /* ── Dataframes / tables ── */
        div[data-testid="stDataFrame"],
        div[data-testid="stTable"] {
            background: var(--glass-bg) !important;
            border: 1px solid var(--glass-stroke) !important;
            border-radius: 22px !important;
            backdrop-filter: var(--glass-blur) !important;
            -webkit-backdrop-filter: var(--glass-blur) !important;
            box-shadow: var(--glass-shadow) !important;
            overflow: hidden !important;
        }
        div[data-testid="stDataFrame"] [role="grid"],
        div[data-testid="stDataFrame"] [data-testid="stDataFrameResizable"],
        div[data-testid="stDataFrame"] [data-testid="stDataFrameGlideDataEditor"] {
            background: transparent !important;
        }
        div[data-testid="stDataFrame"] [role="columnheader"],
        div[data-testid="stDataFrame"] [role="gridcell"] {
            background: rgba(255,255,255,0.10) !important;
        }

        /* ── Dialogs / containers ── */
        div[role="dialog"] > div {
            background: rgba(255,255,255,0.34) !important;
            border: 1px solid var(--glass-stroke) !important;
            border-radius: 24px !important;
            backdrop-filter: blur(22px) !important;
            -webkit-backdrop-filter: blur(22px) !important;
            box-shadow: 0 24px 60px rgba(15,23,42,0.18) !important;
        }

        /* ── Status messages ── */
        div.stSuccess > div { background: rgba(220, 252, 231, 0.52) !important; color: #166534 !important; border: 1px solid rgba(34, 197, 94, 0.28) !important; border-radius: 18px !important; backdrop-filter: var(--glass-blur) !important; -webkit-backdrop-filter: var(--glass-blur) !important; }
        div.stError > div   { background: rgba(254, 226, 226, 0.52) !important; color: #b91c1c !important; border: 1px solid rgba(239, 68, 68, 0.24) !important; border-radius: 18px !important; backdrop-filter: var(--glass-blur) !important; -webkit-backdrop-filter: var(--glass-blur) !important; }
        div.stWarning > div { background: rgba(254, 243, 199, 0.52) !important; color: #b45309 !important; border: 1px solid rgba(245, 158, 11, 0.24) !important; border-radius: 18px !important; backdrop-filter: var(--glass-blur) !important; -webkit-backdrop-filter: var(--glass-blur) !important; }
        div.stInfo > div    { background: rgba(219, 234, 254, 0.52) !important; color: #1d4ed8 !important; border: 1px solid rgba(59, 130, 246, 0.24) !important; border-radius: 18px !important; backdrop-filter: var(--glass-blur) !important; -webkit-backdrop-filter: var(--glass-blur) !important; }

        /* ── Mobile tweaks ── */
        @media (max-width: 640px) {
            .block-container { padding-left: 0.75rem !important; padding-right: 0.75rem !important; }
            .item-card { padding: 0.85rem 0.9rem; }
            h1 { font-size: 1.25rem !important; }
            .metric-value { font-size: 1.35rem !important; }
            .brand-title { font-size: 1.35rem !important; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def get_version_info():
    """Return (short_hash, timestamp_str) for current repo or fallback to CSV mtime."""
    short = ""
    ts = None
    try:
        short = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=os.getcwd(), stderr=subprocess.DEVNULL).decode().strip()
        ts = subprocess.check_output(["git", "show", "-s", "--format=%ci", "HEAD"], cwd=os.getcwd(), stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        try:
            if os.path.exists(ITEMS_SNAPSHOT_CSV_PATH):
                m = os.path.getmtime(ITEMS_SNAPSHOT_CSV_PATH)
                ts = dt.datetime.fromtimestamp(m).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            ts = None
    if not ts:
        ts = dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    return short, ts


def render_version_stamp():
    ver, ts = get_version_info()
    label = f"v {ver} • {ts}" if ver else ts
    html = (
        f"<div style=\"position:fixed;right:12px;bottom:12px;opacity:0.3;z-index:9999;"
        f"pointer-events:none;font-size:12px;color:#0f172a;background:transparent;" 
        f"padding:4px 6px;border-radius:6px;\">{label}</div>"
    )
    st.markdown(html, unsafe_allow_html=True)


def stock_pill(qty, min_level):
    if qty == 0:
        return '<span class="pill p-zero">Out of stock</span>'
    if qty <= min_level:
        return f'<span class="pill p-low">Low &mdash; {qty} left</span>'
    return f'<span class="pill p-ok">&#10003; {qty} in stock</span>'


def render_metric(label, value, extra_class=""):
    st.markdown(
        f"""
        <div class="metric-card">
            <div class="metric-label">{label}</div>
            <div class="metric-value {extra_class}">{value}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


@st.dialog("Manager Access", width="small", dismissible=False)
def show_manager_password_dialog():
    password = st.text_input(
        "Enter manager password",
        type="password",
        key="manager_password_input",
    )
    unlock_col, cancel_col = st.columns(2)

    if unlock_col.button("Unlock", key="manager_unlock", type="primary"):
        if password == "321":
            st.session_state["manager_authenticated"] = True
            st.session_state["role"] = "manager"
            st.session_state.pop("manager_login_requested", None)
            st.session_state.pop("manager_password_input", None)
            safe_rerun()
        else:
            st.error("Incorrect manager password.")

    if cancel_col.button("Cancel", key="manager_cancel", type="secondary"):
        st.session_state["manager_authenticated"] = False
        st.session_state["role"] = "user"
        st.session_state.pop("manager_login_requested", None)
        st.session_state.pop("manager_password_input", None)
        safe_rerun()


def sidebar_identity(conn):
    with st.sidebar:
        if "role" not in st.session_state:
            st.session_state["role"] = "user"
        render_brand_logo(width=76)
        st.markdown("---")

        current_role = st.session_state.get("role", "user")
        manager_ready = bool(st.session_state.get("manager_authenticated"))
        st.caption("Choose access")
        user_col, manager_col = st.columns(2)
        with user_col:
            user_active = current_role == "user"
            st.markdown(
                f"""
                <div class="role-card {'role-card-active' if user_active else ''}">
                    <div class="role-card-title">User</div>
                    <div class="role-card-copy">Browse items, issue material, return items, and view history.</div>
                    <div class="role-card-badge">{'Active' if user_active else 'Standard access'}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            if st.button("Use User", key="role_user_card", use_container_width=True, type="secondary"):
                st.session_state["role"] = "user"
                st.session_state["user"] = "operator"
                st.session_state["manager_authenticated"] = False
                st.session_state.pop("manager_login_requested", None)
                safe_rerun()
        with manager_col:
            manager_active = current_role == "manager" and manager_ready
            st.markdown(
                f"""
                <div class="role-card {'role-card-active' if manager_active else ''}">
                    <div class="role-card-title">Manager</div>
                    <div class="role-card-copy">Unlock inward stock, dashboard, alerts, and full master controls.</div>
                    <div class="role-card-badge">{'Unlocked' if manager_active else 'Password required'}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            if st.button(
                "Use Manager" if not manager_active else "Manager Active",
                key="role_manager_card",
                use_container_width=True,
                type="primary" if manager_active else "secondary",
            ):
                if manager_ready:
                    st.session_state["role"] = "manager"
                    st.session_state["user"] = "manager"
                else:
                    st.session_state["role"] = "user"
                    st.session_state["user"] = "operator"
                    st.session_state["manager_login_requested"] = True
                safe_rerun()

        if not manager_ready and st.session_state.get("role") != "manager":
            st.session_state["role"] = "user"
            st.session_state["user"] = "operator"
        elif manager_ready and st.session_state.get("role") == "manager":
            st.session_state["user"] = "manager"

        st.markdown("---")
        st.caption(f"Access: **{st.session_state['role'].capitalize()}**")
        st.markdown("---")
        st.caption("Danger zone — reset application database")
        reset_code = st.text_input("Enter reset code to wipe app (permanent)", type="password", key="reset_code_input")
        if st.button("Reset app (permanent)", key="reset_app", type="primary"):
            if reset_code == "611881":
                try:
                    # Close current DB handle, mark next startup as empty, then wipe DB file.
                    conn.close()
                    with open(RESET_EMPTY_MARKER, "w", encoding="utf-8"):
                        pass
                    if os.path.exists(DB_PATH):
                        os.remove(DB_PATH)
                    if os.path.exists(ITEMS_SNAPSHOT_CSV_PATH):
                        os.remove(ITEMS_SNAPSHOT_CSV_PATH)

                    # Clear session state to avoid stale selections and credentials.
                    for k in list(st.session_state.keys()):
                        st.session_state.pop(k, None)
                    safe_rerun()
                except Exception as e:
                    st.error(f"Reset failed: {e}")
            else:
                st.error("Incorrect reset code.")

    if st.session_state.get("manager_login_requested"):
        show_manager_password_dialog()


def filter_parts(parts, query):
    q = query.strip().lower()
    if not q:
        return parts
    return [
        p for p in parts
        if q in p["name"].lower()
        or q in p["part_id"].lower()
        or q in (p["description"] or "").lower()
        or q in (p["location"] or "").lower()
    ]


def part_list_label(part):
    # support multiple row types: dict, sqlite3.Row (mapping), or sequence/tuple
    def _get(key, idx_fallback):
        # dict-like with .get
        try:
            if hasattr(part, "get"):
                return part.get(key)
        except Exception:
            pass
        # mapping access like sqlite3.Row
        try:
            return part[key]
        except Exception:
            pass
        # sequence fallback by index
        try:
            return part[idx_fallback]
        except Exception:
            return None

    name = _get("name", 2) or str(_get("part_id", 1) or "")
    desc = (_get("description", 3) or "").strip()
    if len(desc) > 54:
        desc = desc[:51] + "..."
    if desc:
        return f"{name} | {desc}"
    return name


def safe_part_field(part, key, default=""):
    """Safely get a field from `part` which may be a dict, sqlite3.Row, or sequence."""
    # dict-like with .get
    try:
        if hasattr(part, "get"):
            return part.get(key, default)
    except Exception:
        pass
    # mapping access like sqlite3.Row
    try:
        return part[key]
    except Exception:
        pass
    # sequence fallback not used often; return default
    return default


def _show_arrow_animation_once(key_prefix="pick"):
    # key_prefix: 'pick' or 'deposit'
    flag_key = f"{key_prefix}_animation_shown"
    if st.session_state.get(flag_key):
        return
    html = """
    <div style='text-align:center; pointer-events:none; margin: .8rem 0'>
      <div class='eq-arrow'></div>
    </div>
    <style>
    .eq-arrow{width:0;height:0;border-left:36px solid transparent;border-right:36px solid transparent;border-bottom:60px solid #10b981;margin:0 auto;animation:eq-up 1200ms ease-out;}
    @keyframes eq-up{0%{transform:translateY(40px);opacity:0}50%{opacity:1}100%{transform:translateY(-120px);opacity:0}}
    </style>
    """
    st.markdown(html, unsafe_allow_html=True)
    st.session_state[flag_key] = True


def render_part_picker(parts, search_key, dialog_key, button_prefix, placeholder, category_key=None):
    search_term = live_search_input("Search item", placeholder, search_key)
    matches = filter_parts(parts, search_term)

    # optional category filter
    if category_key:
        categories = sorted({safe_part_field(p, "category", "Others") for p in parts})
        if categories:
            sel = st.selectbox("Category", ["All"] + categories, key=category_key)
            if sel and sel != "All":
                matches = [p for p in matches if safe_part_field(p, "category", "Others") == sel]

    st.caption(f"Showing {len(matches)} of {len(parts)} items")
    with st.container(height=460, border=True):
        if not matches:
            st.info("No items matched your search.")
        else:
            for p in matches:
                pid = safe_part_field(p, "part_id", "")
                if st.button(
                    part_list_label(p),
                    key=f"{button_prefix}_{pid}",
                    use_container_width=True,
                    type="secondary",
                ):
                    st.session_state[dialog_key] = pid
                    safe_rerun()


@st.dialog("Item Details", width="large", dismissible=False)
def show_item_details_dialog(conn):
    part_id = st.session_state.get("items_dialog_part_id")
    part = get_part(conn, part_id) if part_id else None
    if not part:
        st.session_state.pop("items_dialog_part_id", None)
        safe_rerun()
        return

    st.markdown(
        f"""
        <div class="item-card">
            <div class="item-name">{part['name']}</div>
            <div class="item-desc">{part['description']}</div>
            <div class="pill-row">
                <span class="pill p-neutral">{part['part_id']}</span>
                <span class="pill p-neutral">Location: {part['location']}</span>
                <span class="pill p-neutral">Category: {safe_part_field(part, 'category', 'Others')}</span>
                <span class="pill p-neutral">Unit: {part['unit']}</span>
                <span class="pill p-neutral">Min {part['min_level']} &nbsp;&middot;&nbsp; Reorder {part['reorder_qty']}</span>
                {stock_pill(part['quantity'], part['min_level'])}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if st.button("Close", key="close_items_dialog", type="secondary"):
        st.session_state.pop("items_dialog_part_id", None)
        safe_rerun()


@st.dialog("Issue Material", width="large", dismissible=False)
def show_pick_dialog(conn):
    part_id = st.session_state.get("pick_dialog_part_id")
    part = get_part(conn, part_id) if part_id else None
    if not part:
        st.session_state.pop("pick_dialog_part_id", None)
        safe_rerun()
        return

    st.markdown(
        f"""
        <div class="item-card">
            <div class="item-name">{part['name']}</div>
            <div class="item-desc">{part['description']}</div>
            <div class="pill-row">
                <span class="pill p-neutral">Location: {part['location']}</span>
                <span class="pill p-neutral">Category: {safe_part_field(part, 'category', 'Others')}</span>
                {stock_pill(part['quantity'], part['min_level'])}
                <span class="pill p-neutral">Min {part['min_level']} &nbsp;&middot;&nbsp; Reorder {part['reorder_qty']}</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if part["quantity"] <= 0:
        st.error("This item is out of stock and cannot be issued.")
        if st.button("Close", key="close_pick_dialog_oos", type="secondary"):
            st.session_state.pop("pick_dialog_part_id", None)
            safe_rerun()
        return

    qty = st.number_input(
        "Quantity to pick",
        min_value=1,
        max_value=min(int(part["quantity"]), 8),
        value=1,
        step=1,
        key=f"pick_qty_{part_id}",
    )
    st.markdown(
        f'<p style="color:#64748b;font-size:0.78rem;font-weight:700;text-transform:uppercase;'
        f'letter-spacing:.06em;margin:0 0 .3rem 0">Machine serial number '
        f'(required — one per material issue)</p>',
        unsafe_allow_html=True,
    )
    serial_input = st.text_input(
        "Machine serial number *",
        placeholder="e.g. VMC-120",
        key=f"pick_serial_{part_id}",
    )

    st.markdown('<div style="margin-top:.5rem;font-weight:700">Purpose / Usage *</div>', unsafe_allow_html=True)
    purpose_options = ["Assembly", "Checking", "Testing", "Other"]
    if hasattr(st, "pills"):
        selected_purposes = st.pills(
            "Purpose / Usage *",
            purpose_options,
            selection_mode="multi",
            key=f"pick_purpose_pills_{part_id}",
            label_visibility="collapsed",
        )
    else:
        selected_purposes = st.multiselect(
            "Purpose / Usage *",
            purpose_options,
            key=f"pick_purpose_pills_{part_id}",
            label_visibility="collapsed",
        )
    assembly = "Assembly" in selected_purposes
    checking = "Checking" in selected_purposes
    testing = "Testing" in selected_purposes
    other_purpose_checked = "Other" in selected_purposes
    purpose_other = ""
    if other_purpose_checked:
        purpose_other = st.text_input(
            "Specify other purpose",
            placeholder="e.g. UTM-200 calibration",
            key=f"pick_purpose_other_{part_id}",
        )

    returnable_option = "↩ Returnable (material will be brought back)"
    if hasattr(st, "pills"):
        returnable_selection = st.pills(
            "Returnable",
            [returnable_option],
            selection_mode="single",
            key=f"pick_returnable_pill_{part_id}",
            label_visibility="collapsed",
            width="stretch",
        )
        returnable = returnable_selection == returnable_option
    else:
        returnable = st.checkbox(
            returnable_option,
            key=f"pick_returnable_{part_id}",
        )

    # User Name selection: chip-style single select for compact mobile layout
    st.markdown('<div style="margin-top:.6rem;font-weight:700">User Name</div>', unsafe_allow_html=True)
    user_options = ["Ravi", "Shani", "Suraj", "Mangesh", "Ram", "Sonu", "Sandip", "Other"]
    if hasattr(st, "pills"):
        selected_user = st.pills(
            "User Name",
            user_options,
            selection_mode="single",
            key=f"pick_user_pills_{part_id}",
            label_visibility="collapsed",
        )
    else:
        selected_user = st.radio(
            "User Name",
            user_options,
            horizontal=True,
            key=f"pick_user_pills_{part_id}",
            label_visibility="collapsed",
        )

    if selected_user == "Other":
        user_other = st.text_input("Specify other user", placeholder="Name", key=f"pick_user_other_{part_id}")
        if user_other.strip():
            selected_user = user_other.strip()

    action_col, close_col = st.columns(2)
    if action_col.button("✅  Confirm Material Issue", key=f"confirm_pick_{part_id}", type="primary"):
        serial = serial_input.strip()
        purpose_parts = []
        if assembly:
            purpose_parts.append("Assembly")
        if checking:
            purpose_parts.append("Checking")
        if testing:
            purpose_parts.append("Testing")
        if other_purpose_checked:
            if purpose_other.strip():
                purpose_parts.append(purpose_other.strip())
            else:
                st.error("Please specify the other purpose.")
                return
        purpose_str = ", ".join(purpose_parts)

        if not serial:
            st.error("Please enter a machine serial number.")
        elif not purpose_parts:
            st.error("Please select at least one Purpose / Usage.")
        elif (selected_user or "").strip() == "":
            st.error("Please specify user name.")
        else:
            try:
                new_balance = pick_material(
                    conn,
                    part["part_id"],
                    [serial],
                    int(qty),
                    st.session_state["user"],
                    st.session_state["role"],
                    purpose_str,
                    (selected_user or "").strip(),
                    returnable=returnable,
                )
                st.session_state["pick_done"] = {
                    "part": part["name"],
                    "qty": int(qty),
                    "balance": new_balance,
                    "unit": part["unit"],
                    "returnable": returnable,
                }
                # ensure animation runs once when pick is completed
                st.session_state["pick_animation_shown"] = False
                st.session_state.pop("pick_dialog_part_id", None)
                safe_rerun()
            except Exception as exc:
                st.error(str(exc))

    if close_col.button("Cancel", key=f"close_pick_dialog_{part_id}", type="secondary"):
        st.session_state.pop("pick_dialog_part_id", None)
        safe_rerun()


@st.dialog("Inward Stock", width="large", dismissible=False)
def show_deposit_dialog(conn):
    part_id = st.session_state.get("deposit_dialog_part_id")
    part = get_part(conn, part_id) if part_id else None
    if not part:
        st.session_state.pop("deposit_dialog_part_id", None)
        safe_rerun()
        return

    st.markdown(
        f"""
        <div class="item-card">
            <div class="item-name">{part['name']}</div>
            <div class="item-desc">{part['description']}</div>
            <div class="pill-row">
                <span class="pill p-neutral">Location: {part['location']}</span>
                <span class="pill p-neutral">Category: {safe_part_field(part, 'category', 'Others')}</span>
                <span class="pill p-neutral">Unit: {part['unit']}</span>
                {stock_pill(part['quantity'], part['min_level'])}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    qty = st.number_input(
        "Quantity to Inward",
        min_value=1,
        value=1,
        step=1,
        key=f"deposit_qty_{part_id}",
    )
    note = st.text_input(
        "Note / GRN reference *",
        placeholder="e.g. GRN-001 / Vendor invoice (required)",
        key=f"deposit_note_{part_id}",
    )
    action_col, close_col = st.columns(2)
    if action_col.button("📥  Confirm Inward", key=f"confirm_deposit_{part_id}", type="primary"):
        if not note.strip():
            st.error("Note / GRN reference is required.")
        else:
            try:
                new_balance = deposit_stock(
                    conn,
                    part["part_id"],
                    int(qty),
                    st.session_state["user"],
                    st.session_state["role"],
                    note.strip(),
                )
                # record deposit done state so we can show animation on the main page
                st.session_state["deposit_done"] = {
                    "part": part["name"],
                    "qty": int(qty),
                    "balance": new_balance,
                    "unit": part["unit"],
                }
                st.session_state["deposit_animation_shown"] = False
                st.session_state.pop("deposit_dialog_part_id", None)
                safe_rerun()
            except Exception as exc:
                st.error(str(exc))

    if close_col.button("Cancel", key=f"close_deposit_dialog_{part_id}", type="secondary"):
        st.session_state.pop("deposit_dialog_part_id", None)
        safe_rerun()


@st.dialog("Return Material", width="large", dismissible=False)
def show_return_dialog(conn):
    if st.session_state.get("role", "user") != "manager":
        st.error("Only managers can return material.")
        if st.button("Close", key="close_return_manager_only", type="secondary"):
            st.session_state.pop("return_dialog_issue_id", None)
            safe_rerun()
        return

    issue_tx_id = st.session_state.get("return_dialog_issue_id")
    issue = None
    if issue_tx_id:
        issue = next((row for row in list_open_returnable_issues(conn, limit=500) if row["id"] == issue_tx_id), None)
        if issue is None:
            issue = next((row for row in list_transactions(conn, tx_type="issue", limit=500) if row["id"] == issue_tx_id), None)

    if not issue:
        st.session_state.pop("return_dialog_issue_id", None)
        safe_rerun()
        return

    st.markdown(
        f"""
        <div class="item-card">
            <div class="item-name">{issue['part_name']}</div>
            <div class="item-desc">Issued by {issue['performed_by']} on {issue['created_at']}</div>
            <div class="pill-row">
                <span class="pill p-neutral">{issue['part_id']}</span>
                <span class="pill p-neutral">Qty: {issue['qty']} {issue['unit']}</span>
                <span class="pill p-neutral">Machine: {issue['machine_sn'] or 'N/A'}</span>
                <span class="pill p-neutral">Purpose: {issue['purpose'] or 'N/A'}</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    note = st.text_input(
        "Return note / reference",
        placeholder="e.g. Returned in good condition / DO-014",
        key=f"return_note_{issue_tx_id}",
    )
    action_col, close_col = st.columns(2)
    if action_col.button("↩ Mark Returned", key=f"mark_returned_{issue_tx_id}", type="primary"):
        try:
            new_balance = return_issue_material(
                conn,
                int(issue_tx_id),
                st.session_state["user"],
                st.session_state["role"],
                note.strip(),
            )
            st.session_state.pop("return_dialog_issue_id", None)
            st.success(f"Returned successfully. New balance: {new_balance} {issue['unit']}")
            safe_rerun()
        except Exception as exc:
            st.error(str(exc))
    if close_col.button("Cancel", key=f"cancel_return_{issue_tx_id}", type="secondary"):
        st.session_state.pop("return_dialog_issue_id", None)
        safe_rerun()


@st.dialog("Review CSV Import", width="large", dismissible=False)
def show_import_review_dialog(conn):
    preview = st.session_state.get("im_import_preview")
    if not preview:
        safe_rerun()
        return

    st.markdown(f"**File:** {preview.get('file_name', 'uploaded.csv')}")
    st.caption(
        f"Review all uploaded rows before import. Nothing will be saved until you click Confirm Import."
    )

    summary_text = format_import_summary(preview, prefix="Review summary")
    if preview["can_import"] and preview.get("near_duplicate_rows"):
        st.warning(summary_text + " Possible duplicates are highlighted in yellow for manual review.")
    elif preview["can_import"]:
        st.info(summary_text)
    else:
        st.error(
            summary_text + " Fix the flagged rows in your CSV and upload again before importing."
        )

    preview_df = import_preview_dataframe(preview)
    if not preview_df.empty:
        st.dataframe(style_import_preview_dataframe(preview_df), use_container_width=True, hide_index=True, height=380)

    if preview["messages"]:
        st.caption("Validation notes")
        for message in preview["messages"][:8]:
            st.write(f"- {message}")

    confirm_col, cancel_col = st.columns(2)
    if confirm_col.button(
        "Confirm Import",
        key="im_confirm_import",
        type="primary",
        disabled=not preview["can_import"],
    ):
        result = import_parts_from_csv(conn, preview["records"])
        reset_master_table_draft(conn)
        st.session_state["im_import_result"] = result
        clear_import_review_state(reset_uploader=True)
        safe_rerun()

    if cancel_col.button("Cancel and Reupload", key="im_cancel_import_review", type="secondary"):
        clear_import_review_state(reset_uploader=True)
        safe_rerun()


def returnables_page(conn):
    role = st.session_state.get("role", "user")
    performed_by = None if role == "manager" else st.session_state["user"]

    search = live_search_input(
        "Search returnables",
        "Item, serial no, purpose, user…",
        "returnables_search",
    )

    pending_rows = list_open_returnable_issues(conn, search=search.strip(), performed_by=performed_by)
    done_rows = list_returned_returnable_issues(conn, search=search.strip(), performed_by=performed_by)

    pending_tab, done_tab = st.tabs(["Pending", "Done"])

    with pending_tab:
        st.markdown("<div class='section-label'>Pending Returnable Items</div>", unsafe_allow_html=True)
        if not pending_rows:
            st.info("No pending returnable items.")
        else:
            if role == "manager":
                with st.container(height=260, border=True):
                    for issue in pending_rows:
                        meta = issue["machine_sn"] or issue["purpose"] or "No machine / purpose noted"
                        label = f"{issue['part_name']} | {issue['part_id']} | {meta}"
                        if st.button(
                            label,
                            key=f"returnables_pending_{issue['id']}",
                            use_container_width=True,
                            type="secondary",
                        ):
                            st.session_state["return_dialog_issue_id"] = int(issue["id"])
                            safe_rerun()
                if st.session_state.get("return_dialog_issue_id"):
                    show_return_dialog(conn)

            pending_df = pd.DataFrame(rows_to_dicts(pending_rows))
            pending_df["return_status"] = "Pending Return"
            pending_cols = [
                c for c in [
                    "created_at", "return_status", "part_name", "part_id", "qty", "unit",
                    "performed_by", "machine_sn", "purpose", "note"
                ] if c in pending_df.columns
            ]
            st.dataframe(pending_df[pending_cols], use_container_width=True, hide_index=True)

    with done_tab:
        st.markdown("<div class='section-label'>Returned Materials</div>", unsafe_allow_html=True)
        if not done_rows:
            st.info("No returned returnable items.")
        else:
            done_df = pd.DataFrame(rows_to_dicts(done_rows))
            done_df["return_status"] = "Returned"
            done_cols = [
                c for c in [
                    "returned_at", "return_status", "part_name", "part_id", "qty", "unit",
                    "performed_by", "machine_sn", "purpose", "note", "returned_tx_id"
                ] if c in done_df.columns
            ]
            st.dataframe(done_df[done_cols], use_container_width=True, hide_index=True)


def items_page(conn):
    parts = get_parts(conn)

    if not parts:
        st.info("No items available.")
        return

    render_part_picker(
        parts,
        "items_search",
        "items_dialog_part_id",
        "items_browser",
        "Type item name, ID, description or location…",
    )
    if st.session_state.get("items_dialog_part_id"):
        show_item_details_dialog(conn)

def pick_material_page(conn):
    # ── Success state: shown after a confirmed issue to prevent double-press ──
    done = st.session_state.get("pick_done")
    if done:
        # show the arrow animation only once per completed pick
        if not st.session_state.get("pick_animation_shown"):
            _show_arrow_animation_once("pick")
        st.markdown(
            f"""
            <div style="background:#dcfce7;border:2px solid #16a34a;border-radius:12px;
                        padding:2rem;text-align:center;margin:1rem 0">
                <div style="font-size:2.8rem">✅</div>
                <div style="font-size:1.5rem;font-weight:800;color:#15803d">
                    Material Issued Successfully!
                </div>
                <div style="font-size:1rem;color:#166534;margin-top:.6rem">
                    <strong>{done['qty']}</strong> × {done['part']} issued
                    &nbsp;|&nbsp; New balance:
                    <strong>{done['balance']} {done['unit']}</strong>
                </div>
                {"<div style='margin-top:.6rem;font-size:0.9rem;color:#b45309;font-weight:700'>↩ Returnable — item must be returned to store</div>" if done.get('returnable') else ""}
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("← Issue another item", key="pick_another", type="secondary"):
            st.session_state.pop("pick_done", None)
            st.session_state.pop("pick_selected_id", None)
            st.session_state.pop("pick_animation_shown", None)
            safe_rerun()
        return

    available = get_parts(conn)
    if not available:
        st.info("No active items available for issue.")
        return

    render_part_picker(
        available,
        "pick_search",
        "pick_dialog_part_id",
        "pick_browser",
        "Type item name, ID, description or location…",
    )
    if st.session_state.get("pick_dialog_part_id"):
        show_pick_dialog(conn)


def deposit_stock_page(conn):
    # show completed deposit/inward state if present
    deposit_done = st.session_state.get("deposit_done")
    if deposit_done:
        # show the arrow animation only once
        if not st.session_state.get("deposit_animation_shown"):
            _show_arrow_animation_once("deposit")
        st.markdown(
            f"""
            <div style="background:#dcfce7;border:2px solid #16a34a;border-radius:12px;padding:2rem;text-align:center;margin:1rem 0">
                <div style="font-size:2.8rem">📥</div>
                <div style="font-size:1.5rem;font-weight:800;color:#15803d">Inwarded Successfully!</div>
                <div style="font-size:1rem;color:#166534;margin-top:.6rem">
                    <strong>{deposit_done['qty']}</strong> × {deposit_done['part']} inwarded
                    &nbsp;|&nbsp; New balance:
                    <strong>{deposit_done['balance']} {deposit_done['unit']}</strong>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("← Inward another item", key="inward_another", type="secondary"):
            st.session_state.pop("deposit_done", None)
            st.session_state.pop("deposit_animation_shown", None)
            safe_rerun()
        return

    parts = get_parts(conn, active_only=False)
    if not parts:
        st.info("Add items via Item Master before depositing stock.")
        return

    render_part_picker(
        parts,
        "deposit_search",
        "deposit_dialog_part_id",
        "deposit_browser",
        "Type item name, ID, description or location…",
    )
    if st.session_state.get("deposit_dialog_part_id"):
        show_deposit_dialog(conn)


def item_master_page(conn):
    sync_master_table_draft_from_db(conn)

    save_notice = st.session_state.pop("im_master_save_notice", None)
    if save_notice:
        st.success(save_notice)

    top_left, top_right = st.columns([0.28, 0.72])
    if top_left.button("Add blank row", key="im_add_row", type="secondary"):
        draft_df = st.session_state.get("im_master_table_df", build_master_table_dataframe(conn)).copy()
        draft_df = pd.concat([draft_df, pd.DataFrame([blank_master_table_row()])], ignore_index=True)
        st.session_state["im_master_table_df"] = draft_df[MASTER_TABLE_COLUMNS]
        st.session_state.pop("im_master_editor", None)
        safe_rerun()
    if top_right.button("Auto-classify categories", key="im_autoclass", type="secondary"):
        try:
            preview = auto_classify_parts(conn, apply=False)
            st.session_state["im_autoclass_preview"] = preview
            safe_rerun()
        except Exception as exc:
            st.error(str(exc))

    st.caption("Edit any item directly in the table below. The table now syncs from the database automatically whenever data changes.")

    draft_df = st.session_state.get("im_master_table_df", build_master_table_dataframe(conn))
    edited_df = st.data_editor(
        draft_df,
        key="im_master_editor",
        use_container_width=True,
        hide_index=True,
        height=420,
        num_rows="fixed",
        column_order=MASTER_TABLE_COLUMNS,
        disabled=["id"],
        column_config={
            "id": st.column_config.NumberColumn("ID", help="Internal row ID", disabled=True, width="small"),
            "part_id": st.column_config.TextColumn("Item Code", required=True),
            "name": st.column_config.TextColumn("Name", required=True, width="medium"),
            "description": st.column_config.TextColumn("Description", width="large"),
            "unit": st.column_config.TextColumn("Unit", width="small"),
            "quantity": st.column_config.NumberColumn("Quantity", min_value=0, step=1, format="%d"),
            "location": st.column_config.TextColumn("Location", width="medium"),
            "min_level": st.column_config.NumberColumn("Min Level", min_value=0, step=1, format="%d"),
            "reorder_qty": st.column_config.NumberColumn("Reorder Qty", min_value=0, step=1, format="%d"),
            "category": st.column_config.SelectboxColumn("Category", options=MASTER_CATEGORY_OPTIONS, required=True),
            "active": st.column_config.CheckboxColumn("Active"),
        },
    )
    st.session_state["im_master_table_df"] = edited_df[MASTER_TABLE_COLUMNS]

    if st.session_state.pop("im_clear_master_password", False):
        st.session_state.pop("im_master_password", None)

    save_col, password_col = st.columns([0.26, 0.74])
    save_clicked = save_col.button("Save table", key="im_save_table", type="primary")
    master_password = password_col.text_input(
        "Save password",
        type="password",
        placeholder="Enter password to save edits",
        key="im_master_password",
    )

    if save_clicked:
        if master_password != MASTER_SAVE_PASSWORD:
            st.error("Incorrect save password.")
        else:
            try:
                result = save_master_table(conn, edited_df.to_dict("records"))
                reset_master_table_draft(conn)
                st.session_state["im_clear_master_password"] = True
                st.session_state["im_master_save_notice"] = (
                    f"Master table saved. {result['updated']} row(s) updated, {result['inserted']} row(s) added."
                )
                safe_rerun()
            except Exception as exc:
                st.error(str(exc))

    # ── CSV Export / Import ───────────────────────────────────────────────
    st.markdown("---")
    st.markdown("**Export / Import items (CSV)**")
    col_exp, col_imp = st.columns(2)

    with col_exp:
        all_parts = get_parts(conn, active_only=False)
        if all_parts:
            exp_df = pd.DataFrame(rows_to_dicts(all_parts)).drop(
                columns=["id", "created_at", "updated_at"], errors="ignore"
            )
            exp_df = exp_df.rename(columns={"part_id": "item_code"})
            st.download_button(
                "⬇ Export all items",
                exp_df.to_csv(index=False).encode("utf-8"),
                file_name="items_export.csv",
                mime="text/csv",
                key="im_export",
            )
        else:
            st.info("No items to export yet.")

    # show autoclass preview/apply controls if present
    if st.session_state.get("im_autoclass_preview"):
        preview = st.session_state.get("im_autoclass_preview")
        changes = [c for c in preview["changes"] if c["current"] != c["suggested"]]
        st.markdown("**Auto-classify preview**")
        if not changes:
            st.info("No suggested changes. Items already classified or matched Others.")
        else:
            df = pd.DataFrame(changes)
            st.dataframe(df[["part_id", "name", "current", "suggested"]], use_container_width=True)
            c1, c2 = st.columns([0.5, 0.5])
            if c1.button("Apply suggested categories", key="im_autoclass_apply"):
                try:
                    result = auto_classify_parts(conn, apply=True)
                    reset_master_table_draft(conn)
                    st.success(f"Applied {result['updated']} category updates")
                    st.session_state.pop("im_autoclass_preview", None)
                    safe_rerun()
                except Exception as exc:
                    st.error(str(exc))
            if c2.button("Dismiss", key="im_autoclass_dismiss"):
                st.session_state.pop("im_autoclass_preview", None)
                safe_rerun()

    with col_imp:
        if "im_upload_nonce" not in st.session_state:
            st.session_state["im_upload_nonce"] = 0

        import_result = st.session_state.pop("im_import_result", None)
        if import_result is not None:
            result_text = format_import_summary(import_result)
            if import_result["duplicate_rows"] or import_result["invalid_rows"] or import_result.get("near_duplicate_rows"):
                preview_text = "; ".join(import_result["messages"][:3])
                st.warning(f"{result_text} {preview_text}".strip())
            else:
                st.success(f"✅  {result_text}")

        uploaded = st.file_uploader("⬆ Import CSV", type=["csv"], key=next_import_upload_key())
        if uploaded is not None and not st.session_state.get("im_import_preview"):
            try:
                import_df = pd.read_csv(uploaded, dtype=str).fillna("")
                records = import_df.to_dict("records")
                preview = analyze_parts_import(conn, records)
                preview["records"] = records
                preview["file_name"] = uploaded.name
                st.session_state["im_import_preview"] = preview
                safe_rerun()
            except Exception as exc:
                st.error(f"Import failed: {exc}")

        if st.session_state.get("im_import_preview"):
            show_import_review_dialog(conn)


def dashboard_page(conn):
    # optional category filter for dashboard
    parts_all = get_parts(conn, active_only=False)
    categories = sorted({safe_part_field(p, "category", "Others") for p in parts_all})
    selected_category = st.selectbox("Category", ["All"] + categories, key="dash_category")

    metrics = get_dashboard_metrics_with_category(conn, None if selected_category == "All" else selected_category)
    top_items = get_top_consumed_items_by_category(conn, selected_category if selected_category != "All" else None)
    machine_usage = get_machine_usage_by_category(conn, selected_category if selected_category != "All" else None)
    recent_rows = list_transactions(conn, limit=8)

    low_cls  = "mc-danger" if metrics["low_stock_items"] > 0 else ""
    zero_cls = "mc-danger" if metrics["out_of_stock_items"] > 0 else ""
    st.markdown(
        f"""
        <div class="metrics-grid">
            <div class="metric-card mc-accent">
                <div class="metric-icon">📦</div>
                <div class="metric-label">Active Items</div>
                <div class="metric-value">{metrics['total_items']}</div>
            </div>
            <div class="metric-card mc-accent">
                <div class="metric-icon">🏷️</div>
                <div class="metric-label">Total Units in Store</div>
                <div class="metric-value m-accent">{metrics['total_stock_units']}</div>
            </div>
            <div class="metric-card {low_cls}">
                <div class="metric-icon">⚠️</div>
                <div class="metric-label">Low Stock Items</div>
                <div class="metric-value {'m-danger' if metrics['low_stock_items'] > 0 else ''}">{metrics['low_stock_items']}</div>
            </div>
            <div class="metric-card {zero_cls}">
                <div class="metric-icon">🚫</div>
                <div class="metric-label">Out of Stock</div>
                <div class="metric-value {'m-danger' if metrics['out_of_stock_items'] > 0 else ''}">{metrics['out_of_stock_items']}</div>
            </div>
            <div class="metric-card mc-issued">
                <div class="metric-icon">⬆️</div>
                <div class="metric-label">Issued Today</div>
                <div class="metric-value m-issued">{metrics['issues_today']}</div>
            </div>
            <div class="metric-card mc-deposit">
                <div class="metric-icon">📥</div>
                <div class="metric-label">Inwarded Today</div>
                <div class="metric-value m-deposit">{metrics['deposits_today']}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<div class="section-label">Most consumed items</div>', unsafe_allow_html=True)
    top_df = pd.DataFrame(rows_to_dicts(top_items))
    if top_df.empty:
        st.info("No issue history yet.")
    else:
        st.dataframe(top_df, use_container_width=True, hide_index=True)

    st.markdown('<div class="section-label">Top machine usage</div>', unsafe_allow_html=True)
    mdf = pd.DataFrame(rows_to_dicts(machine_usage))
    if mdf.empty:
        st.info("No machine-wise issue history yet.")
    else:
        st.dataframe(mdf, use_container_width=True, hide_index=True)

    st.markdown('<div class="section-label">Recent activity</div>', unsafe_allow_html=True)
    rdf = pd.DataFrame(rows_to_dicts(recent_rows))
    if rdf.empty:
        st.info("No stock movement yet.")
    else:
        display_cols = [
            c for c in ["created_at", "tx_type", "part_name", "qty", "unit",
                         "performed_by", "machine_sn", "purpose", "balance_stock"]
            if c in rdf.columns
        ]
        st.dataframe(rdf[display_cols], use_container_width=True, hide_index=True)


def history_page(conn):
    fc1, fc2 = st.columns(2)
    hist_options = ["all", "issue", "deposit", "return"]
    tx_type = fc1.selectbox(
        "Type",
        hist_options,
        format_func=lambda x: {"all": "All", "issue": "Issue", "deposit": "Inward", "return": "Return"}.get(x, x),
        key="hist_type",
    )
    with fc2:
        search = live_search_input("Search", "Item, serial no, user…", "hist_search")

    role = st.session_state.get("role", "user")
    performed_by = None if role == "manager" else st.session_state["user"]

    if role == "manager":
        open_returns = list_open_returnable_issues(conn, search=search.strip())
        st.markdown("<div class='section-label'>Pending Returnable Items</div>", unsafe_allow_html=True)
        if not open_returns:
            st.info("No pending returnable items.")
        else:
            with st.container(height=260, border=True):
                for issue in open_returns:
                    meta = issue["machine_sn"] or issue["purpose"] or "No machine / purpose noted"
                    label = f"{issue['part_name']} | {issue['part_id']} | {meta}"
                    if st.button(
                        label,
                        key=f"return_issue_{issue['id']}",
                        use_container_width=True,
                        type="secondary",
                    ):
                        st.session_state["return_dialog_issue_id"] = int(issue["id"])
                        safe_rerun()
        if st.session_state.get("return_dialog_issue_id"):
            show_return_dialog(conn)
        st.markdown("---")

    rows = list_transactions(conn, tx_type=tx_type, search=search.strip(), performed_by=performed_by)
    df = pd.DataFrame(rows_to_dicts(rows))

    if df.empty:
        st.info("No matching records.")
        return

    def _return_status(row):
        if row.get("tx_type") == "return":
            return "Returned"
        if row.get("tx_type") == "issue" and row.get("returnable"):
            return "Pending Return" if not row.get("returned_at") else ""
        return ""

    df["return_status"] = df.apply(_return_status, axis=1)

    display_cols = [
        c for c in ["created_at", "tx_type", "return_status", "part_name", "qty", "unit",
                     "performed_by", "machine_sn", "purpose", "returnable",
                     "prev_stock", "balance_stock", "note"]
        if c in df.columns
    ]
    st.dataframe(df[display_cols], use_container_width=True, hide_index=True)
    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button("⬇ Export CSV", csv, file_name="inventory_history.csv", mime="text/csv")


def alerts_page(conn):
    alerts = low_stock_alerts(conn)
    if not alerts:
        st.success("✅  All items are above their minimum stock level.")
        return

    st.warning(f"⚠️  {len(alerts)} item(s) at or below minimum stock level")
    for part in alerts:
        st.markdown(
            f"""
            <div class="item-card">
                <div class="item-name">{part['name']}</div>
                <div class="item-desc">{part['description']}</div>
                <div class="pill-row">
                    <span class="pill p-zero">Current: {part['quantity']} {part['unit']}</span>
                    <span class="pill p-neutral">Min: {part['min_level']}</span>
                    <span class="pill p-neutral">Reorder: {part['reorder_qty']}</span>
                    <span class="pill p-neutral">Location: {part['location']}</span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_main_navigation(options, key):
    default_option = st.session_state.get(key, options[0])
    if default_option not in options:
        default_option = options[0]

    if hasattr(st, "pills"):
        selected_option = st.pills(
            "Section",
            options,
            selection_mode="single",
            default=default_option,
            required=True,
            key=key,
            label_visibility="collapsed",
            width="stretch",
        )
        return selected_option or default_option

    return st.selectbox(
        "Section",
        options,
        index=options.index(default_option),
        key=key,
        label_visibility="collapsed",
    )


def clear_inactive_page_state(active_section):
    if active_section != "⬆ Pick":
        for key in ["pick_done", "pick_selected_id", "pick_animation_shown", "pick_dialog_part_id"]:
            st.session_state.pop(key, None)
    if active_section != "📥 Inward":
        for key in ["deposit_done", "deposit_animation_shown", "deposit_dialog_part_id"]:
            st.session_state.pop(key, None)
    if active_section != "↩ Returnables":
        st.session_state.pop("return_dialog_issue_id", None)
    if active_section != "📦 Items":
        st.session_state.pop("items_dialog_part_id", None)


def main():
    inject_theme()

    conn = get_conn()
    init_db(conn)
    if os.path.exists(RESET_EMPTY_MARKER):
        os.remove(RESET_EMPTY_MARKER)
    else:
        bootstrap_parts_catalog(conn)
    sync_parts_snapshot_csv(conn)
    sidebar_identity(conn)

    role = st.session_state.get("role", "user")
    alerts = low_stock_alerts(conn)

    # header row
    brand_col, title_col = st.columns([0.18, 0.82])
    with brand_col:
        render_brand_logo(width=48)
    with title_col:
        st.markdown(
            """
            <div class="brand-header">
                <div>
                    <div class="brand-title">Eqvimech Inventory</div>
                    <div class="brand-subtitle">Machine spares, inward stock, issue tracking, and launch-ready controls.</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    if alerts:
        st.markdown(
            f'<div class="low-stock-banner">'
            f'⚠️&nbsp; {len(alerts)} item(s) are at or below minimum stock level'
            f'</div>',
            unsafe_allow_html=True,
        )

    # controlled navigation: render only the active section so transient state
    # does not persist after users switch away and come back.
    if role == "manager":
        nav_options = ["📦 Items", "⬆ Pick", "📥 Inward", "↩ Returnables", "🗂 Master", "📊 Dashboard", "📋 History", "🔔 Alerts"]
        active_section = render_main_navigation(nav_options, "main_nav_manager")
        clear_inactive_page_state(active_section)

        if active_section == "📦 Items":
            items_page(conn)
        elif active_section == "⬆ Pick":
            pick_material_page(conn)
        elif active_section == "📥 Inward":
            deposit_stock_page(conn)
        elif active_section == "↩ Returnables":
            returnables_page(conn)
        elif active_section == "🗂 Master":
            item_master_page(conn)
        elif active_section == "📊 Dashboard":
            dashboard_page(conn)
        elif active_section == "📋 History":
            history_page(conn)
        else:
            alerts_page(conn)
    else:
        nav_options = ["📦 Items", "⬆ Pick", "📋 History", "🔔 Alerts"]
        active_section = render_main_navigation(nav_options, "main_nav_user")
        clear_inactive_page_state(active_section)

        if active_section == "📦 Items":
            items_page(conn)
        elif active_section == "⬆ Pick":
            pick_material_page(conn)
        elif active_section == "📋 History":
            history_page(conn)
        else:
            alerts_page(conn)

    # render faint version/timestamp stamp so users can confirm deployed build
    try:
        render_version_stamp()
    except Exception:
        pass

if __name__ == "__main__":
    main()
