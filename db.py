import sqlite3

DB_PATH = "inventory.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn):
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS parts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            part_id TEXT UNIQUE,
            name TEXT,
            description TEXT,
            unit TEXT DEFAULT 'Nos',
            quantity INTEGER DEFAULT 0,
            location TEXT DEFAULT '',
            min_level INTEGER DEFAULT 0,
            reorder_qty INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tx_type TEXT DEFAULT 'issue',
            part_id TEXT,
            part_name TEXT,
            qty INTEGER,
            unit TEXT DEFAULT 'Nos',
            performed_by TEXT,
            performed_role TEXT DEFAULT 'user',
            machine_sn TEXT DEFAULT '',
            purpose TEXT DEFAULT '',
            note TEXT DEFAULT '',
            prev_stock INTEGER DEFAULT 0,
            balance_stock INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
    ensure_columns(conn)
    c.execute("CREATE INDEX IF NOT EXISTS idx_transactions_part_id ON transactions(part_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_transactions_machine_sn ON transactions(machine_sn)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_transactions_created_at ON transactions(created_at)")
    conn.commit()


def ensure_columns(conn):
    ensure_table_columns(
        conn,
        "parts",
        {
            "description": "TEXT DEFAULT ''",
            "unit": "TEXT DEFAULT 'Nos'",
            "location": "TEXT DEFAULT ''",
            "min_level": "INTEGER DEFAULT 0",
            "reorder_qty": "INTEGER DEFAULT 0",
            "active": "INTEGER DEFAULT 1",
            "created_at": "TEXT",
            "updated_at": "TEXT",
        },
    )
    ensure_table_columns(
        conn,
        "transactions",
        {
            "tx_type": "TEXT DEFAULT 'issue'",
            "part_name": "TEXT DEFAULT ''",
            "unit": "TEXT DEFAULT 'Nos'",
            "performed_by": "TEXT DEFAULT ''",
            "performed_role": "TEXT DEFAULT 'user'",
            "machine_sn": "TEXT DEFAULT ''",
            "purpose": "TEXT DEFAULT ''",
            "note": "TEXT DEFAULT ''",
            "prev_stock": "INTEGER DEFAULT 0",
            "balance_stock": "INTEGER DEFAULT 0",
            "created_at": "TEXT",
        },
    )
    c = conn.cursor()
    c.execute("UPDATE parts SET created_at = COALESCE(created_at, CURRENT_TIMESTAMP)")
    c.execute("UPDATE parts SET updated_at = COALESCE(updated_at, CURRENT_TIMESTAMP)")
    c.execute("UPDATE transactions SET created_at = COALESCE(created_at, CURRENT_TIMESTAMP)")
    conn.commit()


def ensure_table_columns(conn, table_name, desired_columns):
    c = conn.cursor()
    existing = {row[1] for row in c.execute(f"PRAGMA table_info({table_name})").fetchall()}
    for column_name, sql_type in desired_columns.items():
        if column_name not in existing:
            c.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {sql_type}")
    conn.commit()


def seed_sample_data(conn):
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM parts")
    if c.fetchone()[0] == 0:
        parts = [
            (
                "Ballscrew-R25",
                "Ballscrew R25",
                "Ballscrew R25-5-890-05.05 | Make: Hiwin",
                "Nos",
                1,
                "Shelf 1",
                1,
                5,
                1,
            ),
            ("NutR32", "Nut R32", "R32-10T3-FSI | Make: Hiwin", "Nos", 1, "Shelf 1", 1, 5, 1),
            (
                "Ballscrew-R32",
                "Ballscrew R32",
                "R32-10-1340-1340-0.05 | Make: Hiwin",
                "Nos",
                2,
                "Shelf 2",
                1,
                5,
                1,
            ),
            (
                "Ballscrew-R40",
                "Ballscrew R40",
                "R40-10-1340-1340-0.05 | Make: Hiwin",
                "Nos",
                8,
                "Shelf 2",
                2,
                5,
                1,
            ),
            (
                "Ballscrew-R50",
                "Ballscrew R50",
                "R50-10-1600-0.05 | Make: Hiwin",
                "Nos",
                4,
                "Shelf 3",
                2,
                5,
                1,
            ),
        ]
        c.executemany(
            """
            INSERT INTO parts (
                part_id, name, description, unit, quantity, location, min_level, reorder_qty, active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            parts,
        )
        conn.commit()


def get_parts(conn, query="", active_only=True):
    c = conn.cursor()
    sql = "SELECT * FROM parts WHERE 1=1"
    params = []
    if active_only:
        sql += " AND active = 1"
    if query:
        sql += " AND (part_id LIKE ? OR name LIKE ? OR description LIKE ? OR location LIKE ?)"
        like_query = f"%{query}%"
        params.extend([like_query, like_query, like_query, like_query])
    sql += " ORDER BY name"
    return c.execute(sql, params).fetchall()


def get_part(conn, part_id):
    c = conn.cursor()
    return c.execute("SELECT * FROM parts WHERE part_id = ?", (part_id,)).fetchone()


def save_part(conn, part_data):
    c = conn.cursor()
    existing = get_part(conn, part_data["part_id"])
    if existing:
        c.execute(
            """
            UPDATE parts
            SET name = ?, description = ?, unit = ?, quantity = ?, location = ?,
                min_level = ?, reorder_qty = ?, active = ?, updated_at = CURRENT_TIMESTAMP
            WHERE part_id = ?
            """,
            (
                part_data["name"],
                part_data["description"],
                part_data["unit"],
                part_data["quantity"],
                part_data["location"],
                part_data["min_level"],
                part_data["reorder_qty"],
                part_data["active"],
                part_data["part_id"],
            ),
        )
    else:
        c.execute(
            """
            INSERT INTO parts (
                part_id, name, description, unit, quantity, location, min_level, reorder_qty, active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                part_data["part_id"],
                part_data["name"],
                part_data["description"],
                part_data["unit"],
                part_data["quantity"],
                part_data["location"],
                part_data["min_level"],
                part_data["reorder_qty"],
                part_data["active"],
            ),
        )
    conn.commit()


def pick_material(conn, part_id, machine_serials, performed_by, performed_role, purpose, note=""):
    c = conn.cursor()
    part = get_part(conn, part_id)
    if part is None:
        raise ValueError("Part not found")
    if not part["active"]:
        raise ValueError("Inactive items cannot be issued")
    qty = len(machine_serials)
    if qty <= 0:
        raise ValueError("At least one serial number is required")
    if part["quantity"] < qty:
        raise ValueError("Insufficient stock")

    try:
        c.execute("BEGIN")
        running_balance = part["quantity"]
        for machine_sn in machine_serials:
            previous_stock = running_balance
            running_balance -= 1
            c.execute(
                """
                INSERT INTO transactions (
                    tx_type, part_id, part_name, qty, unit, performed_by, performed_role,
                    machine_sn, purpose, note, prev_stock, balance_stock
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "issue",
                    part_id,
                    part["name"],
                    1,
                    part["unit"],
                    performed_by,
                    performed_role,
                    machine_sn,
                    purpose,
                    note,
                    previous_stock,
                    running_balance,
                ),
            )
        c.execute(
            "UPDATE parts SET quantity = ?, updated_at = CURRENT_TIMESTAMP WHERE part_id = ?",
            (running_balance, part_id),
        )
        conn.commit()
        return running_balance
    except Exception:
        conn.rollback()
        raise


def deposit_stock(conn, part_id, qty, performed_by, performed_role, note=""):
    c = conn.cursor()
    part = get_part(conn, part_id)
    if part is None:
        raise ValueError("Create the item in Item Master before depositing stock")
    if qty <= 0:
        raise ValueError("Deposit quantity must be greater than zero")

    previous_stock = part["quantity"]
    balance_stock = previous_stock + qty
    try:
        c.execute("BEGIN")
        c.execute(
            "UPDATE parts SET quantity = ?, updated_at = CURRENT_TIMESTAMP WHERE part_id = ?",
            (balance_stock, part_id),
        )
        c.execute(
            """
            INSERT INTO transactions (
                tx_type, part_id, part_name, qty, unit, performed_by, performed_role,
                machine_sn, purpose, note, prev_stock, balance_stock
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "deposit",
                part_id,
                part["name"],
                qty,
                part["unit"],
                performed_by,
                performed_role,
                "",
                "stock deposit",
                note,
                previous_stock,
                balance_stock,
            ),
        )
        conn.commit()
        return balance_stock
    except Exception:
        conn.rollback()
        raise


def low_stock_alerts(conn):
    c = conn.cursor()
    return c.execute(
        "SELECT * FROM parts WHERE active = 1 AND quantity <= min_level ORDER BY quantity, name"
    ).fetchall()


def list_transactions(conn, tx_type="all", search="", performed_by=None, limit=250):
    c = conn.cursor()
    sql = "SELECT * FROM transactions WHERE 1=1"
    params = []
    if tx_type != "all":
        sql += " AND tx_type = ?"
        params.append(tx_type)
    if performed_by:
        sql += " AND performed_by = ?"
        params.append(performed_by)
    if search:
        sql += " AND (part_id LIKE ? OR part_name LIKE ? OR machine_sn LIKE ? OR purpose LIKE ? OR note LIKE ? OR performed_by LIKE ?)"
        like_query = f"%{search}%"
        params.extend([like_query, like_query, like_query, like_query, like_query, like_query])
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    return c.execute(sql, params).fetchall()


def get_dashboard_metrics(conn):
    c = conn.cursor()
    return {
        "total_items": c.execute("SELECT COUNT(*) FROM parts WHERE active = 1").fetchone()[0],
        "total_stock_units": c.execute("SELECT COALESCE(SUM(quantity), 0) FROM parts WHERE active = 1").fetchone()[0],
        "low_stock_items": c.execute(
            "SELECT COUNT(*) FROM parts WHERE active = 1 AND quantity <= min_level"
        ).fetchone()[0],
        "out_of_stock_items": c.execute(
            "SELECT COUNT(*) FROM parts WHERE active = 1 AND quantity = 0"
        ).fetchone()[0],
        "issues_today": c.execute(
            "SELECT COALESCE(SUM(qty), 0) FROM transactions WHERE tx_type = 'issue' AND DATE(created_at) = DATE('now')"
        ).fetchone()[0],
        "deposits_today": c.execute(
            "SELECT COALESCE(SUM(qty), 0) FROM transactions WHERE tx_type = 'deposit' AND DATE(created_at) = DATE('now')"
        ).fetchone()[0],
    }


def get_top_consumed_items(conn, limit=5):
    c = conn.cursor()
    return c.execute(
        """
        SELECT part_id, part_name, SUM(qty) AS issued_qty
        FROM transactions
        WHERE tx_type = 'issue'
        GROUP BY part_id, part_name
        ORDER BY issued_qty DESC, part_name ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def get_machine_usage(conn, limit=10):
    c = conn.cursor()
    return c.execute(
        """
        SELECT machine_sn, COUNT(*) AS issued_lines
        FROM transactions
        WHERE tx_type = 'issue' AND machine_sn <> ''
        GROUP BY machine_sn
        ORDER BY issued_lines DESC, machine_sn ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def delete_part(conn, part_id):
    """Soft-delete: mark active=0 so history is preserved."""
    c = conn.cursor()
    c.execute(
        "UPDATE parts SET active = 0, updated_at = CURRENT_TIMESTAMP WHERE part_id = ?",
        (part_id,),
    )
    conn.commit()


def rows_to_dicts(rows):
    """Convert a list of sqlite3.Row objects to plain dicts for pandas."""
    return [dict(r) for r in rows]


def import_parts_from_csv(conn, records):
    """
    Upsert parts from a list of dicts (from CSV import).
    Required keys: part_id, name.
    Optional: description, unit, quantity, location, min_level, reorder_qty, active.
    Returns (inserted, updated, skipped_errors) counts.
    """
    inserted = updated = errors = 0
    for row in records:
        try:
            part_id = str(row.get("part_id", "")).strip()
            name = str(row.get("name", "")).strip()
            if not part_id or not name:
                errors += 1
                continue
            save_part(conn, {
                "part_id": part_id,
                "name": name,
                "description": str(row.get("description", "")).strip(),
                "unit": str(row.get("unit", "Nos")).strip() or "Nos",
                "quantity": int(row.get("quantity", 0) or 0),
                "location": str(row.get("location", "")).strip(),
                "min_level": int(row.get("min_level", 0) or 0),
                "reorder_qty": int(row.get("reorder_qty", 0) or 0),
                "active": int(row.get("active", 1) or 1),
            })
            if get_part(conn, part_id):
                updated += 1
            else:
                inserted += 1
        except Exception:
            errors += 1
    return inserted, updated, errors
