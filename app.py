import streamlit as st
import sqlite3
from datetime import datetime

DB_PATH = "inventory.db"

# Simple users (replace with secure auth in production)
USERS = {
    "storemanager": {"password": "manager123", "role": "manager"},
    "user1": {"password": "user123", "role": "user"}
}

@st.cache_resource
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(conn):
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS parts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        part_id TEXT UNIQUE,
        name TEXT,
        quantity INTEGER,
        location TEXT
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        part_id TEXT,
        qty INTEGER,
        taken_by TEXT,
        machine_sn TEXT,
        purpose TEXT,
        timestamp TEXT
    )
    """)
    conn.commit()

def seed_sample_data(conn):
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM parts")
    if c.fetchone()[0] == 0:
        parts = [
            ("P-1001", "Bearing", 50, "A1"),
            ("P-1002", "Bolt M8", 200, "A2"),
            ("P-1003", "Shaft", 20, "B1"),
            ("Ballscrew-R25", "Ballscrew R25-5-890-05.05 | Make : Hiwin", 1, "Shelf 1"),
            ("NutR32", "R32-10T3-FSI | Make : Hiwin", 1, "Shelf 1"),
            ("Ballscrew-R32", "R32-10-1340-1340-0.05 | Make : Hiwin", 2, "Shelf 2"),
            ("Ballscrew-R40", "R40-10-1340-1340-0.05 | Make : Hiwin", 8, "Shelf 2"),
            ("Ballscrew-R50", "R50-10-1600-0.05 | Make : Hiwin", 4, "Shelf 3"),
        ]
        c.executemany("INSERT INTO parts (part_id, name, quantity, location) VALUES (?, ?, ?, ?)", parts)
        conn.commit()

def authenticate():
    st.sidebar.title("Login")
    username = st.sidebar.text_input("Username")
    password = st.sidebar.text_input("Password", type="password")
    if st.sidebar.button("Login"):
        user = USERS.get(username)
        if user and user["password"] == password:
            st.session_state["user"] = username
            st.session_state["role"] = user["role"]
            st.sidebar.success(f"Logged in as {username} ({user['role']})")
        else:
            st.sidebar.error("Invalid credentials")

def logout():
    if st.sidebar.button("Logout"):
        st.session_state.clear()
        st.experimental_rerun()

def show_inventory(conn):
    st.header("Inventory")
    c = conn.cursor()
    rows = c.execute("SELECT * FROM parts ORDER BY part_id").fetchall()
    for r in rows:
        st.write(f"**{r['part_id']}** — {r['name']} | Qty: {r['quantity']} | Loc: {r['location']}")

    st.markdown("---")
    if st.session_state.get("role") == "manager":
        st.subheader("Add / Update Part")
        part_id = st.text_input("Part ID")
        name = st.text_input("Name")
        qty = st.number_input("Quantity", min_value=0, value=0)
        loc = st.text_input("Location")
        if st.button("Add / Update"):
            c.execute("INSERT OR REPLACE INTO parts (part_id, name, quantity, location) VALUES (?, ?, ?, ?)",
                      (part_id, name, qty, loc))
            conn.commit()
            st.success("Part added/updated")
            st.experimental_rerun()

def take_material(conn):
    st.header("Take Material")
    c = conn.cursor()
    parts = c.execute("SELECT part_id, name, quantity FROM parts").fetchall()
    options = [f"{p['part_id']} - {p['name']} (Qty {p['quantity']})" for p in parts]
    if not options:
        st.info("No parts available")
        return
    sel = st.selectbox("Select part", options)
    part_id = sel.split(" - ")[0]
    max_qty = next((p['quantity'] for p in parts if p['part_id'] == part_id), 0)
    qty = st.number_input("Quantity to take", min_value=1, max_value=max_qty, value=1)
    machine_sn = st.text_input("Machine Serial No.")
    purpose = st.text_input("Purpose / Build")
    taken_by = st.session_state.get("user", "anonymous")
    if st.button("Confirm Take"):
        if qty <= 0 or qty > max_qty:
            st.error("Invalid quantity")
        else:
            # update parts
            c.execute("UPDATE parts SET quantity = quantity - ? WHERE part_id = ?", (qty, part_id))
            c.execute("INSERT INTO transactions (part_id, qty, taken_by, machine_sn, purpose, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                      (part_id, qty, taken_by, machine_sn, purpose, datetime.utcnow().isoformat()))
            conn.commit()
            st.success(f"Taken {qty} of {part_id} by {taken_by}")
            st.experimental_rerun()

def show_transactions(conn):
    st.header("Transactions / Audit Log")
    c = conn.cursor()
    rows = c.execute("SELECT * FROM transactions ORDER BY id DESC LIMIT 200").fetchall()
    for r in rows:
        st.write(f"{r['timestamp']}: {r['taken_by']} took {r['qty']} x {r['part_id']} for {r['purpose']} (SN: {r['machine_sn']})")
    if st.button("Export CSV"):
        import pandas as pd
        df = pd.DataFrame(rows)
        csv = df.to_csv(index=False).encode('utf-8')
        st.download_button("Download CSV", csv, "transactions.csv", "text/csv")


def main():
    st.title("Inventory - Stock Keeper")
    conn = get_conn()
    init_db(conn)
    seed_sample_data(conn)

    if "user" not in st.session_state:
        authenticate()
    else:
        st.sidebar.write(f"User: {st.session_state.get('user')} ({st.session_state.get('role')})")
        logout()

    if "user" in st.session_state:
        role = st.session_state.get("role")
        pages = ["Inventory", "Take Material"]
        if role == "manager":
            pages.append("Transactions")
        choice = st.sidebar.selectbox("Page", pages)
        if choice == "Inventory":
            show_inventory(conn)
        elif choice == "Take Material":
            take_material(conn)
        elif choice == "Transactions":
            show_transactions(conn)
    else:
        st.info("Please login from the sidebar to use the app")

if __name__ == '__main__':
    main()
