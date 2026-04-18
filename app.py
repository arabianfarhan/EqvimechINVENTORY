import os
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
    DB_PATH,
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
            padding-top: 2.5rem !important;
            padding-bottom: 4rem !important;
            max-width: 880px !important;
        }
        @media (min-width: 1100px) {
            .block-container {
                padding-top: 3.5rem !important;
            }
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
            border: 1.8px solid #cbd5e1 !important;
            border-radius: 8px !important;
            background: #ffffff !important;
            color: #0f172a !important;
            box-shadow: inset 0 1px 2px rgba(15,23,42,0.04) !important;
            transition: border-color 0.15s, box-shadow 0.15s !important;
        }
        .stTextInput input:focus, .stTextArea textarea:focus, .stNumberInput input:focus {
            border-color: #0d9488 !important;
            box-shadow: 0 0 0 3px rgba(13,148,136,0.15), inset 0 1px 2px rgba(15,23,42,0.04) !important;
            outline: none !important;
        }
        /* selectbox / multiselect borders */
        div[data-baseweb="select"] > div {
            border: 1.8px solid #cbd5e1 !important;
            border-radius: 8px !important;
            background: #ffffff !important;
        }
        div[data-baseweb="select"] > div:focus-within {
            border-color: #0d9488 !important;
            box-shadow: 0 0 0 3px rgba(13,148,136,0.15) !important;
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
        .stButton > button[kind="primary"]:hover {
            background: #0f766e !important;
            box-shadow: 0 4px 12px rgba(13,148,136,0.28) !important;
        }
        .stButton > button[kind="secondary"] {
            background: #ffffff !important;
            color: #0f172a !important;
            border: 1px solid #dbe3ee !important;
            border-radius: 12px !important;
            font-weight: 600 !important;
            font-size: 0.92rem !important;
            min-height: 3rem !important;
            width: 100% !important;
            justify-content: flex-start !important;
            text-align: left !important;
            white-space: normal !important;
            line-height: 1.35 !important;
            box-shadow: none !important;
        }
        .stButton > button[kind="secondary"]:hover {
            border-color: #0d9488 !important;
            color: #0d9488 !important;
            background: #f8fffd !important;
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
        .metrics-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 0.85rem;
            margin-bottom: 1.2rem;
        }
        .metric-card {
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 16px;
            padding: 1.3rem 1.4rem;
            box-shadow: 0 2px 8px rgba(15,23,42,0.07);
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
        .metric-label { color: #94a3b8; font-size: 0.7rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.09em; }
        .metric-value { color: #0f172a; font-size: 2rem; font-weight: 800; line-height: 1; }
        .m-accent { color: #0d9488 !important; }
        .m-warn   { color: #ea580c !important; }
        .m-danger { color: #dc2626 !important; }
        .m-issued { color: #6366f1 !important; }
        .m-deposit{ color: #0ea5e9 !important; }

        /* ── Low-stock banner ── */
        .low-stock-banner {
            background: #fff7ed;
            border: 1.5px solid #fb923c;
            border-radius: 10px;
            padding: 0.55rem 1rem;
            margin-bottom: 0.8rem;
            display: flex;
            align-items: center;
            gap: 0.5rem;
            font-size: 0.9rem;
            font-weight: 700;
            color: #c2410c;
        }
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
        st.markdown("---")
        st.caption("Danger zone — reset application database")
        reset_code = st.text_input("Enter reset code to wipe app (permanent)", type="password", key="reset_code_input")
        if st.button("Reset app (permanent)", key="reset_app", type="primary"):
            if reset_code == "611881":
                try:
                    # close any open connections, remove DB file, re-create schema and seed data
                    try:
                        conn = get_conn()
                        conn.close()
                    except Exception:
                        pass
                    if os.path.exists(DB_PATH):
                        os.remove(DB_PATH)
                    conn = get_conn()
                    init_db(conn)
                    seed_sample_data(conn)
                    # clear session state to avoid stale selections
                    for k in list(st.session_state.keys()):
                        st.session_state.pop(k, None)
                    st.success("App reset complete — fresh state initialized.")
                    safe_rerun()
                except Exception as e:
                    st.error(f"Reset failed: {e}")
            else:
                st.error("Incorrect reset code.")


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


def render_part_browser(conn, parts, search_key, selected_key, button_prefix, placeholder):
    search_term = st.text_input(
        "Search item",
        placeholder=placeholder,
        key=search_key,
        label_visibility="collapsed",
    )
    matches = filter_parts(parts, search_term)

    valid_ids = {p["part_id"] for p in parts}
    selected_id = st.session_state.get(selected_key)
    if selected_id not in valid_ids:
        st.session_state.pop(selected_key, None)
        selected_id = None

    list_col, detail_col = st.columns([1.05, 1.45], gap="large")

    with list_col:
        st.caption(f"Showing {len(matches)} of {len(parts)} items")
        with st.container(height=460, border=True):
            if not matches:
                st.info("No items matched your search.")
            else:
                for p in matches:
                    label = f"{p['name']} | {p['part_id']}"
                    if st.button(
                        label,
                        key=f"{button_prefix}_{p['part_id']}",
                        use_container_width=True,
                        type="secondary",
                    ):
                        st.session_state[selected_key] = p["part_id"]
                        safe_rerun()

    selected_id = st.session_state.get(selected_key)
    selected_part = get_part(conn, selected_id) if selected_id else None
    return selected_part, detail_col


def items_page(conn):
    parts = get_parts(conn)

    if not parts:
        st.info("No items available.")
        return

    part, detail_col = render_part_browser(
        conn,
        parts,
        "items_search",
        "items_selected_id",
        "items_browser",
        "Type item name, ID, description or location…",
    )

    with detail_col:
        if not part:
            st.info("Select an item from the list to view its details.")
            return

        st.markdown("<div class='section-label'>Item Details</div>", unsafe_allow_html=True)
        st.markdown(
            f"""
            <div class="item-card">
                <div class="item-name">{part['name']}</div>
                <div class="item-desc">{part['description']}</div>
                <div class="pill-row">
                    <span class="pill p-neutral">{part['part_id']}</span>
                    <span class="pill p-neutral">Location: {part['location']}</span>
                    <span class="pill p-neutral">Unit: {part['unit']}</span>
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
                {"<div style='margin-top:.6rem;font-size:0.9rem;color:#b45309;font-weight:700'>↩ Returnable — item must be returned to store</div>" if done.get('returnable') else ""}
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("← Issue another item", key="pick_another", type="secondary"):
            st.session_state.pop("pick_done", None)
            st.session_state.pop("pick_selected_id", None)
            safe_rerun()
        return

    available = get_parts(conn)
    if not available:
        st.info("No active items available for issue.")
        return

    part, detail_col = render_part_browser(
        conn,
        available,
        "pick_search",
        "pick_selected_id",
        "pick_browser",
        "Type item name, ID, description or location…",
    )

    with detail_col:
        if not part:
            st.info("Select an item from the list to issue material.")
            return

        st.markdown("<div class='section-label'>Issue Material</div>", unsafe_allow_html=True)
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
        purpose_options = st.multiselect(
            "Purpose / Usage *",
            ["Assembly", "Checking", "Testing", "Other"],
            key="pick_purpose_opts",
        )
        purpose_other = ""
        if "Other" in purpose_options:
            purpose_other = st.text_input(
                "Specify other purpose",
                placeholder="e.g. UTM-200 calibration",
                key="pick_purpose_other",
            )
        returnable = st.checkbox("↩  Returnable (material will be brought back)", key="pick_returnable")
        note = st.text_input("Note (optional)", placeholder="e.g. Urgent – project deadline", key="pick_note")

        if st.button("✅  Confirm Material Issue", key="confirm_pick", type="primary"):
            serials = [s.strip() for s in serials_raw.replace("\n", ",").split(",") if s.strip()]
            purpose_parts = [p for p in purpose_options if p != "Other"]
            if purpose_other.strip():
                purpose_parts.append(purpose_other.strip())
            purpose_str = ", ".join(purpose_parts)

            if len(serials) != int(qty):
                st.error(
                    f"You entered {len(serials)} serial number(s) but picked {int(qty)} unit(s). "
                    "One serial number per unit is required."
                )
            elif not purpose_options:
                st.error("Please select at least one Purpose / Usage.")
            elif "Other" in purpose_options and not purpose_other.strip():
                st.error("Please specify the other purpose.")
            else:
                try:
                    new_balance = pick_material(
                        conn,
                        part["part_id"],
                        serials,
                        st.session_state["user"],
                        st.session_state["role"],
                        purpose_str,
                        note.strip(),
                        returnable=returnable,
                    )
                    st.session_state["pick_done"] = {
                        "part": part["name"],
                        "qty": int(qty),
                        "balance": new_balance,
                        "unit": part["unit"],
                        "returnable": returnable,
                    }
                    safe_rerun()
                except Exception as exc:
                    st.error(str(exc))


def deposit_stock_page(conn):
    parts = get_parts(conn, active_only=False)
    if not parts:
        st.info("Add items via Item Master before depositing stock.")
        return

    part, detail_col = render_part_browser(
        conn,
        parts,
        "deposit_search",
        "deposit_selected_id",
        "deposit_browser",
        "Type item name, ID, description or location…",
    )

    with detail_col:
        if not part:
            st.info("Select an item from the list to deposit stock.")
            return

        st.markdown("<div class='section-label'>Deposit Stock</div>", unsafe_allow_html=True)
        st.markdown(
            f"""
            <div class="item-card">
                <div class="item-name">{part['name']}</div>
                <div class="item-desc">{part['description']}</div>
                <div class="pill-row">
                    {stock_pill(part['quantity'], part['min_level'])}
                    <span class="pill p-neutral">Location: {part['location']}</span>
                    <span class="pill p-neutral">Unit: {part['unit']}</span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown("---")

        with st.form("deposit_form"):
            qty = st.number_input("Quantity to deposit", min_value=1, value=1, step=1)
            note = st.text_input("Note / GRN reference *", placeholder="e.g. GRN-001 / Vendor invoice (required)")
            col_save, col_cancel = st.columns([1, 1])
            submitted = col_save.form_submit_button("📥  Confirm Deposit", use_container_width=True)
            cancelled = col_cancel.form_submit_button("✖  Cancel", use_container_width=True, type="secondary")

            if cancelled:
                st.session_state.pop("deposit_search", None)
                st.session_state.pop("deposit_selected_id", None)
                safe_rerun()

            if submitted:
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
                <div class="metric-label">Deposited Today</div>
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
    st.markdown("## 🏗️ Eqvimech Inventory")
    if alerts:
        st.markdown(
            f'<div class="low-stock-banner">'
            f'⚠️&nbsp; {len(alerts)} item(s) are at or below minimum stock level'
            f'</div>',
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
