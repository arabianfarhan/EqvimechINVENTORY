import os
import hmac
import html
import subprocess
import threading
import datetime as dt
from contextlib import contextmanager
from zoneinfo import ZoneInfo
import pandas as pd
import psycopg2
import streamlit as st
from st_keyup import st_keyup

from db import (
    APP_TIMEZONE,
    CATEGORY_OPTIONS,
    ConnectionManager,
    StockConflictError,
    analyze_parts_import,
    bootstrap_parts_catalog,
    deposit_stock,
    get_part,
    get_parts,
    get_transaction,
    get_dashboard_metrics_with_category,
    get_top_consumed_items_by_category,
    get_machine_usage_by_category,
    import_parts_from_csv,
    auto_classify_parts,
    init_db,
    list_open_returnable_issues,
    list_returned_returnable_issues,
    list_transactions,
    pick_material,
    return_issue_material,
    rows_to_dicts,
    save_master_table,
    ITEMS_SNAPSHOT_CSV_PATH,
    normalize_category,
    wipe_all_data,
)

st.set_page_config(
    page_title="Eqvimech Inventory",
    page_icon="🏭",
    layout="centered",
    initial_sidebar_state="auto",
)

APP_DIR = os.path.dirname(os.path.abspath(__file__))
LOGO_PATH = os.path.join(APP_DIR, "assets", "eqvimech_logo.svg")
MASTER_CATEGORY_OPTIONS = list(CATEGORY_OPTIONS)
PICK_USER_OPTIONS = ["Ravi", "Shani", "Suraj", "Mangesh", "Ram", "Sonu", "Sandip", "Other"]

# Old default codes. They are in a public GitHub repo, so they must be
# replaced via Streamlit secrets (MANAGER_PASSWORD / RESET_CODE).
_LEGACY_MANAGER_PASSWORD = "321"


def _secret(name, default=None):
    try:
        value = st.secrets.get(name)
    except Exception:
        value = None
    if value in (None, ""):
        value = os.environ.get(name, default)
    return str(value) if value not in (None, "") else default


def manager_password_is_default():
    return _secret("MANAGER_PASSWORD") is None


def check_manager_password(password):
    expected = _secret("MANAGER_PASSWORD", _LEGACY_MANAGER_PASSWORD)
    return hmac.compare_digest(str(password or ""), expected)


def esc(value):
    """HTML-escape DB text before putting it inside unsafe_allow_html markup."""
    return html.escape(str(value if value is not None else ""))


def to_ist(df, columns=("created_at", "returned_at")):
    """Show database timestamps (stored in UTC) as Indian time."""
    for col in columns:
        if col in df.columns:
            converted = pd.to_datetime(df[col], utc=True, errors="coerce").dt.tz_convert(APP_TIMEZONE)
            df[col] = converted.dt.strftime("%d-%b-%Y %H:%M").fillna("")
    return df


def fmt_ist(value):
    if not value:
        return ""
    try:
        return pd.Timestamp(value).tz_convert(APP_TIMEZONE).strftime("%d-%b-%Y %H:%M")
    except Exception:
        return str(value)


@st.cache_resource
def _connection_manager():
    """One connection pool per server process, shared safely by all users."""
    return ConnectionManager()


@st.cache_resource
def _run_db_init():
    """Run DB schema setup + one-time catalogue bootstrap once per server process."""
    manager = _connection_manager()
    conn = manager.getconn()
    try:
        init_db(conn)
        bootstrap_parts_catalog(conn)
    finally:
        manager.putconn(conn)
    return True


class LazyConnection:
    """Borrow a pooled DB connection only when a query actually needs one.

    Most reruns are served entirely from cache, so they never touch the
    database (and never pay the US<->Mumbai network delay).
    """

    def __init__(self, manager):
        object.__setattr__(self, "_manager", manager)
        object.__setattr__(self, "_conn", None)

    def _real(self):
        if self._conn is None:
            object.__setattr__(self, "_conn", self._manager.getconn())
        return self._conn

    def __getattr__(self, name):
        return getattr(self._real(), name)

    def __setattr__(self, name, value):
        setattr(self._real(), name, value)

    def release(self):
        if self._conn is not None:
            self._manager.putconn(self._conn)
            object.__setattr__(self, "_conn", None)


@contextmanager
def db_session():
    """A connection scope for one script run, fragment rerun or dialog rerun.

    If the database connection was dropped (network blip, Supabase restart or
    idle timeout) the broken connection is discarded and the page reloads
    itself once, so users don't see an error.
    """
    manager = _connection_manager()
    lazy = LazyConnection(manager)
    try:
        yield lazy
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        real = lazy._conn
        if real is not None and not real.closed:
            try:
                real.close()
            except Exception:
                pass
        lazy.release()
        manager.mark_all_stale()
        if not st.session_state.get("_db_retry"):
            st.session_state["_db_retry"] = True
            st.rerun()
        st.session_state.pop("_db_retry", None)
        raise
    else:
        st.session_state.pop("_db_retry", None)
    finally:
        lazy.release()


# ── Shared data cache ───────────────────────────────────────────────────────
# Every write made through the app bumps this version, so cached reads are
# refreshed instantly for every user. The TTL only matters for changes made
# outside the app (e.g. directly in Supabase).
@st.cache_resource
def _data_state():
    return {"version": 0, "lock": threading.Lock()}


def data_version():
    return _data_state()["version"]


def bump_data_version():
    state = _data_state()
    with state["lock"]:
        state["version"] += 1


def _today_ist():
    return dt.datetime.now(tz=ZoneInfo(APP_TIMEZONE)).strftime("%Y-%m-%d")


@st.cache_data(ttl=300, show_spinner=False, max_entries=4)
def _cached_parts(_conn, version):
    return [dict(r) for r in get_parts(_conn, active_only=False)]


def all_parts(conn):
    return _cached_parts(conn, data_version())


def active_parts(conn):
    return [p for p in all_parts(conn) if p.get("active")]


def low_stock_from_parts(parts):
    low = [
        p for p in parts
        if p.get("active") and int(p.get("quantity") or 0) <= int(p.get("min_level") or 0)
    ]
    return sorted(low, key=lambda p: (int(p.get("quantity") or 0), str(p.get("name") or "")))


@st.cache_data(ttl=300, show_spinner=False, max_entries=30)
def _cached_dashboard(_conn, version, category, today):
    cat = None if category == "All" else category
    return {
        "metrics": dict(get_dashboard_metrics_with_category(_conn, cat)),
        "top": rows_to_dicts(get_top_consumed_items_by_category(_conn, cat)),
        "machines": rows_to_dicts(get_machine_usage_by_category(_conn, cat)),
        "recent": rows_to_dicts(list_transactions(_conn, limit=10)),
    }


@st.cache_data(ttl=300, show_spinner=False, max_entries=60)
def _cached_history(_conn, version, tx_type, search, since_days, today):
    return rows_to_dicts(
        list_transactions(_conn, tx_type=tx_type, search=search, limit=1000, since_days=since_days)
    )


@st.cache_data(ttl=300, show_spinner=False, max_entries=40)
def _cached_returnables(_conn, version, search):
    return (
        rows_to_dicts(list_open_returnable_issues(_conn, search=search)),
        rows_to_dicts(list_returned_returnable_issues(_conn, search=search)),
    )


@st.cache_data(ttl=3600)
def _cached_version_info():
    return get_version_info()


def safe_rerun(sync_csv=False):
    getattr(st, "rerun", getattr(st, "experimental_rerun", lambda: None))()


def clear_widget_keys(prefix, part_id):
    """Forget a dialog's inputs so reopening it starts clean (and a stale
    quantity can never exceed the new stock limit)."""
    suffix = f"_{part_id}"
    for key in list(st.session_state.keys()):
        if isinstance(key, str) and key.startswith(prefix) and key.endswith(suffix):
            st.session_state.pop(key, None)


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
            max-width: 1300px !important;
        }
        @media (min-width: 1100px) {
            .block-container {
                padding-top: 3.5rem !important;
                max-width: 1500px !important;
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
        .item-name-row { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 0.15rem; }
        .item-name { color: var(--glass-text); font-size: 1rem; font-weight: 700; }
        .item-code-badge { font-size: 0.75rem; font-weight: 600; color: var(--accent); opacity: 0.8; white-space: nowrap; margin-left: 0.5rem; }
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
        
        /* ── Page titles & result cards ── */
        .page-title { font-size: 1.15rem; font-weight: 800; color: #0f172a; margin: 0.2rem 0 0.1rem 0; }
        .page-sub { font-size: 0.85rem; color: #64748b; margin-bottom: 0.7rem; }
        .done-card {
            background: rgba(220, 252, 231, 0.85); border: 1px solid #86efac; border-radius: 18px;
            padding: 1.6rem 1rem; text-align: center; margin: 0.8rem 0 1rem 0;
            box-shadow: 0 14px 30px rgba(22, 163, 74, 0.12);
        }
        .done-icon { font-size: 2.4rem; line-height: 1; }
        .done-title { font-size: 1.35rem; font-weight: 800; color: #15803d; margin-top: 0.4rem; }
        .done-detail { font-size: 1rem; color: #166534; margin-top: 0.5rem; }
        .done-note { margin-top: 0.6rem; font-size: 0.9rem; color: #b45309; font-weight: 700; }
        /* item list buttons: left aligned, compact on phones */
        div[data-testid="stVerticalBlockBorderWrapper"] .stButton > button[kind="secondary"] {
            justify-content: flex-start !important; text-align: left !important;
        }
        div[data-testid="stVerticalBlockBorderWrapper"] .stButton > button[kind="secondary"] p {
            text-align: left !important;
        }
        @media (max-width: 640px) {
            .stButton > button[kind="secondary"] { min-height: 2.6rem !important; font-size: 0.88rem !important; }
            .page-title { font-size: 1.05rem; }
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
        short = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=APP_DIR, stderr=subprocess.DEVNULL).decode().strip()
        epoch = subprocess.check_output(["git", "show", "-s", "--format=%ct", "HEAD"], cwd=APP_DIR, stderr=subprocess.DEVNULL).decode().strip()
        ts = dt.datetime.fromtimestamp(int(epoch), tz=ZoneInfo(APP_TIMEZONE)).strftime("%d-%b-%Y %H:%M IST")
    except Exception:
        try:
            if os.path.exists(ITEMS_SNAPSHOT_CSV_PATH):
                m = os.path.getmtime(ITEMS_SNAPSHOT_CSV_PATH)
                ts = dt.datetime.fromtimestamp(m, tz=ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S IST")
        except Exception:
            ts = None
    if not ts:
        ts = dt.datetime.now(tz=ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S IST")
    return short, ts


def render_version_stamp():
    ver, ts = _cached_version_info()
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



@st.dialog("Manager Access", width="small", dismissible=False)
def show_manager_password_dialog():
    password = st.text_input(
        "Enter manager password",
        type="password",
        key="manager_password_input",
    )
    unlock_col, cancel_col = st.columns(2)

    if unlock_col.button("Unlock", key="manager_unlock", type="primary"):
        if check_manager_password(password):
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
            if st.button("Use User", key="role_user_card", width="stretch", type="secondary"):
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
                width="stretch",
                type="primary" if manager_active else "secondary",
            ):
                if manager_active:
                    # Already unlocked — clicking again does nothing
                    pass
                else:
                    # Require password dialog before granting manager access
                    st.session_state["manager_login_requested"] = True
                    safe_rerun()

        if not manager_ready and st.session_state.get("role") != "manager":
            st.session_state["role"] = "user"
            st.session_state["user"] = "operator"
        elif manager_ready and st.session_state.get("role") == "manager":
            st.session_state["user"] = "manager"

        st.markdown("---")
        st.caption(f"Access: **{st.session_state['role'].capitalize()}**")

        is_manager = manager_ready and st.session_state.get("role") == "manager"
        if is_manager and manager_password_is_default():
            st.warning(
                "Manager password is still the default from the public code. "
                "Set MANAGER_PASSWORD in the Streamlit app secrets."
            )

        # Database wipe: managers only, and only when a RESET_CODE secret exists.
        reset_secret = _secret("RESET_CODE")
        if is_manager and reset_secret:
            st.markdown("---")
            with st.expander("Danger zone — wipe all data"):
                st.caption("Deletes every item and all history. This cannot be undone.")
                reset_code = st.text_input("Reset code", type="password", key="reset_code_input")
                confirm_text = st.text_input("Type DELETE to confirm", key="reset_confirm_text")
                if st.button("Wipe database (permanent)", key="reset_app", type="primary"):
                    if not hmac.compare_digest(str(reset_code or ""), reset_secret):
                        st.error("Incorrect reset code.")
                    elif confirm_text.strip() != "DELETE":
                        st.error("Type DELETE to confirm.")
                    else:
                        try:
                            wipe_all_data(conn)
                            bump_data_version()
                            for k in list(st.session_state.keys()):
                                st.session_state.pop(k, None)
                            safe_rerun()
                        except Exception as e:
                            st.error(f"Reset failed: {e}")

    if st.session_state.get("manager_login_requested"):
        show_manager_password_dialog()


def filter_parts(parts, query):
    q = query.strip().lower()
    if not q:
        return parts
    return [
        p for p in parts
        if q in (p["name"] or "").lower()
        or q in (p["part_id"] or "").lower()
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


PICKER_LIMIT = 60


def picker_label(part):
    """Button text: name · description, with live stock at the end."""
    base = part_list_label(part)
    qty = int(part.get("quantity") or 0)
    unit = part.get("unit") or "Nos"
    if not part.get("active"):
        badge = "inactive"
        marker = "⚪"
    elif qty <= 0:
        badge = "out of stock"
        marker = "🔴"
    elif qty <= int(part.get("min_level") or 0):
        badge = f"{qty} {unit} · low"
        marker = "🟠"
    else:
        badge = f"{qty} {unit}"
        marker = "🟢"
    return f"{marker}  {base}  —  {badge}"


@st.fragment
def render_part_picker(parts, parts_all, search_key, dialog_key, button_prefix, placeholder, category_key=None):
    # Runs as a fragment: typing in the search box only re-draws this list,
    # not the whole page.
    search_term = live_search_input("Search item", placeholder, search_key) or ""
    matches = filter_parts(parts, search_term)

    if category_key:
        categories = sorted({normalize_category(p.get("category")) for p in parts_all})
        if categories:
            sel = st.selectbox("Category", ["All"] + categories, key=category_key)
            if sel and sel != "All":
                matches = [p for p in matches if normalize_category(p.get("category")) == sel]

    shown = matches[:PICKER_LIMIT]
    if len(matches) > PICKER_LIMIT:
        st.caption(f"Showing {len(shown)} of {len(matches)} matches — type more to narrow down")
    else:
        st.caption(f"{len(matches)} of {len(parts)} items")

    with st.container(height=460, border=True):
        if not matches:
            q = search_term.strip().lower()
            inactive_matches = []
            if q:
                for p in parts_all:
                    if not p.get("active") and any(
                        q in str(p.get(f) or "").lower() for f in ("name", "part_id", "description", "location")
                    ):
                        inactive_matches.append(p)
            if inactive_matches:
                st.info(
                    f"No active items matched. {len(inactive_matches)} inactive item(s) match — "
                    "a manager can switch them on in Master."
                )
                for p in inactive_matches[:30]:
                    st.write(part_list_label(p))
            else:
                st.info("No items matched your search.")
        else:
            for p in shown:
                pid = p.get("part_id", "")
                if st.button(
                    picker_label(p),
                    key=f"{button_prefix}_{pid}",
                    width="stretch",
                    type="secondary",
                ):
                    st.session_state[dialog_key] = pid
                    st.rerun()


def _show_item_details_dialog_body(conn):
    part_id = st.session_state.get("items_dialog_part_id")
    part = get_part(conn, part_id) if part_id else None
    if not part:
        st.session_state.pop("items_dialog_part_id", None)
        safe_rerun()
        return

    st.markdown(
        f"""
        <div class="item-card">
            <div class="item-name-row"><span class="item-name">{esc(part['name'])}</span><span class="item-code-badge">{esc(part['part_id'])}</span></div>
            <div class="item-desc">{esc(part['description'])}</div>
            <div class="pill-row">
                <span class="pill p-neutral">Location: {esc(part['location'])}</span>
                <span class="pill p-neutral">Category: {esc(safe_part_field(part, 'category', 'Others'))}</span>
                <span class="pill p-neutral">Unit: {esc(part['unit'])}</span>
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


def _show_pick_dialog_body(conn):
    part_id = st.session_state.get("pick_dialog_part_id")
    part = get_part(conn, part_id) if part_id else None
    if not part:
        st.session_state.pop("pick_dialog_part_id", None)
        safe_rerun()
        return

    st.markdown(
        f"""
        <div class="item-card">
            <div class="item-name-row"><span class="item-name">{esc(part['name'])}</span><span class="item-code-badge">{esc(part['part_id'])}</span></div>
            <div class="item-desc">{esc(part['description'])}</div>
            <div class="pill-row">
                <span class="pill p-neutral">Location: {esc(part['location'])}</span>
                <span class="pill p-neutral">Category: {esc(safe_part_field(part, 'category', 'Others'))}</span>
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
        max_value=int(part["quantity"]),
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
    user_options = PICK_USER_OPTIONS
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

    picked_by = (selected_user or "").strip()
    if selected_user == "Other":
        user_other = st.text_input("Specify other user", placeholder="Name", key=f"pick_user_other_{part_id}")
        picked_by = user_other.strip()

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
        elif not picked_by:
            st.error("Please select the user name (or type the name for Other).")
        else:
            try:
                new_balance = pick_material(
                    conn,
                    part["part_id"],
                    [serial],
                    int(qty),
                    picked_by,
                    st.session_state["role"],
                    purpose_str,
                    "",
                    returnable=returnable,
                )
                bump_data_version()
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
                clear_widget_keys("pick_", part_id)
                safe_rerun()
            except Exception as exc:
                st.error(str(exc))

    if close_col.button("Cancel", key=f"close_pick_dialog_{part_id}", type="secondary"):
        st.session_state.pop("pick_dialog_part_id", None)
        clear_widget_keys("pick_", part_id)
        safe_rerun()


def _show_deposit_dialog_body(conn):
    part_id = st.session_state.get("deposit_dialog_part_id")
    part = get_part(conn, part_id) if part_id else None
    if not part:
        st.session_state.pop("deposit_dialog_part_id", None)
        safe_rerun()
        return

    st.markdown(
        f"""
        <div class="item-card">
            <div class="item-name-row"><span class="item-name">{esc(part['name'])}</span><span class="item-code-badge">{esc(part['part_id'])}</span></div>
            <div class="item-desc">{esc(part['description'])}</div>
            <div class="pill-row">
                <span class="pill p-neutral">Location: {esc(part['location'])}</span>
                <span class="pill p-neutral">Category: {esc(safe_part_field(part, 'category', 'Others'))}</span>
                <span class="pill p-neutral">Unit: {esc(part['unit'])}</span>
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
                bump_data_version()
                # record deposit done state so we can show animation on the main page
                st.session_state["deposit_done"] = {
                    "part": part["name"],
                    "qty": int(qty),
                    "balance": new_balance,
                    "unit": part["unit"],
                }
                st.session_state["deposit_animation_shown"] = False
                st.session_state.pop("deposit_dialog_part_id", None)
                clear_widget_keys("deposit_", part_id)
                safe_rerun()
            except Exception as exc:
                st.error(str(exc))

    if close_col.button("Cancel", key=f"close_deposit_dialog_{part_id}", type="secondary"):
        st.session_state.pop("deposit_dialog_part_id", None)
        clear_widget_keys("deposit_", part_id)
        safe_rerun()


def _show_return_dialog_body(conn):
    if st.session_state.get("role", "user") != "manager":
        st.error("Only managers can return material.")
        if st.button("Close", key="close_return_manager_only", type="secondary"):
            st.session_state.pop("return_dialog_issue_id", None)
            safe_rerun()
        return

    issue_tx_id = st.session_state.get("return_dialog_issue_id")
    issue = get_transaction(conn, int(issue_tx_id)) if issue_tx_id else None
    if issue is not None and (issue["tx_type"] != "issue" or issue["returned_at"]):
        issue = None

    if not issue:
        st.session_state.pop("return_dialog_issue_id", None)
        safe_rerun()
        return

    st.markdown(
        f"""
        <div class="item-card">
            <div class="item-name">{esc(issue['part_name'])}</div>
            <div class="item-desc">Issued by {esc(issue['performed_by'])} on {fmt_ist(issue['created_at'])}</div>
            <div class="pill-row">
                <span class="pill p-neutral">{esc(issue['part_id'])}</span>
                <span class="pill p-neutral">Qty: {issue['qty']} {esc(issue['unit'])}</span>
                <span class="pill p-neutral">Machine: {esc(issue['machine_sn'] or 'N/A')}</span>
                <span class="pill p-neutral">Purpose: {esc(issue['purpose'] or 'N/A')}</span>
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
                "manager",
                st.session_state["role"],
                note.strip(),
            )
            bump_data_version()
            st.session_state.pop("return_dialog_issue_id", None)
            st.session_state["return_notice"] = (
                f"↩ {issue['part_name']} returned to store. New balance: {new_balance} {issue['unit']}"
            )
            safe_rerun()
        except Exception as exc:
            st.error(str(exc))
    if close_col.button("Cancel", key=f"cancel_return_{issue_tx_id}", type="secondary"):
        st.session_state.pop("return_dialog_issue_id", None)
        safe_rerun()


def _show_import_review_dialog_body(conn):
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
        st.dataframe(style_import_preview_dataframe(preview_df), width="stretch", hide_index=True, height=380)

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
        n_changes = sum(
            1 for e in preview["rows"] if e["action"] in {"insert", "update"}
        )
        prog = st.progress(0, text=f"⬆ Saving {n_changes} item(s) to database…")
        try:
            result = import_parts_from_csv(conn, preview["records"], pre_analyzed_rows=preview["rows"])
            bump_data_version()
        except Exception as exc:
            conn.rollback()
            prog.empty()
            st.error(f"Import failed, nothing was saved: {exc}")
            return
        prog.progress(100, text="✅ Import complete!")
        st.session_state["im_import_result"] = result
        clear_import_review_state(reset_uploader=True)
        safe_rerun()

    if cancel_col.button("Cancel and Reupload", key="im_cancel_import_review", type="secondary"):
        clear_import_review_state(reset_uploader=True)
        safe_rerun()


@st.dialog("Item Details", width="large", dismissible=False)
def show_item_details_dialog(conn=None):
    # Dialog reruns happen on their own, so each borrows its own connection.
    with db_session() as dialog_conn:
        _show_item_details_dialog_body(dialog_conn)


@st.dialog("Issue Material", width="large", dismissible=False)
def show_pick_dialog(conn=None):
    # Dialog reruns happen on their own, so each borrows its own connection.
    with db_session() as dialog_conn:
        _show_pick_dialog_body(dialog_conn)


@st.dialog("Inward Stock", width="large", dismissible=False)
def show_deposit_dialog(conn=None):
    # Dialog reruns happen on their own, so each borrows its own connection.
    with db_session() as dialog_conn:
        _show_deposit_dialog_body(dialog_conn)


@st.dialog("Return Material", width="large", dismissible=False)
def show_return_dialog(conn=None):
    # Dialog reruns happen on their own, so each borrows its own connection.
    with db_session() as dialog_conn:
        _show_return_dialog_body(dialog_conn)


@st.dialog("Review CSV Import", width="large", dismissible=False)
def show_import_review_dialog(conn=None):
    # Dialog reruns happen on their own, so each borrows its own connection.
    with db_session() as dialog_conn:
        _show_import_review_dialog_body(dialog_conn)


def _success_card(icon, title, detail_html, extra_html=""):
    st.markdown(
        f"""
        <div class="done-card">
            <div class="done-icon">{icon}</div>
            <div class="done-title">{title}</div>
            <div class="done-detail">{detail_html}</div>
            {extra_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _section_title(text, sub=""):
    sub_html = f'<div class="page-sub">{esc(sub)}</div>' if sub else ""
    st.markdown(f'<div class="page-title">{esc(text)}</div>{sub_html}', unsafe_allow_html=True)


def returnables_page(conn):
    role = st.session_state.get("role", "user")
    _section_title("Returnable material", "Items issued on loan that must come back to the store.")
    notice = st.session_state.pop("return_notice", None)
    if notice:
        st.success(notice)

    search = (live_search_input("Search returnables", "Item, serial no, purpose, user…", "returnables_search") or "").strip()
    pending_rows, done_rows = _cached_returnables(conn, data_version(), search)

    pending_tab, done_tab = st.tabs([f"Pending ({len(pending_rows)})", f"Returned ({len(done_rows)})"])

    with pending_tab:
        if not pending_rows:
            st.info("No pending returnable items.")
        else:
            if role == "manager":
                st.caption("Tap an item to mark it returned.")
                with st.container(height=260, border=True):
                    for issue in pending_rows:
                        meta = issue["machine_sn"] or issue["purpose"] or "No machine / purpose noted"
                        label = f"↩  {issue['part_name']}  ·  {issue['qty']} {issue['unit']}  ·  {meta}  ·  {issue['performed_by']}"
                        if st.button(label, key=f"returnables_pending_{issue['id']}", width="stretch", type="secondary"):
                            st.session_state["return_dialog_issue_id"] = int(issue["id"])
                            safe_rerun()
                if st.session_state.get("return_dialog_issue_id"):
                    show_return_dialog()
            pending_df = to_ist(pd.DataFrame(pending_rows))
            cols = [c for c in ["created_at", "part_name", "part_id", "qty", "unit", "performed_by", "machine_sn", "purpose"] if c in pending_df.columns]
            st.dataframe(pending_df[cols], width="stretch", hide_index=True, column_config=HISTORY_COLUMN_CONFIG)

    with done_tab:
        if not done_rows:
            st.info("No returned items yet.")
        else:
            done_df = to_ist(pd.DataFrame(done_rows))
            cols = [c for c in ["returned_at", "created_at", "part_name", "part_id", "qty", "unit", "performed_by", "machine_sn", "purpose"] if c in done_df.columns]
            st.dataframe(done_df[cols], width="stretch", hide_index=True, column_config=HISTORY_COLUMN_CONFIG)


def items_page(conn):
    parts_all = all_parts(conn)
    parts_active = [p for p in parts_all if p.get("active")]
    if not parts_active:
        st.info("No items available.")
        return
    render_part_picker(
        parts_active,
        parts_all,
        "items_search",
        "items_dialog_part_id",
        "items_browser",
        "Search item name, code, description or location…",
        category_key="items_category",
    )
    if st.session_state.get("items_dialog_part_id"):
        show_item_details_dialog()


def pick_material_page(conn):
    done = st.session_state.get("pick_done")
    if done:
        if not st.session_state.get("pick_animation_shown"):
            _show_arrow_animation_once("pick")
        extra = (
            "<div class='done-note'>↩ Returnable — item must be returned to store</div>"
            if done.get("returnable") else ""
        )
        _success_card(
            "✅",
            "Material issued",
            f"<strong>{done['qty']}</strong> × {esc(done['part'])} &nbsp;|&nbsp; New balance: "
            f"<strong>{done['balance']} {esc(done['unit'])}</strong>",
            extra,
        )
        if st.button("Issue another item", key="pick_another", type="primary"):
            for key in ["pick_done", "pick_selected_id", "pick_animation_shown"]:
                st.session_state.pop(key, None)
            safe_rerun()
        return

    parts_all = all_parts(conn)
    parts_active = [p for p in parts_all if p.get("active")]
    if not parts_active:
        st.info("No active items available for issue.")
        return
    render_part_picker(
        parts_active,
        parts_all,
        "pick_search",
        "pick_dialog_part_id",
        "pick_browser",
        "Search item to issue — name, code, description…",
    )
    if st.session_state.get("pick_dialog_part_id"):
        show_pick_dialog()


def deposit_stock_page(conn):
    deposit_done = st.session_state.get("deposit_done")
    if deposit_done:
        if not st.session_state.get("deposit_animation_shown"):
            _show_arrow_animation_once("deposit")
        _success_card(
            "📥",
            "Stock inwarded",
            f"<strong>{deposit_done['qty']}</strong> × {esc(deposit_done['part'])} &nbsp;|&nbsp; New balance: "
            f"<strong>{deposit_done['balance']} {esc(deposit_done['unit'])}</strong>",
        )
        if st.button("Inward another item", key="inward_another", type="primary"):
            st.session_state.pop("deposit_done", None)
            st.session_state.pop("deposit_animation_shown", None)
            safe_rerun()
        return

    parts_all = all_parts(conn)
    if not parts_all:
        st.info("Add items in Master before inwarding stock.")
        return
    render_part_picker(
        parts_all,
        parts_all,
        "deposit_search",
        "deposit_dialog_part_id",
        "deposit_browser",
        "Search item to inward — name, code, description…",
    )
    if st.session_state.get("deposit_dialog_part_id"):
        show_deposit_dialog()


# ── Item Master ─────────────────────────────────────────────────────────────
MASTER_EDIT_COLUMNS = [
    "id", "part_id", "name", "quantity", "unit", "location", "category",
    "min_level", "reorder_qty", "description", "active",
]


def master_dataframe(parts, query, category, show_inactive):
    q = (query or "").strip().lower()
    rows = []
    for p in parts:
        if not show_inactive and not p.get("active"):
            continue
        cat = normalize_category(p.get("category"))
        if category != "All" and cat != category:
            continue
        if q and not any(q in str(p.get(f) or "").lower() for f in ("part_id", "name", "description", "location")):
            continue
        rows.append(
            {
                "id": int(p["id"]),
                "part_id": str(p.get("part_id") or "").strip(),
                "name": str(p.get("name") or "").strip(),
                "quantity": int(p.get("quantity") or 0),
                "unit": str(p.get("unit") or "Nos").strip() or "Nos",
                "location": str(p.get("location") or "").strip(),
                "category": cat,
                "min_level": int(p.get("min_level") or 0),
                "reorder_qty": int(p.get("reorder_qty") or 0),
                "description": str(p.get("description") or "").strip(),
                "active": bool(p.get("active")),
            }
        )
    df = pd.DataFrame(rows, columns=MASTER_EDIT_COLUMNS)
    df["id"] = df["id"].astype("Int64")
    return df


def _editor_pending(editor_key):
    state = st.session_state.get(editor_key)
    if not state:
        return False
    try:
        return bool(state.get("edited_rows") or state.get("added_rows") or state.get("deleted_rows"))
    except Exception:
        return False


def _master_signature(df):
    return tuple(tuple(r) for r in df.astype(object).where(df.notna(), "").itertuples(index=False, name=None))


def _master_reload():
    """Throw away the current editor instance so the next run rebuilds it from fresh data."""
    old_key = f"im_editor_{st.session_state.get('im_nonce', 0)}"
    st.session_state.pop(old_key, None)
    st.session_state["im_nonce"] = st.session_state.get("im_nonce", 0) + 1
    for key in ("im_view_key", "im_failed_sig", "im_committed_rows", "im_committed_sig", "im_base_stale"):
        st.session_state.pop(key, None)


def _records(df):
    return df.astype(object).where(df.notna(), "").to_dict("records")


@st.fragment
def master_table_fragment():
    # A fragment: editing a cell re-runs only this table, not the whole app.
    with db_session() as conn:
        _master_table(conn)


def _master_table(conn):
    notice = st.session_state.pop("im_notice", None)
    if notice:
        kind, text = notice
        getattr(st, kind)(text)

    parts = all_parts(conn)
    categories = sorted(set(MASTER_CATEGORY_OPTIONS) | {normalize_category(p.get("category")) for p in parts})

    f1, f2, f3, f4 = st.columns([0.46, 0.24, 0.17, 0.13], vertical_alignment="center")
    query = f1.text_input(
        "Search items",
        key="im_q",
        placeholder="🔍  Search code, name, description, location",
        label_visibility="collapsed",
    )
    category = f2.selectbox("Category", ["All"] + categories, key="im_cat", label_visibility="collapsed")
    show_inactive = f3.toggle("Show inactive", value=True, key="im_show_inactive")
    refresh = f4.button("↻ Refresh", key="im_refresh", type="secondary", help="Reload the table from the database")

    view_key = ((query or "").strip().lower(), category, show_inactive)
    editor_key = f"im_editor_{st.session_state.get('im_nonce', 0)}"
    editor_untouched = not _editor_pending(editor_key)
    needs_load = (
        refresh
        or "im_base_df" not in st.session_state
        or st.session_state.get("im_view_key") != view_key
        # Someone else changed data and this table has no edits in progress
        or (editor_untouched and st.session_state.get("im_data_version") != data_version())
        # Coming back after saving: the old editor is gone, rebuild from the DB
        or (editor_untouched and st.session_state.get("im_base_stale"))
    )
    if needs_load:
        _master_reload()
        base = master_dataframe(parts, query, category, show_inactive)
        st.session_state["im_base_df"] = base
        st.session_state["im_view_key"] = view_key
        st.session_state["im_data_version"] = data_version()
        st.session_state["im_committed_rows"] = _records(base)
        st.session_state["im_committed_sig"] = _master_signature(base)
        editor_key = f"im_editor_{st.session_state['im_nonce']}"

    base_df = st.session_state["im_base_df"]
    edited_df = st.data_editor(
        base_df,
        key=editor_key,
        width="stretch",
        hide_index=True,
        height=520,
        num_rows="add",
        column_order=[c for c in MASTER_EDIT_COLUMNS if c != "id"],
        column_config={
            "part_id": st.column_config.TextColumn("Item Code", width="medium"),
            "name": st.column_config.TextColumn("Name", width="medium"),
            "quantity": st.column_config.NumberColumn(
                "Qty", min_value=0, step=1, format="%d", width="small", default=0,
                help="Stock corrections are recorded in History. Use Inward for received goods.",
            ),
            "unit": st.column_config.TextColumn("Unit", width="small", default="Nos"),
            "location": st.column_config.TextColumn("Location", width="small"),
            "category": st.column_config.SelectboxColumn(
                "Category", options=categories, required=True, default="Others", width="small"
            ),
            "min_level": st.column_config.NumberColumn("Min", min_value=0, step=1, format="%d", width="small", default=0),
            "reorder_qty": st.column_config.NumberColumn("Reorder", min_value=0, step=1, format="%d", width="small", default=0),
            "description": st.column_config.TextColumn("Description", width="large"),
            "active": st.column_config.CheckboxColumn("Active", width="small", default=True),
        },
    )

    status = st.container()
    signature = _master_signature(edited_df)
    if signature == st.session_state.get("im_committed_sig"):
        outdated = st.session_state.get("im_data_version") != data_version()
        status.caption(
            f"{len(base_df)} item(s) shown · Edits save automatically when you leave a cell · "
            "Add a new item with the ＋ at the bottom of the table."
            + (" · Stock has changed elsewhere — press ↻ Refresh to see it." if outdated else "")
        )
        return

    if st.session_state.get("im_failed_sig") == signature:
        status.error(st.session_state.get("im_failed_msg", "Not saved."))
    else:
        try:
            result = save_master_table(
                conn,
                _records(edited_df),
                original_rows=st.session_state.get("im_committed_rows"),
                performed_by=st.session_state.get("user", "manager"),
                performed_role="manager",
            )
        except StockConflictError as exc:
            bump_data_version()
            _master_reload()
            st.session_state["im_notice"] = ("warning", f"⚠️ {exc}")
            st.rerun(scope="fragment")
        except ValueError as exc:
            st.session_state["im_failed_sig"] = signature
            st.session_state["im_failed_msg"] = f"❌ Not saved: {exc}"
            status.error(st.session_state["im_failed_msg"])
        except Exception as exc:
            _master_reload()
            st.session_state["im_notice"] = ("error", f"❌ Save failed: {exc}. Table reloaded from the database.")
            st.rerun(scope="fragment")
        else:
            if result["inserted"]:
                # New rows need their database ids: rebuild the table once.
                bump_data_version()
                _master_reload()
                st.toast(f"✅ {result['inserted']} new item(s) added", icon="📦")
                st.rerun(scope="fragment")
            if result["updated"]:
                bump_data_version()
                # Keep the same table on screen (cursor and scroll stay put);
                # just remember what is now saved.
                st.session_state["im_committed_rows"] = _records(edited_df)
                st.session_state["im_committed_sig"] = signature
                st.session_state["im_data_version"] = data_version()
                st.session_state["im_base_stale"] = True
                st.session_state.pop("im_failed_sig", None)
                msg = f"Saved — {result['updated']} item(s) updated"
                if result.get("adjusted"):
                    msg += f" · {result['adjusted']} stock correction(s) logged"
                st.toast(msg, icon="✅")
                status.caption("✅ All changes saved.")
                return
            if result.get("incomplete"):
                status.info("New row: fill in **Item Code** and **Name** — it saves automatically.")
            else:
                # Edited back to the original value: nothing to save.
                st.session_state["im_committed_sig"] = signature
                st.session_state["im_committed_rows"] = _records(edited_df)
                status.caption("No changes to save.")
                return

    if status.button("Discard unsaved change", key="im_discard", type="secondary"):
        _master_reload()
        st.rerun(scope="fragment")


def _read_uploaded_csv(uploaded):
    """Read a CSV saved from Excel or Google Sheets (handles BOM and Windows encoding)."""
    raw = uploaded.getvalue()
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    import io
    df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    if "item_code" not in df.columns and "part_id" not in df.columns:
        raise ValueError("The file needs an 'item_code' column (use Export to get the right layout).")
    return df.fillna("")


@st.dialog("Add new item", width="large")
def show_add_item_dialog():
    with db_session() as conn:
        _add_item_form(conn)


def _add_item_form(conn):
    categories = sorted(set(MASTER_CATEGORY_OPTIONS) | {normalize_category(p.get("category")) for p in all_parts(conn)})
    with st.form("add_item_form", clear_on_submit=False, border=False):
        c1, c2 = st.columns(2)
        code = c1.text_input("Item code *", placeholder="e.g. BS-R25-890")
        name = c2.text_input("Item name *", placeholder="e.g. Ballscrew R25")
        description = st.text_input("Description / specification", placeholder="Make, size, model…")
        c3, c4, c5 = st.columns(3)
        quantity = c3.number_input("Opening stock", min_value=0, step=1, value=0)
        unit = c4.text_input("Unit", value="Nos")
        category = c5.selectbox("Category", categories, index=categories.index("Others") if "Others" in categories else 0)
        c6, c7, c8 = st.columns(3)
        location = c6.text_input("Location", placeholder="Rack / shelf / bin")
        min_level = c7.number_input("Min stock (alert level)", min_value=0, step=1, value=0)
        reorder_qty = c8.number_input("Reorder qty", min_value=0, step=1, value=0)
        b1, b2 = st.columns(2)
        save = b1.form_submit_button("➕  Add item", type="primary", width="stretch")
        add_more = b2.form_submit_button("Add & add another", width="stretch")

    if save or add_more:
        code, name = code.strip(), name.strip()
        if not code or not name:
            st.error("Item code and item name are required.")
            return
        existing = get_part(conn, code)
        if existing:
            st.error(f"Item code '{code}' already exists ({existing['name']}). Use a different code.")
            return
        try:
            save_master_table(
                conn,
                [{
                    "id": None, "part_id": code, "name": name, "description": description,
                    "unit": unit or "Nos", "quantity": int(quantity), "location": location,
                    "min_level": int(min_level), "reorder_qty": int(reorder_qty),
                    "category": category, "active": True,
                }],
                performed_by=st.session_state.get("user", "manager"),
                performed_role="manager",
            )
        except ValueError as exc:
            st.error(str(exc))
            return
        bump_data_version()
        st.toast(f"Added {name}", icon="📦")
        if add_more:
            st.session_state["im_add_open"] = True
            st.session_state["im_notice"] = ("success", f"✅ Added {code} — {name}.")
            st.rerun()
        st.session_state.pop("im_add_open", None)
        st.session_state["im_notice"] = ("success", f"✅ Added {code} — {name}.")
        st.rerun()


@st.dialog("Edit item", width="large")
def show_edit_item_dialog():
    with db_session() as conn:
        _edit_item_form(conn)


def _edit_item_form(conn):
    parts = all_parts(conn)
    by_code = {p["part_id"]: p for p in parts}
    codes = sorted(by_code, key=lambda c: str(by_code[c].get("name") or "").lower())
    code = st.selectbox(
        "Item", codes, index=None, placeholder="Type to search an item…",
        format_func=lambda c: f"{by_code[c].get('name')}  ·  {c}",
        key="im_edit_pick",
    )
    if not code:
        return
    part = get_part(conn, code)  # fresh from the database
    if part is None:
        st.error("This item no longer exists.")
        return
    categories = sorted(set(MASTER_CATEGORY_OPTIONS) | {normalize_category(p.get("category")) for p in parts})
    cat_now = normalize_category(part["category"])
    with st.form(f"edit_item_form_{part['id']}", border=False):
        c1, c2 = st.columns(2)
        new_code = c1.text_input("Item code *", value=part["part_id"])
        name = c2.text_input("Item name *", value=part["name"] or "")
        description = st.text_input("Description / specification", value=part["description"] or "")
        c3, c4, c5 = st.columns(3)
        quantity = c3.number_input("Stock qty", min_value=0, step=1, value=int(part["quantity"] or 0),
                                   help="Changing this records a stock correction in History.")
        unit = c4.text_input("Unit", value=part["unit"] or "Nos")
        category = c5.selectbox("Category", categories, index=categories.index(cat_now) if cat_now in categories else 0)
        c6, c7, c8 = st.columns(3)
        location = c6.text_input("Location", value=part["location"] or "")
        min_level = c7.number_input("Min stock (alert level)", min_value=0, step=1, value=int(part["min_level"] or 0))
        reorder_qty = c8.number_input("Reorder qty", min_value=0, step=1, value=int(part["reorder_qty"] or 0))
        active = st.checkbox("Active (can be issued)", value=bool(part["active"]))
        saved = st.form_submit_button("💾  Save changes", type="primary", width="stretch")
    if saved:
        original = {k: part[k] for k in ("id", "part_id", "name", "description", "unit", "quantity", "location",
                                         "min_level", "reorder_qty", "category", "active")}
        edited = dict(original, part_id=new_code.strip(), name=name.strip(), description=description,
                      unit=unit or "Nos", quantity=int(quantity), location=location, min_level=int(min_level),
                      reorder_qty=int(reorder_qty), category=category, active=active)
        try:
            result = save_master_table(conn, [edited], original_rows=[original],
                                       performed_by=st.session_state.get("user", "manager"),
                                       performed_role="manager")
        except ValueError as exc:  # includes stock conflicts
            st.error(str(exc))
            return
        if result["updated"]:
            bump_data_version()
            msg = f"✅ Saved {edited['part_id']} — {edited['name']}."
            if result.get("adjusted"):
                msg += " Stock correction recorded in History."
            st.session_state["im_notice"] = ("success", msg)
        st.session_state.pop("im_edit_pick", None)
        st.rerun()


def item_master_page(conn):
    t1, t2, t3 = st.columns([0.5, 0.25, 0.25], vertical_alignment="center")
    with t1:
        _section_title("Item master", "Edit in the table (saves automatically) or use the buttons.")
    if t2.button("➕  Add new item", key="im_add_btn", type="primary", width="stretch") or st.session_state.pop("im_add_open", False):
        show_add_item_dialog()
    if t3.button("✏️  Edit an item", key="im_edit_btn", type="secondary", width="stretch"):
        show_edit_item_dialog()
    master_table_fragment()

    with st.expander("⬇ ⬆  Export / import CSV"):
        import_result = st.session_state.pop("im_import_result", None)
        if import_result is not None:
            result_text = format_import_summary(import_result)
            if import_result["duplicate_rows"] or import_result["invalid_rows"] or import_result.get("near_duplicate_rows"):
                st.warning(f"{result_text} {'; '.join(import_result['messages'][:3])}".strip())
            else:
                st.success(f"✅  {result_text}")

        parts = all_parts(conn)
        col_exp, col_imp = st.columns(2)
        with col_exp:
            st.caption("Download every item (opens in Excel).")
            if parts:
                exp_df = pd.DataFrame(parts).drop(columns=["id", "created_at", "updated_at"], errors="ignore")
                exp_df = exp_df.rename(columns={"part_id": "item_code"})
                ordered = [c for c in ["item_code", "name", "description", "unit", "quantity", "location",
                                        "min_level", "reorder_qty", "category", "active"] if c in exp_df.columns]
                st.download_button(
                    "⬇ Export all items",
                    exp_df[ordered].to_csv(index=False).encode("utf-8-sig"),
                    file_name=f"eqvimech_items_{_today_ist()}.csv",
                    mime="text/csv",
                    key="im_export",
                )
        with col_imp:
            st.caption("Upload a CSV to add or update many items at once.")
            if "im_upload_nonce" not in st.session_state:
                st.session_state["im_upload_nonce"] = 0
            uploaded = st.file_uploader("Import CSV", type=["csv"], key=next_import_upload_key(), label_visibility="collapsed")
            if uploaded is not None and not st.session_state.get("im_import_preview"):
                try:
                    import_df = _read_uploaded_csv(uploaded)
                    records = import_df.to_dict("records")
                    with st.spinner(f"Checking {len(records)} rows…"):
                        preview = analyze_parts_import(conn, records)
                    preview["records"] = records
                    preview["file_name"] = uploaded.name
                    st.session_state["im_import_preview"] = preview
                    safe_rerun()
                except Exception as exc:
                    st.error(f"Could not read the file: {exc}")
        if st.session_state.get("im_import_preview"):
            show_import_review_dialog()

    with st.expander("🏷  Suggest categories for uncategorised items"):
        st.caption("Looks at items in 'Others' and suggests a category from their name. Nothing changes until you apply.")
        if st.button("Find suggestions", key="im_autoclass", type="secondary"):
            st.session_state["im_autoclass_preview"] = auto_classify_parts(conn, apply=False)
        preview = st.session_state.get("im_autoclass_preview")
        if preview:
            changes = [c for c in preview["changes"] if c["current"] != c["suggested"]]
            if not changes:
                st.info("No suggestions — every uncategorised item still looks like 'Others'.")
            else:
                st.dataframe(pd.DataFrame(changes)[["part_id", "name", "current", "suggested"]], width="stretch", hide_index=True)
                c1, c2 = st.columns(2)
                if c1.button(f"Apply {len(changes)} suggestion(s)", key="im_autoclass_apply", type="primary"):
                    result = auto_classify_parts(conn, apply=True)
                    bump_data_version()
                    st.session_state.pop("im_autoclass_preview", None)
                    st.session_state["im_notice"] = ("success", f"✅ {result['updated']} item categories updated.")
                    safe_rerun()
                if c2.button("Dismiss", key="im_autoclass_dismiss", type="secondary"):
                    st.session_state.pop("im_autoclass_preview", None)
                    safe_rerun()


HISTORY_COLUMN_CONFIG = {
    "created_at": st.column_config.TextColumn("Date (IST)"),
    "returned_at": st.column_config.TextColumn("Returned (IST)"),
    "tx_type": st.column_config.TextColumn("Type"),
    "return_status": st.column_config.TextColumn("Return"),
    "part_name": st.column_config.TextColumn("Item"),
    "part_id": st.column_config.TextColumn("Code"),
    "qty": st.column_config.NumberColumn("Qty", format="%d"),
    "unit": st.column_config.TextColumn("Unit"),
    "performed_by": st.column_config.TextColumn("By"),
    "machine_sn": st.column_config.TextColumn("Machine S/N"),
    "purpose": st.column_config.TextColumn("Purpose"),
    "prev_stock": st.column_config.NumberColumn("Before", format="%d"),
    "balance_stock": st.column_config.NumberColumn("After", format="%d"),
    "note": st.column_config.TextColumn("Note"),
    "issued_qty": st.column_config.NumberColumn("Issued qty", format="%d"),
}
TX_TYPE_LABELS = {"issue": "Issue", "deposit": "Inward", "return": "Return", "adjust": "Stock correction"}


def dashboard_page(conn):
    parts_all = all_parts(conn)
    categories = sorted({normalize_category(p.get("category")) for p in parts_all})
    selected_category = st.selectbox("Category", ["All"] + categories, key="dash_category")
    data = _cached_dashboard(conn, data_version(), selected_category, _today_ist())
    metrics = data["metrics"]

    low_cls = "mc-danger" if metrics["low_stock_items"] > 0 else ""
    zero_cls = "mc-danger" if metrics["out_of_stock_items"] > 0 else ""
    st.markdown(
        f"""
        <div class="metrics-grid">
            <div class="metric-card mc-accent"><div class="metric-icon">📦</div>
                <div class="metric-label">Active Items</div><div class="metric-value">{metrics['total_items']}</div></div>
            <div class="metric-card mc-accent"><div class="metric-icon">🏷️</div>
                <div class="metric-label">Total Units in Store</div><div class="metric-value m-accent">{metrics['total_stock_units']}</div></div>
            <div class="metric-card {low_cls}"><div class="metric-icon">⚠️</div>
                <div class="metric-label">Low Stock Items</div>
                <div class="metric-value {'m-danger' if metrics['low_stock_items'] > 0 else ''}">{metrics['low_stock_items']}</div></div>
            <div class="metric-card {zero_cls}"><div class="metric-icon">🚫</div>
                <div class="metric-label">Out of Stock</div>
                <div class="metric-value {'m-danger' if metrics['out_of_stock_items'] > 0 else ''}">{metrics['out_of_stock_items']}</div></div>
            <div class="metric-card mc-issued"><div class="metric-icon">⬆️</div>
                <div class="metric-label">Issued Today</div><div class="metric-value m-issued">{metrics['issues_today']}</div></div>
            <div class="metric-card mc-deposit"><div class="metric-icon">📥</div>
                <div class="metric-label">Inwarded Today</div><div class="metric-value m-deposit">{metrics['deposits_today']}</div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    left, right = st.columns(2)
    with left:
        st.markdown('<div class="section-label">Most consumed items</div>', unsafe_allow_html=True)
        top_df = pd.DataFrame(data["top"])
        if top_df.empty:
            st.info("No issue history yet.")
        else:
            st.dataframe(top_df, width="stretch", hide_index=True, column_config=HISTORY_COLUMN_CONFIG)
    with right:
        st.markdown('<div class="section-label">Top machine usage</div>', unsafe_allow_html=True)
        mdf = pd.DataFrame(data["machines"]).rename(columns={"issued_lines": "issued_qty"})
        if mdf.empty:
            st.info("No machine-wise issue history yet.")
        else:
            st.dataframe(mdf, width="stretch", hide_index=True, column_config=HISTORY_COLUMN_CONFIG)

    st.markdown('<div class="section-label">Recent activity</div>', unsafe_allow_html=True)
    rdf = to_ist(pd.DataFrame(data["recent"]))
    if rdf.empty:
        st.info("No stock movement yet.")
    else:
        rdf["tx_type"] = rdf["tx_type"].map(TX_TYPE_LABELS).fillna(rdf["tx_type"])
        cols = [c for c in ["created_at", "tx_type", "part_name", "qty", "unit", "performed_by", "machine_sn", "balance_stock"] if c in rdf.columns]
        st.dataframe(rdf[cols], width="stretch", hide_index=True, column_config=HISTORY_COLUMN_CONFIG)


@st.fragment
def history_fragment():
    with db_session() as conn:
        _history(conn)


def _history(conn):
    fc1, fc2, fc3 = st.columns([0.25, 0.25, 0.5])
    tx_type = fc1.selectbox(
        "Type",
        ["all", "issue", "deposit", "return", "adjust"],
        format_func=lambda x: {"all": "All types", **TX_TYPE_LABELS}.get(x, x),
        key="hist_type",
    )
    period_options = {"Today": 0, "Last 7 days": 6, "Last 30 days": 29, "Last 90 days": 89, "All time": None}
    period = fc2.selectbox("Period", list(period_options), index=2, key="hist_period")
    with fc3:
        search = (live_search_input("Search", "Item, serial no, user, purpose…", "hist_search") or "").strip()

    rows = _cached_history(conn, data_version(), tx_type, search, period_options[period], _today_ist())
    if not rows:
        st.info("No matching records.")
        return
    df = pd.DataFrame(rows)

    def _return_status(row):
        if row.get("tx_type") == "return":
            return "Returned"
        if row.get("tx_type") == "issue" and row.get("returnable"):
            return "Returned" if row.get("returned_at") else "Pending"
        return ""

    df["return_status"] = df.apply(_return_status, axis=1)
    df = to_ist(df)
    df["tx_type"] = df["tx_type"].map(TX_TYPE_LABELS).fillna(df["tx_type"])
    cols = [c for c in ["created_at", "tx_type", "part_name", "qty", "unit", "performed_by", "machine_sn",
                         "purpose", "return_status", "prev_stock", "balance_stock", "note"] if c in df.columns]
    st.caption(f"{len(df)} record(s)" + (" — showing the latest 1000" if len(df) >= 1000 else ""))
    st.dataframe(df[cols], width="stretch", hide_index=True, height=480, column_config=HISTORY_COLUMN_CONFIG)
    st.download_button(
        "⬇ Export to Excel (CSV)",
        df[cols].to_csv(index=False).encode("utf-8-sig"),
        file_name=f"inventory_history_{_today_ist()}.csv",
        mime="text/csv",
        key="hist_export",
    )


def history_page(conn):
    _section_title("Stock history", "Every issue, inward, return and stock correction.")
    if st.session_state.get("role") == "manager":
        pending, _ = _cached_returnables(conn, data_version(), "")
        if pending:
            st.info(f"↩ {len(pending)} returnable item(s) still out — see the Returnables tab.")
    history_fragment()


def alerts_page(conn):
    alerts = low_stock_from_parts(all_parts(conn))
    if not alerts:
        st.success("✅  All items are above their minimum stock level.")
        return

    out = [p for p in alerts if int(p.get("quantity") or 0) <= 0]
    st.warning(f"⚠️  {len(alerts)} item(s) at or below minimum stock — {len(out)} completely out of stock.")
    df = pd.DataFrame(
        [
            {
                "Item": p.get("name"),
                "Code": p.get("part_id"),
                "In stock": int(p.get("quantity") or 0),
                "Min": int(p.get("min_level") or 0),
                "Order qty": int(p.get("reorder_qty") or 0),
                "Unit": p.get("unit"),
                "Category": normalize_category(p.get("category")),
                "Location": p.get("location") or "",
            }
            for p in alerts
        ]
    )
    st.dataframe(df, width="stretch", hide_index=True, height=min(600, 38 + 35 * len(df)))
    st.download_button(
        "⬇ Download reorder list",
        df.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"reorder_list_{_today_ist()}.csv",
        mime="text/csv",
        key="alerts_export",
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

    try:
        _connection_manager()
        _run_db_init()
    except Exception as e:
        st.error(f"**Database connection failed:** {e}")
        st.caption("Check DATABASE_URL in the app secrets and that the Supabase project is not paused.")
        if st.button("Retry", type="primary"):
            _connection_manager.clear()
            _run_db_init.clear()
            safe_rerun()
        st.stop()
    with db_session() as conn:
        render_app(conn)


def render_app(conn):
    sidebar_identity(conn)

    role = st.session_state.get("role", "user")
    alerts = low_stock_from_parts(all_parts(conn))

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
                    <div class="brand-subtitle">Store issue, inward &amp; stock control</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    if alerts and role == "manager":
        out_count = sum(1 for p in alerts if int(p.get("quantity") or 0) <= 0)
        st.markdown(
            f'<div class="low-stock-banner">'
            f'⚠️&nbsp; {len(alerts)} item(s) at or below minimum stock'
            f'{f" · {out_count} out of stock" if out_count else ""} — see Alerts'
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
        nav_options = ["⬆ Pick", "📦 Items", "📋 History", "🔔 Alerts"]
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
