import pandas as pd
import streamlit as st

from db import (
    delete_part,
    deposit_stock,
    get_conn,
    get_dashboard_metrics,
    get_machine_usage,
    get_part,
    get_parts,
    get_top_consumed_items,
    import_parts_from_csv,
    init_db,
    list_transactions,
    low_stock_alerts,
    pick_material,
    rows_to_dicts,
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
        /* ── Layout ── */
        .block-container {
            padding-top: 1rem !important;
            padding-bottom: 4rem !important;
            max-width: 880px !important;
        }

        /* ── Sidebar ── */
        section[data-testid="stSidebar"] {
            border-right: 1px solid #e2e8f0 !important;
        }

        /* ── Tabs ── */
        div[data-baseweb="tab-list"] {
            background: #e2e8f0 !important;
            border-radius: 12px !important;
            padding: 4px !important;
            gap: 2px !important;
            border: none !important;
            overflow-x: auto !important;
        }
        button[data-baseweb="tab"] {
            background: transparent !important;
            color: #64748b !important;
            border-radius: 9px !important;
            font-weight: 600 !important;
            font-size: 0.82rem !important;
            padding: 0.38rem 0.75rem !important;
            border: none !important;
            white-space: nowrap !important;
        }
        button[data-baseweb="tab"][aria-selected="true"] {
            background: #0d9488 !important;
            color: #ffffff !important;
        }
        div[data-baseweb="tab-panel"] { padding-top: 1rem !important; }
        div[data-baseweb="tab-highlight"] { display: none !important; }

        /* ── Inputs ── */
        .stTextInput input, .stTextArea textarea, .stNumberInput input {
            border-radius: 8px !important;
        }
        .stTextInput label, .stTextArea label, .stNumberInput label,
        .stSelectbox label, .stCheckbox label, .stRadio label {
            font-size: 0.78rem !important;
            font-weight: 700 !important;
            text-transform: uppercase !important;
            letter-spacing: 0.06em !important;
        }

        /* ── Primary button ── */
        .stButton > button {
            background: #0d9488 !important;
            color: #ffffff !important;
            border: none !important;
            border-radius: 10px !important;
            font-weight: 700 !important;
            font-size: 0.93rem !important;
            min-height: 2.75rem !important;
            width: 100% !important;
            transition: background 0.15s, box-shadow 0.15s !important;
            box-shadow: 0 1px 3px rgba(13,148,136,0.2) !important;
        }
        .stButton > button:hover {
            background: #0f766e !important;
            box-shadow: 0 4px 12px rgba(13,148,136,0.28) !important;
        }
        .stFormSubmitButton > button {
            background: #0d9488 !important;
            color: #ffffff !important;
            border: none !important;
            border-radius: 10px !important;
            font-weight: 700 !important;
            min-height: 2.75rem !important;
        }
        .stFormSubmitButton > button:hover { background: #0f766e !important; }
        .stDownloadButton > button {
            background: transparent !important;
            color: #0d9488 !important;
            border: 1.5px solid #0d9488 !important;
            border-radius: 10px !important;
            font-weight: 600 !important;
        }

        /* ── Item card ── */
        .item-card {
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 14px;
            padding: 1rem 1.1rem;
            margin-bottom: 0.8rem;
            box-shadow: 0 1px 3px rgba(15,23,42,0.06);
        }
        .item-name { color: #0f172a; font-size: 1rem; font-weight: 700; margin-bottom: 0.15rem; }
        .item-desc { color: #64748b; font-size: 0.85rem; margin-bottom: 0.65rem; line-height: 1.5; }
        .pill-row  { display: flex; flex-wrap: wrap; gap: 0.35rem; }
        .pill      { display: inline-block; padding: 0.18rem 0.55rem; border-radius: 999px; font-size: 0.73rem; font-weight: 600; }
        .p-neutral { background: #f1f5f9; color: #475569; }
        .p-ok      { background: #dcfce7; color: #15803d; }
        .p-low     { background: #ffedd5; color: #c2410c; }
        .p-zero    { background: #fee2e2; color: #b91c1c; }

        /* ── Metric card ── */
        .metric-card {
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 12px;
            padding: 0.9rem 1rem;
            margin-bottom: 0.6rem;
            box-shadow: 0 1px 3px rgba(15,23,42,0.05);
        }
        .metric-label { color: #94a3b8; font-size: 0.72rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; }
        .metric-value { color: #0f172a; font-size: 1.6rem; font-weight: 800; margin-top: 0.1rem; }
        .m-accent { color: #0d9488 !important; }
        .m-warn   { color: #ea580c !important; }
        .m-danger { color: #dc2626 !important; }

        /* ── Section label ── */
        .section-label {
            color: #94a3b8;
            font-size: 0.72rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            margin: 1.2rem 0 0.5rem 0;
        }

        /* ── Divider ── */
        hr { margin: 0.9rem 0 !important; }

        /* ── Form container ── */
        div[data-testid="stForm"] {
            border-radius: 14px !important;
            padding: 1rem !important;
        }

        /* ── Checkbox ── */
        .stCheckbox [data-baseweb="checkbox"] > div { border-color: #cbd5e1 !important; }

        /* ── Status messages ── */
        div.stSuccess > div { background: #f0fdf4 !important; color: #15803d !important; border: 1px solid #bbf7d0 !important; border-radius: 10px !important; }
        div.stError > div   { background: #fef2f2 !important; color: #dc2626 !important; border: 1px solid #fecaca !important; border-radius: 10px !important; }
        div.stWarning > div { background: #fffbeb !important; color: #d97706 !important; border: 1px solid #fde68a !important; border-radius: 10px !important; }
        div.stInfo > div    { background: #eff6ff !important; color: #2563eb !important; border: 1px solid #bfdbfe !important; border-radius: 10px !important; }

        /* ── Mobile tweaks ── */
        @media (max-width: 640px) {
            .block-container { padding-left: 0.75rem !important; padding-right: 0.75rem !important; }
            .item-card { padding: 0.85rem 0.9rem; }
            h1 { font-size: 1.25rem !important; }
            .metric-value { font-size: 1.35rem !important; }
        }
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
        "Search items",
        placeholder="🔍  Name, item ID, description, location…",
        key="items_search",
        label_visibility="collapsed",
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
    # ── Success state: shown after a confirmed issue to prevent double-press ──
    done = st.session_state.get("pick_done")
    if done:
        st.balloons()
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
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("← Issue another item", key="pick_another"):
            st.session_state.pop("pick_done", None)
            st.session_state.pop("pick_part", None)
            safe_rerun()
        return

    available = get_parts(conn)
    if not available:
        st.info("No active items available for issue.")
        return

    part_id = st.session_state.get("pick_part")
    part_ids = [p["part_id"] for p in available]
    options = [None] + part_ids
    default_index = (part_ids.index(part_id) + 1) if part_id and part_id in part_ids else 0

    selected_id = st.selectbox(
        "Select item",
        options,
        index=default_index,
        format_func=lambda v: "— select an item —" if v is None else (
            lambda p: f"{p['name']}" + (f"  —  {p['description']}" if p['description'] else "")
        )(get_part(conn, v)),
        key="pick_select",
    )

    if selected_id is None:
        return

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
        f'<p style="color:#64748b;font-size:0.78rem;font-weight:700;text-transform:uppercase;'
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
                st.session_state["pick_done"] = {
                    "part": part["name"],
                    "qty": int(qty),
                    "balance": new_balance,
                    "unit": part["unit"],
                }
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
        format_func=lambda v: (
            lambda p: f"{p['name']}" + (f"  —  {p['description']}" if p['description'] else "")
        )(get_part(conn, v)),
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

    # ── Delete ────────────────────────────────────────────────────────────
    if current is not None:
        st.markdown("---")
        st.markdown("**Delete item**")
        st.caption("Soft-deletes the item; all history is preserved.")
        if st.button("🗑️  Delete this item", key="im_delete", type="secondary"):
            st.session_state["im_confirm_delete"] = True
        if st.session_state.get("im_confirm_delete"):
            st.warning(f"Are you sure you want to delete **{current['name']}**? This cannot be undone.")
            c1, c2 = st.columns(2)
            if c1.button("Yes, delete", key="im_delete_yes", type="primary"):
                delete_part(conn, current["part_id"])
                st.success("Item deleted.")
                st.session_state.pop("im_confirm_delete", None)
                st.session_state.pop("im_select", None)
                safe_rerun()
            if c2.button("Cancel", key="im_delete_no"):
                st.session_state.pop("im_confirm_delete", None)
                safe_rerun()

    # ── CSV Export / Import ───────────────────────────────────────────────
    st.markdown("---")
    st.markdown("**Export / Import items (CSV)**")
    col_exp, col_imp = st.columns(2)

    with col_exp:
        all_parts = get_parts(conn, active_only=False)
        if all_parts:
            exp_df = pd.DataFrame(rows_to_dicts(all_parts)).drop(
                columns=["created_at", "updated_at"], errors="ignore"
            )
            st.download_button(
                "⬇ Export all items",
                exp_df.to_csv(index=False).encode("utf-8"),
                file_name="items_export.csv",
                mime="text/csv",
                key="im_export",
            )
        else:
            st.info("No items to export yet.")

    with col_imp:
        uploaded = st.file_uploader("⬆ Import CSV", type=["csv"], key="im_upload")
        if uploaded is not None:
            try:
                import_df = pd.read_csv(uploaded, dtype=str).fillna("")
                records = import_df.to_dict("records")
                inserted, updated, errors = import_parts_from_csv(conn, records)
                if errors:
                    st.warning(
                        f"Imported with {len(errors)} error(s): {'; '.join(errors[:3])}"
                    )
                else:
                    st.success(f"✅  {inserted} new item(s) added, {updated} updated.")
                safe_rerun()
            except Exception as exc:
                st.error(f"Import failed: {exc}")


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
    tx_type = fc1.selectbox("Type", ["all", "issue", "deposit"], key="hist_type")
    search = fc2.text_input("Search", placeholder="Item, serial no, user…", key="hist_search")

    role = st.session_state.get("role", "user")
    performed_by = None if role == "manager" else st.session_state["user"]

    rows = list_transactions(conn, tx_type=tx_type, search=search.strip(), performed_by=performed_by)
    df = pd.DataFrame(rows_to_dicts(rows))

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
                f'<div style="padding-top:1rem">'
                f'<span class="pill p-low" style="font-size:0.8rem">⚠ {len(alerts)} low stock</span></div>',
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
