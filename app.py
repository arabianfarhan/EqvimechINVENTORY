import pandas as pd
import streamlit as st

from db import (
    deposit_stock,
    get_conn,
    get_dashboard_metrics,
    get_machine_usage,
    get_part,
    get_parts,
    get_top_consumed_items,
    init_db,
    list_transactions,
    low_stock_alerts,
    pick_material,
    save_part,
    seed_sample_data,
)

st.set_page_config(
    page_title="Eqvimech Inventory",
    page_icon="🏭",
    layout="centered",
    initial_sidebar_state="auto",
)


def safe_rerun():
    getattr(st, "rerun", getattr(st, "experimental_rerun", lambda: None))()


def inject_theme():
    st.markdown(
        """
        <style>
        /* ── Global ── */
        html, body, [class*="css"] { font-family: 'Inter', 'Segoe UI', sans-serif; }

        /* ── Main background ── */
        .stApp { background: #0d1924; }
        .block-container {
            padding-top: 0.8rem !important;
            padding-bottom: 5rem !important;
            max-width: 860px !important;
        }

        /* ── Sidebar ── */
        section[data-testid="stSidebar"] {
            background: #0d1924 !important;
            border-right: 1px solid #1e2e3d !important;
        }
        section[data-testid="stSidebar"] * { color: #c8d8e4 !important; }
        section[data-testid="stSidebar"] .stTextInput input,
        section[data-testid="stSidebar"] div[data-baseweb="select"] {
            background: #162332 !important;
            border-color: #2a3f52 !important;
            color: #e2edf5 !important;
        }

        /* ── Headings ── */
        h1 { color: #e2edf5 !important; font-size: 1.5rem !important; font-weight: 800 !important; }
        h2, h3 { color: #c8d8e4 !important; font-size: 1.1rem !important; font-weight: 700 !important; }
        p, li { color: #a0bece !important; }

        /* ── Inputs ── */
        .stTextInput input, .stTextArea textarea, .stNumberInput input {
            background: #162332 !important;
            border: 1px solid #2a3f52 !important;
            border-radius: 10px !important;
            color: #e2edf5 !important;
        }
        div[data-baseweb="select"] > div {
            background: #162332 !important;
            border-color: #2a3f52 !important;
            border-radius: 10px !important;
        }
        div[data-baseweb="select"] * { color: #e2edf5 !important; }
        div[data-baseweb="menu"] { background: #162332 !important; border-color: #2a3f52 !important; }
        div[data-baseweb="menu"] li:hover { background: #1e3248 !important; }
        .stTextInput label, .stTextArea label, .stNumberInput label,
        .stSelectbox label, .stCheckbox label {
            color: #6b8fa4 !important;
            font-size: 0.8rem !important;
            font-weight: 700 !important;
            text-transform: uppercase !important;
            letter-spacing: 0.06em !important;
        }

        /* ── Buttons ── */
        .stButton > button {
            background: #00c4b4 !important;
            color: #0d1924 !important;
            border: none !important;
            border-radius: 12px !important;
            font-weight: 700 !important;
            font-size: 0.95rem !important;
            padding: 0.55rem 1.2rem !important;
            min-height: 2.7rem !important;
            width: 100% !important;
            transition: all 0.15s ease !important;
        }
        .stButton > button:hover { background: #00e0ce !important; transform: translateY(-1px) !important; }
        .stButton > button:active { transform: translateY(0) !important; }
        .stDownloadButton > button {
            background: #162332 !important;
            color: #00c4b4 !important;
            border: 1px solid #00c4b4 !important;
            border-radius: 12px !important;
            font-weight: 600 !important;
        }

        /* ── Tabs ── */
        div[data-baseweb="tab-list"] {
            background: #162332 !important;
            border-radius: 12px !important;
            padding: 4px !important;
            gap: 2px !important;
            border: none !important;
            overflow-x: auto !important;
        }
        button[data-baseweb="tab"] {
            background: transparent !important;
            color: #7a99ae !important;
            border-radius: 9px !important;
            font-weight: 600 !important;
            font-size: 0.8rem !important;
            padding: 0.35rem 0.65rem !important;
            border: none !important;
            white-space: nowrap !important;
        }
        button[data-baseweb="tab"][aria-selected="true"] { background: #00c4b4 !important; color: #0d1924 !important; }
        div[data-baseweb="tab-panel"] { padding-top: 1rem !important; }
        div[data-baseweb="tab-highlight"] { display: none !important; }

        /* ── Item card ── */
        .item-card {
            background: #162332;
            border: 1px solid #1e3248;
            border-radius: 16px;
            padding: 1rem;
            margin-bottom: 0.9rem;
        }
        .item-name { color: #e2edf5; font-size: 1.05rem; font-weight: 700; margin-bottom: 0.2rem; }
        .item-desc { color: #5d8097; font-size: 0.86rem; margin-bottom: 0.75rem; }
        .pill-row { display: flex; flex-wrap: wrap; gap: 0.4rem; margin-bottom: 0.5rem; }
        .pill { display: inline-block; padding: 0.22rem 0.6rem; border-radius: 999px; font-size: 0.76rem; font-weight: 700; }
        .p-neutral { background: #1e3248; color: #7a99ae; }
        .p-ok      { background: #0d2d21; color: #00c48a; }
        .p-low     { background: #2d1b14; color: #ff6b35; }
        .p-zero    { background: #2d1422; color: #ff4757; }

        /* ── Metric card ── */
        .metric-card {
            background: #162332;
            border: 1px solid #1e3248;
            border-radius: 14px;
            padding: 0.85rem 1rem;
            margin-bottom: 0.6rem;
        }
        .metric-label { color: #5d8097; font-size: 0.76rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.07em; }
        .metric-value { color: #e2edf5; font-size: 1.55rem; font-weight: 800; margin-top: 0.1rem; }
        .m-accent { color: #00c4b4 !important; }
        .m-warn   { color: #ff6b35 !important; }
        .m-danger { color: #ff4757 !important; }

        /* ── Section labels ── */
        .section-label {
            color: #3d6070;
            font-size: 0.76rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            margin: 1.2rem 0 0.5rem 0;
        }

        /* ── Divider ── */
        hr { border-color: #1e3248 !important; margin: 0.8rem 0 !important; }

        /* ── Alerts ── */
        div[data-testid="stAlert"] { border-radius: 12px !important; }

        /* ── Dataframe ── */
        .stDataFrame { border-radius: 12px !important; overflow: hidden; }

        /* ── Form container ── */
        div[data-testid="stForm"] {
            background: #162332 !important;
            border: 1px solid #1e3248 !important;
            border-radius: 16px !important;
            padding: 1rem !important;
        }

        /* ── Status messages ── */
        div.stSuccess > div { background: #0d2d21 !important; color: #00c48a !important; border-radius: 10px !important; }
        div.stError > div   { background: #2d1422 !important; color: #ff4757 !important; border-radius: 10px !important; }
        div.stWarning > div { background: #2d1b14 !important; color: #ff6b35 !important; border-radius: 10px !important; }
        div.stInfo > div    { background: #0d2238 !important; color: #5db9e0 !important; border-radius: 10px !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )


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


def sidebar_identity():
    with st.sidebar:
        st.markdown("### 🏭 Eqvimech")
        st.markdown("---")
        username = st.text_input("Your name", value=st.session_state.get("user", "operator"))
        role = st.selectbox(
            "Role",
            ("user", "manager"),
            index=0 if st.session_state.get("role", "user") == "user" else 1,
        )
        st.session_state["user"] = username.strip() or "operator"
        st.session_state["role"] = role
        st.markdown("---")
        st.caption(f"**{st.session_state['user']}** · {role.capitalize()}")


def items_page(conn):
    search = st.text_input(
        "",
        placeholder="🔍  Search by name, code, description, location…",
        key="items_search",
    )
    parts = get_parts(conn, query=search.strip())

    if not parts:
        st.info("No items matched your search.")
        return

    for part in parts:
        st.markdown(
            f"""
            <div class="item-card">
                <div class="item-name">{part['name']}</div>
                <div class="item-desc">{part['description']}</div>
                <div class="pill-row">
                    <span class="pill p-neutral">{part['part_id']}</span>
                    <span class="pill p-neutral">{part['location']}</span>
                    <span class="pill p-neutral">Min {part['min_level']} &nbsp;&middot;&nbsp; Reorder {part['reorder_qty']}</span>
                    {stock_pill(part['quantity'], part['min_level'])}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def pick_material_page(conn):
    available = get_parts(conn)
    if not available:
        st.info("No active items available for issue.")
        return

    part_id = st.session_state.get("pick_part")
    part_ids = [p["part_id"] for p in available]
    default_index = part_ids.index(part_id) if part_id and part_id in part_ids else 0

    selected_id = st.selectbox(
        "Select item",
        part_ids,
        index=default_index,
        format_func=lambda v: f"{v}  ·  {get_part(conn, v)['name']}",
        key="pick_select",
    )
    part = get_part(conn, selected_id)

    st.markdown(
        f"""
        <div class="item-card">
            <div class="item-name">{part['name']}</div>
            <div class="item-desc">{part['description']}</div>
            <div class="pill-row">
                <span class="pill p-neutral">Location: {part['location']}</span>
                {stock_pill(part['quantity'], part['min_level'])}
                <span class="pill p-neutral">Min {part['min_level']} &nbsp;&middot;&nbsp; Reorder {part['reorder_qty']}</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if part["quantity"] <= 0:
        st.error("This item is out of stock and cannot be issued.")
        return

    st.markdown("---")

    qty = st.number_input(
        "Quantity to pick",
        min_value=1,
        max_value=int(part["quantity"]),
        value=1,
        step=1,
        key="pick_qty",
    )

    st.markdown(
        f'<p style="color:#6b8fa4;font-size:0.8rem;font-weight:700;text-transform:uppercase;'
        f'letter-spacing:.06em;margin:0 0 .3rem 0">Machine serial numbers '
        f'({int(qty)} required — one per line or comma-separated)</p>',
        unsafe_allow_html=True,
    )
    serials_raw = st.text_area(
        "serial_numbers_input",
        placeholder="e.g.\nVMC-120\nVMC-121",
        height=110,
        key="pick_serials",
        label_visibility="collapsed",
    )
    purpose = st.text_input("Purpose / Usage", placeholder="e.g. UTM-200 Assembly", key="pick_purpose")
    note = st.text_input("Note (optional)", placeholder="e.g. Urgent – project deadline", key="pick_note")

    if st.button("✅  Confirm Material Issue", key="confirm_pick"):
        serials = [s.strip() for s in serials_raw.replace("\n", ",").split(",") if s.strip()]
        if len(serials) != int(qty):
            st.error(
                f"You entered {len(serials)} serial number(s) but picked {int(qty)} unit(s). "
                "One serial number per unit is required."
            )
        elif not purpose.strip():
            st.error("Purpose / Usage is required.")
        else:
            try:
                new_balance = pick_material(
                    conn,
                    part["part_id"],
                    serials,
                    st.session_state["user"],
                    st.session_state["role"],
                    purpose.strip(),
                    note.strip(),
                )
                st.success(
                    f"✅  Issued {int(qty)} × {part['name']}  |  "
                    f"New balance: **{new_balance} {part['unit']}**"
                )
                st.session_state.pop("pick_part", None)
                safe_rerun()
            except Exception as exc:
                st.error(str(exc))


def deposit_stock_page(conn):
    parts = get_parts(conn, active_only=False)
    if not parts:
        st.info("Add items via Item Master before depositing stock.")
        return

    selected_id = st.selectbox(
        "Select item",
        [p["part_id"] for p in parts],
        format_func=lambda v: f"{v}  ·  {get_part(conn, v)['name']}",
        key="deposit_select",
    )
    part = get_part(conn, selected_id)

    st.markdown(
        f"""
        <div class="item-card">
            <div class="item-name">{part['name']}</div>
            <div class="item-desc">{part['description']}</div>
            <div class="pill-row">
                {stock_pill(part['quantity'], part['min_level'])}
                <span class="pill p-neutral">Location: {part['location']}</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("---")

    with st.form("deposit_form"):
        qty = st.number_input("Quantity to deposit", min_value=1, value=1, step=1)
        note = st.text_input("Note / GRN reference", placeholder="e.g. GRN-001 / Vendor invoice")
        if st.form_submit_button("📥  Confirm Deposit"):
            try:
                new_balance = deposit_stock(
                    conn,
                    selected_id,
                    int(qty),
                    st.session_state["user"],
                    st.session_state["role"],
                    note.strip(),
                )
                st.success(
                    f"✅  Deposited {int(qty)} × {part['name']}  |  "
                    f"New balance: **{new_balance} {part['unit']}**"
                )
                safe_rerun()
            except Exception as exc:
                st.error(str(exc))


def item_master_page(conn):
    parts = get_parts(conn, active_only=False)
    options = ["— New item —"] + [p["part_id"] for p in parts]
    selected = st.selectbox("Select item to edit", options, key="im_select")
    current = None if selected == "— New item —" else get_part(conn, selected)

    st.markdown("---")

    with st.form("item_master_form"):
        col1, col2 = st.columns(2)
        with col1:
            part_id = st.text_input(
                "Item ID *",
                value="" if current is None else current["part_id"],
                disabled=current is not None,
            )
            name = st.text_input("Item name *", value="" if current is None else current["name"])
            unit = st.text_input("Unit", value="Nos" if current is None else current["unit"])
            location = st.text_input("Location", value="" if current is None else current["location"])
        with col2:
            quantity = st.number_input(
                "Opening stock",
                min_value=0,
                value=0 if current is None else int(current["quantity"]),
                step=1,
            )
            min_level = st.number_input(
                "Min stock level",
                min_value=0,
                value=0 if current is None else int(current["min_level"]),
                step=1,
            )
            reorder_qty = st.number_input(
                "Reorder quantity",
                min_value=0,
                value=0 if current is None else int(current["reorder_qty"]),
                step=1,
            )
            active = st.checkbox("Active", value=True if current is None else bool(current["active"]))
        description = st.text_area("Description", value="" if current is None else current["description"])

        if st.form_submit_button("💾  Save Item"):
            pid = current["part_id"] if current else part_id.strip()
            if not pid:
                st.error("Item ID is required.")
            elif not name.strip():
                st.error("Item name is required.")
            else:
                save_part(
                    conn,
                    {
                        "part_id": pid,
                        "name": name.strip(),
                        "description": description.strip(),
                        "unit": unit.strip() or "Nos",
                        "quantity": int(quantity),
                        "location": location.strip(),
                        "min_level": int(min_level),
                        "reorder_qty": int(reorder_qty),
                        "active": 1 if active else 0,
                    },
                )
                st.success("Item saved.")
                safe_rerun()


def dashboard_page(conn):
    metrics = get_dashboard_metrics(conn)
    top_items = get_top_consumed_items(conn)
    machine_usage = get_machine_usage(conn)
    recent_rows = list_transactions(conn, limit=8)

    c1, c2, c3 = st.columns(3)
    with c1:
        render_metric("Active items", metrics["total_items"])
    with c2:
        render_metric("Total units in store", metrics["total_stock_units"], "m-accent")
    with c3:
        cls = "m-danger" if metrics["low_stock_items"] > 0 else ""
        render_metric("Low stock items", metrics["low_stock_items"], cls)

    c4, c5, c6 = st.columns(3)
    with c4:
        cls = "m-danger" if metrics["out_of_stock_items"] > 0 else ""
        render_metric("Out of stock", metrics["out_of_stock_items"], cls)
    with c5:
        render_metric("Issued today", metrics["issues_today"], "m-accent")
    with c6:
        render_metric("Deposited today", metrics["deposits_today"])

    st.markdown('<div class="section-label">Most consumed items</div>', unsafe_allow_html=True)
    top_df = pd.DataFrame(top_items)
    if top_df.empty:
        st.info("No issue history yet.")
    else:
        st.dataframe(top_df, use_container_width=True, hide_index=True)

    st.markdown('<div class="section-label">Top machine usage</div>', unsafe_allow_html=True)
    mdf = pd.DataFrame(machine_usage)
    if mdf.empty:
        st.info("No machine-wise issue history yet.")
    else:
        st.dataframe(mdf, use_container_width=True, hide_index=True)

    st.markdown('<div class="section-label">Recent activity</div>', unsafe_allow_html=True)
    rdf = pd.DataFrame(recent_rows)
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
    tx_type = fc1.selectbox("Type", ["all", "issue", "deposit"], key="hist_type")
    search = fc2.text_input("Search", placeholder="Item, serial no, user…", key="hist_search")

    role = st.session_state.get("role", "user")
    performed_by = None if role == "manager" else st.session_state["user"]

    rows = list_transactions(conn, tx_type=tx_type, search=search.strip(), performed_by=performed_by)
    df = pd.DataFrame(rows)

    if df.empty:
        st.info("No matching records.")
        return

    display_cols = [
        c for c in ["created_at", "tx_type", "part_name", "qty", "unit",
                     "performed_by", "machine_sn", "purpose", "prev_stock", "balance_stock", "note"]
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


def main():
    inject_theme()

    conn = get_conn()
    init_db(conn)
    seed_sample_data(conn)
    sidebar_identity()

    role = st.session_state.get("role", "user")
    alerts = low_stock_alerts(conn)

    # header row
    header_col, badge_col = st.columns([4, 1])
    with header_col:
        st.markdown("## 🏭 Eqvimech Inventory")
    with badge_col:
        if alerts:
            st.markdown(
                f'<div style="padding-top:0.9rem">'
                f'<span class="pill p-low">⚠ {len(alerts)} low</span></div>',
                unsafe_allow_html=True,
            )

    # tab navigation
    if role == "manager":
        tab_labels = ["📦 Items", "⬆ Pick", "📥 Deposit", "🗂 Master", "📊 Dashboard", "📋 History", "🔔 Alerts"]
        tabs = st.tabs(tab_labels)
        tab_items, tab_pick, tab_deposit, tab_im, tab_dash, tab_hist, tab_alert = tabs

        with tab_items:
            items_page(conn)
        with tab_pick:
            pick_material_page(conn)
        with tab_deposit:
            deposit_stock_page(conn)
        with tab_im:
            item_master_page(conn)
        with tab_dash:
            dashboard_page(conn)
        with tab_hist:
            history_page(conn)
        with tab_alert:
            alerts_page(conn)
    else:
        tab_labels = ["📦 Items", "⬆ Pick", "📋 History", "🔔 Alerts"]
        tabs = st.tabs(tab_labels)
        tab_items, tab_pick, tab_hist, tab_alert = tabs

        with tab_items:
            items_page(conn)
        with tab_pick:
            pick_material_page(conn)
        with tab_hist:
            history_page(conn)
        with tab_alert:
            alerts_page(conn)


if __name__ == "__main__":
    main()
