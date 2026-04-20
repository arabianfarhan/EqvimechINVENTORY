import csv
import difflib
import os
import sqlite3

DB_PATH = "inventory.db"
ITEMS_SNAPSHOT_CSV_PATH = "items_master_live.csv"
DEFAULT_SAMPLE_PART_IDS = {
    "Ballscrew-R25",
    "NutR32",
    "Ballscrew-R32",
    "Ballscrew-R40",
    "Ballscrew-R50",
}


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
            "category": "TEXT DEFAULT 'Others'",
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
            "returnable": "INTEGER DEFAULT 0",
            "returned_at": "TEXT",
            "returned_tx_id": "INTEGER DEFAULT 0",
            "source_tx_id": "INTEGER DEFAULT 0",
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
                "Hardware",
                1,
            ),
            ("NutR32", "Nut R32", "R32-10T3-FSI | Make: Hiwin", "Nos", 1, "Shelf 1", 1, 5, "Hardware", 1),
            (
                "Ballscrew-R32",
                "Ballscrew R32",
                "R32-10-1340-1340-0.05 | Make: Hiwin",
                "Nos",
                2,
                "Shelf 2",
                1,
                5,
                "Hardware",
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
                "Hardware",
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
                "Hardware",
                1,
            ),
        ]
        c.executemany(
            """
            INSERT INTO parts (
                part_id, name, description, unit, quantity, location, min_level, reorder_qty, category, active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            parts,
        )
        conn.commit()


def load_parts_from_snapshot_csv(csv_path=ITEMS_SNAPSHOT_CSV_PATH):
    if not os.path.exists(csv_path):
        return []

    with open(csv_path, newline="", encoding="utf-8") as csvfile:
        reader = csv.DictReader(csvfile)
        return [
            row for row in reader
            if str(row.get("item_code", row.get("part_id", ""))).strip() and str(row.get("name", "")).strip()
        ]


def bootstrap_parts_catalog(conn, csv_path=ITEMS_SNAPSHOT_CSV_PATH):
    c = conn.cursor()
    current_part_ids = [row[0] for row in c.execute("SELECT part_id FROM parts").fetchall()]
    current_count = len(current_part_ids)

    snapshot_records = load_parts_from_snapshot_csv(csv_path)
    snapshot_count = len(snapshot_records)
    is_seed_only = (
        current_count == 0
        or (
            current_count <= len(DEFAULT_SAMPLE_PART_IDS)
            and set(current_part_ids).issubset(DEFAULT_SAMPLE_PART_IDS)
        )
    )

    if snapshot_records and is_seed_only and snapshot_count > current_count:
        return import_parts_from_csv(conn, snapshot_records)

    if current_count == 0:
        seed_sample_data(conn)

    return 0, 0, 0


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


def sync_parts_snapshot_csv(conn, active_only=False):
    c = conn.cursor()
    sql = (
        "SELECT part_id, name, description, unit, quantity, location, min_level, reorder_qty, active "
        "FROM parts"
    )
    params = []
    if active_only:
        sql += " WHERE active = 1"
    sql += " ORDER BY name, part_id"
    rows = c.execute(sql, params).fetchall()

    fieldnames = [
        "item_code", "name", "description", "unit", "quantity",
        "location", "min_level", "reorder_qty", "category", "active",
    ]
    with open(ITEMS_SNAPSHOT_CSV_PATH, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "item_code": row["part_id"],
                    "name": row["name"],
                    "description": row["description"],
                    "unit": row["unit"],
                    "quantity": row["quantity"],
                    "location": row["location"],
                    "min_level": row["min_level"],
                    "reorder_qty": row["reorder_qty"],
                    "category": row.get("category", "Others"),
                    "active": row["active"],
                }
            )


def save_part(conn, part_data):
    c = conn.cursor()
    existing = get_part(conn, part_data["part_id"])
    if existing:
        c.execute(
            """
            UPDATE parts
            SET name = ?, description = ?, unit = ?, quantity = ?, location = ?,
                min_level = ?, reorder_qty = ?, category = ?, active = ?, updated_at = CURRENT_TIMESTAMP
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
                part_data.get("category", "Others"),
                part_data["active"],
                part_data["part_id"],
            ),
        )
    else:
        c.execute(
            """
            INSERT INTO parts (
                part_id, name, description, unit, quantity, location, min_level, reorder_qty, category, active
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
                part_data.get("category", "Others"),
                part_data["active"],
            ),
        )
    conn.commit()
    sync_parts_snapshot_csv(conn)


def pick_material(conn, part_id, machine_serials, performed_by, performed_role, purpose, note="", returnable=False):
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
                    machine_sn, purpose, note, prev_stock, balance_stock, returnable
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    1 if returnable else 0,
                ),
            )
        c.execute(
            "UPDATE parts SET quantity = ?, updated_at = CURRENT_TIMESTAMP WHERE part_id = ?",
            (running_balance, part_id),
        )
        conn.commit()
        sync_parts_snapshot_csv(conn)
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
        sync_parts_snapshot_csv(conn)
        return balance_stock
    except Exception:
        conn.rollback()
        raise


def return_issue_material(conn, issue_tx_id, performed_by, performed_role, note=""):
    c = conn.cursor()
    issue = c.execute("SELECT * FROM transactions WHERE id = ?", (issue_tx_id,)).fetchone()
    if issue is None:
        raise ValueError("Issue record not found")
    if issue["tx_type"] != "issue":
        raise ValueError("Only issue records can be returned")
    if not issue["returnable"]:
        raise ValueError("This issue was not marked as returnable")
    if issue["returned_at"]:
        raise ValueError("This material has already been returned")

    part = get_part(conn, issue["part_id"])
    if part is None:
        raise ValueError("Part not found")

    previous_stock = part["quantity"]
    balance_stock = previous_stock + int(issue["qty"])
    return_note = (note or "").strip()
    if issue["machine_sn"]:
        prefix = f"Return for machine {issue['machine_sn']}"
        return_note = f"{prefix} | {return_note}" if return_note else prefix

    try:
        c.execute("BEGIN")
        c.execute(
            "UPDATE parts SET quantity = ?, updated_at = CURRENT_TIMESTAMP WHERE part_id = ?",
            (balance_stock, issue["part_id"]),
        )
        c.execute(
            """
            INSERT INTO transactions (
                tx_type, part_id, part_name, qty, unit, performed_by, performed_role,
                machine_sn, purpose, note, prev_stock, balance_stock, source_tx_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "return",
                issue["part_id"],
                issue["part_name"],
                int(issue["qty"]),
                issue["unit"],
                performed_by,
                performed_role,
                issue["machine_sn"],
                "returnable material returned",
                return_note,
                previous_stock,
                balance_stock,
                int(issue_tx_id),
            ),
        )
        return_tx_id = c.lastrowid
        c.execute(
            "UPDATE transactions SET returned_at = CURRENT_TIMESTAMP, returned_tx_id = ? WHERE id = ?",
            (return_tx_id, issue_tx_id),
        )
        conn.commit()
        sync_parts_snapshot_csv(conn)
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


def list_open_returnable_issues(conn, search="", performed_by=None, limit=200):
    c = conn.cursor()
    sql = (
        "SELECT * FROM transactions "
        "WHERE tx_type = 'issue' AND returnable = 1 AND COALESCE(returned_at, '') = ''"
    )
    params = []
    if performed_by:
        sql += " AND performed_by = ?"
        params.append(performed_by)
    if search:
        sql += (
            " AND (part_id LIKE ? OR part_name LIKE ? OR machine_sn LIKE ? "
            "OR purpose LIKE ? OR note LIKE ? OR performed_by LIKE ?)"
        )
        like_query = f"%{search}%"
        params.extend([like_query, like_query, like_query, like_query, like_query, like_query])
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    return c.execute(sql, params).fetchall()


def list_returned_returnable_issues(conn, search="", performed_by=None, limit=200):
    c = conn.cursor()
    sql = (
        "SELECT * FROM transactions "
        "WHERE tx_type = 'issue' AND returnable = 1 AND COALESCE(returned_at, '') <> ''"
    )
    params = []
    if performed_by:
        sql += " AND performed_by = ?"
        params.append(performed_by)
    if search:
        sql += (
            " AND (part_id LIKE ? OR part_name LIKE ? OR machine_sn LIKE ? "
            "OR purpose LIKE ? OR note LIKE ? OR performed_by LIKE ?)"
        )
        like_query = f"%{search}%"
        params.extend([like_query, like_query, like_query, like_query, like_query, like_query])
    sql += " ORDER BY returned_at DESC, created_at DESC LIMIT ?"
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
    sync_parts_snapshot_csv(conn)


def rows_to_dicts(rows):
    """Convert a list of sqlite3.Row objects to plain dicts for pandas."""
    return [dict(r) for r in rows]


def _normalize_part_payload(row):
    part_key = str(row.get("item_code", row.get("part_id", ""))).strip()
    return {
        "part_id": part_key,
        "name": str(row.get("name", "")).strip(),
        "description": str(row.get("description", "")).strip(),
        "unit": str(row.get("unit", "Nos")).strip() or "Nos",
        "quantity": int(row.get("quantity", 0) or 0),
        "location": str(row.get("location", "")).strip(),
        "min_level": int(row.get("min_level", 0) or 0),
        "reorder_qty": int(row.get("reorder_qty", 0) or 0),
        "active": int(row.get("active", 1) or 1),
    }


def _payload_from_existing_row(row):
    return {
        "part_id": str(row["part_id"] or "").strip(),
        "name": str(row["name"] or "").strip(),
        "description": str(row["description"] or "").strip(),
        "unit": str(row["unit"] or "Nos").strip() or "Nos",
        "quantity": int(row["quantity"] or 0),
        "location": str(row["location"] or "").strip(),
        "min_level": int(row["min_level"] or 0),
        "reorder_qty": int(row["reorder_qty"] or 0),
        "active": int(row["active"] or 0),
    }


def _normalize_compare_text(value):
    cleaned = []
    for char in str(value or "").lower():
        cleaned.append(char if char.isalnum() else " ")
    return " ".join("".join(cleaned).split())


def _near_duplicate_reason(payload, candidate):
    if payload["part_id"] == candidate["part_id"]:
        return None

    payload_name = _normalize_compare_text(payload["name"])
    candidate_name = _normalize_compare_text(candidate["name"])
    payload_desc = _normalize_compare_text(payload["description"])
    candidate_desc = _normalize_compare_text(candidate["description"])
    payload_combo = " ".join(part for part in [payload_name, payload_desc] if part).strip()
    candidate_combo = " ".join(part for part in [candidate_name, candidate_desc] if part).strip()

    same_unit = payload["unit"] == candidate["unit"]

    if payload_name and payload_name == candidate_name and payload_desc and payload_desc == candidate_desc:
        return "Same name and specification"
    if payload_name and payload_name == candidate_name and same_unit:
        return "Same name"
    if payload_desc and payload_desc == candidate_desc and same_unit:
        return "Same specification"

    name_ratio = difflib.SequenceMatcher(None, payload_name, candidate_name).ratio() if payload_name and candidate_name else 0
    desc_ratio = difflib.SequenceMatcher(None, payload_desc, candidate_desc).ratio() if payload_desc and candidate_desc else 0
    combo_ratio = difflib.SequenceMatcher(None, payload_combo, candidate_combo).ratio() if payload_combo and candidate_combo else 0

    if same_unit and name_ratio >= 0.96 and desc_ratio >= 0.88:
        return "Very similar name and specification"
    if same_unit and combo_ratio >= 0.94 and len(payload_combo) >= 10 and len(candidate_combo) >= 10:
        return "Very similar item details"

    return None


def _find_near_duplicate_matches(payload, existing_payloads, uploaded_payloads):
    matches = []
    seen_codes = set()
    for candidate in [*existing_payloads, *uploaded_payloads]:
        reason = _near_duplicate_reason(payload, candidate)
        candidate_code = candidate["part_id"]
        if not reason or candidate_code in seen_codes:
            continue
        seen_codes.add(candidate_code)
        matches.append(f"{reason} as {candidate_code} ({candidate['name']})")
        if len(matches) >= 3:
            break
    return matches


def _part_matches_payload(existing, payload):
    if existing is None:
        return False

    return (
        str(existing["part_id"]).strip() == payload["part_id"]
        and str(existing["name"] or "").strip() == payload["name"]
        and str(existing["description"] or "").strip() == payload["description"]
        and str(existing["unit"] or "Nos").strip() == payload["unit"]
        and int(existing["quantity"] or 0) == payload["quantity"]
        and str(existing["location"] or "").strip() == payload["location"]
        and int(existing["min_level"] or 0) == payload["min_level"]
        and int(existing["reorder_qty"] or 0) == payload["reorder_qty"]
        and int(existing["active"] or 0) == payload["active"]
    )


def _diff_part_fields(existing, payload):
    changed = []
    field_labels = {
        "name": "name",
        "description": "description",
        "unit": "unit",
        "quantity": "quantity",
        "location": "location",
        "min_level": "min level",
        "reorder_qty": "reorder qty",
        "active": "active",
    }
    for field_name, label in field_labels.items():
        old_value = existing[field_name]
        new_value = payload[field_name]
        if field_name in {"quantity", "min_level", "reorder_qty", "active"}:
            old_value = int(old_value or 0)
        else:
            old_value = str(old_value or "").strip()
        if old_value != new_value:
            changed.append(label)
    return changed


def analyze_parts_import(conn, records):
    analysis = {
        "inserted": 0,
        "updated": 0,
        "unchanged": 0,
        "duplicate_rows": 0,
        "near_duplicate_rows": 0,
        "invalid_rows": 0,
        "messages": [],
        "rows": [],
        "can_import": True,
        "total_rows": len(records),
    }
    seen_part_ids = set()
    existing_payloads = [
        _payload_from_existing_row(row)
        for row in conn.execute(
            "SELECT part_id, name, description, unit, quantity, location, min_level, reorder_qty, active FROM parts"
        ).fetchall()
    ]
    uploaded_payloads = []

    for row_number, row in enumerate(records, start=2):
        entry = {
            "row_number": row_number,
            "action": "invalid",
            "note": "",
            "duplicate_alert": "",
            "payload": None,
        }
        try:
            payload = _normalize_part_payload(row)
            entry["payload"] = payload
            part_id = payload["part_id"]
            name = payload["name"]

            if not part_id or not name:
                analysis["invalid_rows"] += 1
                entry["note"] = "Missing item ID or name"
                analysis["messages"].append(f"Row {row_number}: missing item ID or name")
            elif part_id in seen_part_ids:
                analysis["duplicate_rows"] += 1
                entry["action"] = "duplicate"
                entry["note"] = f"Duplicate item ID in CSV ({part_id})"
                analysis["messages"].append(f"Row {row_number}: duplicate item ID in CSV ({part_id})")
            else:
                seen_part_ids.add(part_id)
                existing = get_part(conn, part_id)

                if existing is None:
                    analysis["inserted"] += 1
                    entry["action"] = "insert"
                    entry["note"] = "Will be added as a new item"
                elif _part_matches_payload(existing, payload):
                    analysis["unchanged"] += 1
                    entry["action"] = "unchanged"
                    entry["note"] = "Matches existing item exactly"
                else:
                    changed_fields = _diff_part_fields(existing, payload)
                    analysis["updated"] += 1
                    entry["action"] = "update"
                    entry["note"] = "Will update: " + ", ".join(changed_fields)

                if entry["action"] in {"insert", "update", "unchanged"}:
                    matches = _find_near_duplicate_matches(payload, existing_payloads, uploaded_payloads)
                    if matches:
                        analysis["near_duplicate_rows"] += 1
                        entry["duplicate_alert"] = "; ".join(matches)
                        analysis["messages"].append(
                            f"Row {row_number}: possible duplicate - {entry['duplicate_alert']}"
                        )
                    uploaded_payloads.append(payload)
        except Exception as exc:
            analysis["invalid_rows"] += 1
            entry["note"] = str(exc)
            analysis["messages"].append(f"Row {row_number}: {exc}")

        analysis["rows"].append(entry)

    if analysis["duplicate_rows"] or analysis["invalid_rows"] or analysis["total_rows"] == 0:
        analysis["can_import"] = False

    return analysis


def import_parts_from_csv(conn, records):
    """
    Upsert parts from a list of dicts (from CSV import).
    Required keys: item_code/name or legacy part_id/name.
    Optional: description, unit, quantity, location, min_level, reorder_qty, active.
    Returns a summary dict with inserted/updated/unchanged/duplicate_rows/invalid_rows counts.
    """
    summary = analyze_parts_import(conn, records)
    for entry in summary["rows"]:
        if entry["action"] in {"insert", "update"} and entry["payload"] is not None:
            save_part(conn, entry["payload"])

    sync_parts_snapshot_csv(conn)
    return summary
