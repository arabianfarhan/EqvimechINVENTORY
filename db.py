import csv
import difflib
import os

import psycopg2
import psycopg2.extras

# ---------------------------------------------------------------------------
# Database backend: PostgreSQL via Supabase (or any Postgres host).
# Set DATABASE_URL in Streamlit secrets (.streamlit/secrets.toml) or as an
# environment variable.  Format:
#   postgresql://USER:PASSWORD@HOST:PORT/DBNAME
# ---------------------------------------------------------------------------

DB_PATH = "inventory.db"  # kept for legacy references in app.py; not used
ITEMS_SNAPSHOT_CSV_PATH = "items_master_live.csv"
CATEGORY_OPTIONS = ("Hardware", "Electronics", "Metals", "Others")
DEFAULT_SAMPLE_PART_IDS = {
    "Ballscrew-R25",
    "NutR32",
    "Ballscrew-R32",
    "Ballscrew-R40",
    "Ballscrew-R50",
}


def _get_database_url():
    # Prefer Streamlit secrets, fall back to env var
    try:
        import streamlit as st
        url = st.secrets.get("DATABASE_URL") or st.secrets.get("database", {}).get("url")
        if url:
            return url
    except Exception:
        pass
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL not set. Add it to .streamlit/secrets.toml or as an environment variable."
        )
    return url


def get_conn():
    url = _get_database_url()
    # Parse connection string manually to avoid special character issues
    # Format: postgresql://user:password@host:port/database
    from urllib.parse import urlparse, unquote
    parsed = urlparse(url)
    
    try:
        conn = psycopg2.connect(
            host=parsed.hostname,
            port=parsed.port or 5432,
            database=parsed.path.lstrip("/"),
            user=parsed.username,
            password=unquote(parsed.password) if parsed.password else None,
            sslmode='require',
            cursor_factory=psycopg2.extras.RealDictCursor
        )
        conn.autocommit = False
        return conn
    except psycopg2.OperationalError as e:
        raise RuntimeError(
            f"DB connection failed | host={parsed.hostname} port={parsed.port} user={parsed.username} db={parsed.path.lstrip('/')} | raw_error={str(e).strip()!r}"
        ) from None


def init_db(conn):
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS parts (
            id SERIAL PRIMARY KEY,
            part_id TEXT UNIQUE,
            name TEXT,
            description TEXT,
            unit TEXT DEFAULT 'Nos',
            quantity INTEGER DEFAULT 0,
            location TEXT DEFAULT '',
            min_level INTEGER DEFAULT 0,
            reorder_qty INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS transactions (
            id SERIAL PRIMARY KEY,
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
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """
    )
    conn.commit()
    ensure_columns(conn)
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_transactions_part_id ON transactions(part_id)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_transactions_machine_sn ON transactions(machine_sn)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_transactions_created_at ON transactions(created_at)"
    )
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
            "created_at": "TIMESTAMPTZ",
            "updated_at": "TIMESTAMPTZ",
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
            "created_at": "TIMESTAMPTZ",
            "returnable": "INTEGER DEFAULT 0",
            "returned_at": "TIMESTAMPTZ",
            "returned_tx_id": "INTEGER DEFAULT 0",
            "source_tx_id": "INTEGER DEFAULT 0",
        },
    )
    c = conn.cursor()
    c.execute("UPDATE parts SET created_at = COALESCE(created_at, NOW())")
    c.execute("UPDATE parts SET updated_at = COALESCE(updated_at, NOW())")
    c.execute("UPDATE transactions SET created_at = COALESCE(created_at, NOW())")
    conn.commit()


def ensure_table_columns(conn, table_name, desired_columns):
    c = conn.cursor()
    c.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
        (table_name,),
    )
    existing = {row["column_name"] for row in c.fetchall()}
    for column_name, sql_type in desired_columns.items():
        if column_name not in existing:
            c.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {sql_type}")
    conn.commit()


def seed_sample_data(conn):
    c = conn.cursor()
    c.execute("SELECT COUNT(*) AS cnt FROM parts")
    if c.fetchone()["cnt"] == 0:
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
        for p in parts:
            c.execute(
                """
                INSERT INTO parts (
                    part_id, name, description, unit, quantity, location, min_level, reorder_qty, category, active
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (part_id) DO NOTHING
                """,
                p,
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
    c.execute("SELECT part_id FROM parts")
    current_part_ids = [row["part_id"] for row in c.fetchall()]
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
        sql += " AND (part_id LIKE %s OR name ILIKE %s OR description ILIKE %s OR location ILIKE %s)"
        like_query = f"%{query}%"
        params.extend([like_query, like_query, like_query, like_query])
    sql += " ORDER BY name"
    c.execute(sql, params)
    return c.fetchall()


def get_part(conn, part_id):
    c = conn.cursor()
    c.execute("SELECT * FROM parts WHERE part_id = %s", (part_id,))
    return c.fetchone()


def sync_parts_snapshot_csv(conn, active_only=False):
    c = conn.cursor()
    sql = (
        "SELECT part_id, name, description, unit, quantity, location, min_level, reorder_qty, category, active "
        "FROM parts"
    )
    params = []
    if active_only:
        sql += " WHERE active = 1"
    sql += " ORDER BY name, part_id"
    c.execute(sql, params if params else None)
    rows = c.fetchall()

    fieldnames = [
        "item_code", "name", "description", "unit", "quantity",
        "location", "min_level", "reorder_qty", "category", "active",
    ]
    with open(ITEMS_SNAPSHOT_CSV_PATH, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            category = (row.get("category") or "").strip() or "Others"
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
                    "category": category,
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
            SET name = %s, description = %s, unit = %s, quantity = %s, location = %s,
                min_level = %s, reorder_qty = %s, category = %s, active = %s, updated_at = NOW()
            WHERE part_id = %s
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
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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


def _row_is_effectively_blank(row):
    text_fields = ["part_id", "name", "description", "unit", "location"]
    if any(str(row.get(field) or "").strip() for field in text_fields):
        return False
    numeric_fields = ["quantity", "min_level", "reorder_qty"]
    if any(str(row.get(field) or "").strip() not in {"", "0"} for field in numeric_fields):
        return False
    return True


def _coerce_non_negative_int(value, field_label, row_number):
    if value in (None, ""):
        return 0
    try:
        result = int(value)
    except (TypeError, ValueError):
        try:
            result = int(float(value))
        except (TypeError, ValueError):
            raise ValueError(f"Row {row_number}: {field_label} must be a whole number")
    if result < 0:
        raise ValueError(f"Row {row_number}: {field_label} cannot be negative")
    return result


def _coerce_active_flag(value):
    if isinstance(value, bool):
        return 1 if value else 0
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return 1
    if text in {"0", "false", "no", "n", "off"}:
        return 0
    return 1


def save_master_table(conn, rows):
    c = conn.cursor()
    c.execute("SELECT id, part_id FROM parts ORDER BY id")
    existing_rows = c.fetchall()
    existing_by_id = {int(row["id"]): row for row in existing_rows}

    prepared_existing = []
    prepared_new = []
    seen_part_ids = set()

    for row_number, row in enumerate(rows, start=1):
        row_id_raw = row.get("id")
        row_id = int(row_id_raw) if row_id_raw not in (None, "") else None

        if row_id is None and _row_is_effectively_blank(row):
            continue

        part_id = str(row.get("part_id") or "").strip()
        name = str(row.get("name") or "").strip()
        description = str(row.get("description") or "").strip()
        unit = str(row.get("unit") or "Nos").strip() or "Nos"
        location = str(row.get("location") or "").strip()
        category = str(row.get("category") or "Others").strip() or "Others"
        quantity = _coerce_non_negative_int(row.get("quantity"), "Quantity", row_number)
        min_level = _coerce_non_negative_int(row.get("min_level"), "Min stock level", row_number)
        reorder_qty = _coerce_non_negative_int(row.get("reorder_qty"), "Reorder quantity", row_number)
        active = _coerce_active_flag(row.get("active"))

        if not part_id:
            raise ValueError(f"Row {row_number}: Item Code is required")
        if not name:
            raise ValueError(f"Row {row_number}: Item name is required")
        if category not in CATEGORY_OPTIONS:
            raise ValueError(
                f"Row {row_number}: Category must be one of {', '.join(CATEGORY_OPTIONS)}"
            )
        if part_id in seen_part_ids:
            raise ValueError(f"Row {row_number}: Duplicate Item Code '{part_id}'")
        seen_part_ids.add(part_id)

        payload = {
            "part_id": part_id,
            "name": name,
            "description": description,
            "unit": unit,
            "quantity": quantity,
            "location": location,
            "min_level": min_level,
            "reorder_qty": reorder_qty,
            "category": category,
            "active": active,
        }

        if row_id is None:
            prepared_new.append(payload)
            continue

        if row_id not in existing_by_id:
            raise ValueError(f"Row {row_number}: Item no longer exists. Refresh and try again.")

        payload["id"] = row_id
        payload["current_part_id"] = str(existing_by_id[row_id]["part_id"] or "").strip()
        prepared_existing.append(payload)

    try:
        c.execute("BEGIN")

        for payload in prepared_existing:
            if payload["part_id"] != payload["current_part_id"]:
                c.execute(
                    "UPDATE parts SET part_id = %s WHERE id = %s",
                    (f"__tmp__{payload['id']}__", payload["id"]),
                )

        for payload in prepared_existing:
            c.execute(
                """
                UPDATE parts
                SET part_id = %s, name = %s, description = %s, unit = %s, quantity = %s, location = %s,
                    min_level = %s, reorder_qty = %s, category = %s, active = %s, updated_at = NOW()
                WHERE id = %s
                """,
                (
                    payload["part_id"],
                    payload["name"],
                    payload["description"],
                    payload["unit"],
                    payload["quantity"],
                    payload["location"],
                    payload["min_level"],
                    payload["reorder_qty"],
                    payload["category"],
                    payload["active"],
                    payload["id"],
                ),
            )

        for payload in prepared_new:
            c.execute(
                """
                INSERT INTO parts (
                    part_id, name, description, unit, quantity, location, min_level, reorder_qty, category, active
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    payload["part_id"],
                    payload["name"],
                    payload["description"],
                    payload["unit"],
                    payload["quantity"],
                    payload["location"],
                    payload["min_level"],
                    payload["reorder_qty"],
                    payload["category"],
                    payload["active"],
                ),
            )

        conn.commit()
        sync_parts_snapshot_csv(conn)
        return {"updated": len(prepared_existing), "inserted": len(prepared_new)}
    except psycopg2.IntegrityError as exc:
        conn.rollback()
        raise ValueError(f"Save failed due to a duplicate item code: {exc}")
    except Exception:
        conn.rollback()
        raise


def pick_material(conn, part_id, machine_serials, qty, performed_by, performed_role, purpose, note="", returnable=False):
    c = conn.cursor()
    part = get_part(conn, part_id)
    if part is None:
        raise ValueError("Part not found")
    if not part["active"]:
        raise ValueError("Inactive items cannot be issued")
    if qty <= 0:
        raise ValueError("Quantity must be at least 1")
    if not machine_serials or len(machine_serials) == 0:
        raise ValueError("At least one serial number is required")

    # Material issue is now recorded as a single transaction row, even when qty > 1.
    # Keep backward compatibility by accepting either one shared serial or multiple
    # serials and storing them in a single field.
    if len(machine_serials) == 1:
        machine_sn = machine_serials[0]
    elif len(machine_serials) == qty:
        machine_sn = ", ".join(machine_serials)
    else:
        raise ValueError("Number of provided serials does not match quantity")

    if part["quantity"] < qty:
        raise ValueError("Insufficient stock")

    try:
        c.execute("BEGIN")
        previous_stock = part["quantity"]
        running_balance = previous_stock - qty
        c.execute(
            """
            INSERT INTO transactions (
                tx_type, part_id, part_name, qty, unit, performed_by, performed_role,
                machine_sn, purpose, note, prev_stock, balance_stock, returnable
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                "issue",
                part_id,
                part["name"],
                qty,
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
            "UPDATE parts SET quantity = %s, updated_at = NOW() WHERE part_id = %s",
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
            "UPDATE parts SET quantity = %s, updated_at = NOW() WHERE part_id = %s",
            (balance_stock, part_id),
        )
        c.execute(
            """
            INSERT INTO transactions (
                tx_type, part_id, part_name, qty, unit, performed_by, performed_role,
                machine_sn, purpose, note, prev_stock, balance_stock
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
    if (performed_role or "").strip().lower() != "manager":
        raise ValueError("Only managers can return material")

    c.execute("SELECT * FROM transactions WHERE id = %s", (issue_tx_id,))
    issue = c.fetchone()
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
            "UPDATE parts SET quantity = %s, updated_at = NOW() WHERE part_id = %s",
            (balance_stock, issue["part_id"]),
        )
        c.execute(
            """
            INSERT INTO transactions (
                tx_type, part_id, part_name, qty, unit, performed_by, performed_role,
                machine_sn, purpose, note, prev_stock, balance_stock, source_tx_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
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
        return_tx_id = c.fetchone()["id"]
        c.execute(
            "UPDATE transactions SET returned_at = NOW(), returned_tx_id = %s WHERE id = %s",
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
    c.execute("SELECT * FROM parts WHERE active = 1 AND quantity <= min_level ORDER BY quantity, name")
    return c.fetchall()


def list_transactions(conn, tx_type="all", search="", performed_by=None, limit=250):
    c = conn.cursor()
    sql = "SELECT * FROM transactions WHERE 1=1"
    params = []
    if tx_type != "all":
        sql += " AND tx_type = %s"
        params.append(tx_type)
    if performed_by:
        sql += " AND performed_by = %s"
        params.append(performed_by)
    if search:
        sql += " AND (part_id ILIKE %s OR part_name ILIKE %s OR machine_sn ILIKE %s OR purpose ILIKE %s OR note ILIKE %s OR performed_by ILIKE %s)"
        like_query = f"%{search}%"
        params.extend([like_query, like_query, like_query, like_query, like_query, like_query])
    sql += " ORDER BY created_at DESC LIMIT %s"
    params.append(limit)
    c.execute(sql, params)
    return c.fetchall()


def list_open_returnable_issues(conn, search="", performed_by=None, limit=200):
    c = conn.cursor()
    sql = (
        "SELECT * FROM transactions "
        "WHERE tx_type = 'issue' AND returnable = 1 AND returned_at IS NULL"
    )
    params = []
    if performed_by:
        sql += " AND performed_by = %s"
        params.append(performed_by)
    if search:
        sql += (
            " AND (part_id ILIKE %s OR part_name ILIKE %s OR machine_sn ILIKE %s "
            "OR purpose ILIKE %s OR note ILIKE %s OR performed_by ILIKE %s)"
        )
        like_query = f"%{search}%"
        params.extend([like_query, like_query, like_query, like_query, like_query, like_query])
    sql += " ORDER BY created_at DESC LIMIT %s"
    params.append(limit)
    c.execute(sql, params)
    return c.fetchall()


def list_returned_returnable_issues(conn, search="", performed_by=None, limit=200):
    c = conn.cursor()
    sql = (
        "SELECT * FROM transactions "
        "WHERE tx_type = 'issue' AND returnable = 1 AND returned_at IS NOT NULL"
    )
    params = []
    if performed_by:
        sql += " AND performed_by = %s"
        params.append(performed_by)
    if search:
        sql += (
            " AND (part_id ILIKE %s OR part_name ILIKE %s OR machine_sn ILIKE %s "
            "OR purpose ILIKE %s OR note ILIKE %s OR performed_by ILIKE %s)"
        )
        like_query = f"%{search}%"
        params.extend([like_query, like_query, like_query, like_query, like_query, like_query])
    sql += " ORDER BY returned_at DESC, created_at DESC LIMIT %s"
    params.append(limit)
    c.execute(sql, params)
    return c.fetchall()


def get_dashboard_metrics(conn):
    # default no category filter
    return get_dashboard_metrics_with_category(conn, None)


def get_dashboard_metrics_with_category(conn, category=None):
    c = conn.cursor()
    parts_where = "WHERE active = 1"
    parts_params = []
    tx_where = ""
    tx_params = []
    if category and category != "All":
        parts_where += " AND category = %s"
        parts_params.append(category)
        tx_where = " AND part_id IN (SELECT part_id FROM parts WHERE category = %s)"
        tx_params.append(category)

    def _scalar(sql, params):
        c.execute(sql, params if params else None)
        row = c.fetchone()
        return list(row.values())[0] if row else 0

    total_items = _scalar(f"SELECT COUNT(*) AS v FROM parts {parts_where}", parts_params)
    total_stock_units = _scalar(f"SELECT COALESCE(SUM(quantity), 0) AS v FROM parts {parts_where}", parts_params)
    low_stock_items = _scalar(f"SELECT COUNT(*) AS v FROM parts {parts_where} AND quantity <= min_level", parts_params)
    out_of_stock_items = _scalar(f"SELECT COUNT(*) AS v FROM parts {parts_where} AND quantity = 0", parts_params)
    issues_today = _scalar(
        f"SELECT COALESCE(SUM(qty), 0) AS v FROM transactions WHERE tx_type = 'issue' AND created_at::date = CURRENT_DATE {tx_where}",
        tx_params,
    )
    deposits_today = _scalar(
        f"SELECT COALESCE(SUM(qty), 0) AS v FROM transactions WHERE tx_type = 'deposit' AND created_at::date = CURRENT_DATE {tx_where}",
        tx_params,
    )

    return {
        "total_items": total_items,
        "total_stock_units": total_stock_units,
        "low_stock_items": low_stock_items,
        "out_of_stock_items": out_of_stock_items,
        "issues_today": issues_today,
        "deposits_today": deposits_today,
    }


def get_top_consumed_items(conn, limit=5):
    c = conn.cursor()
    c.execute(
        """
        SELECT part_id, part_name, SUM(qty) AS issued_qty
        FROM transactions
        WHERE tx_type = 'issue'
        GROUP BY part_id, part_name
        ORDER BY issued_qty DESC, part_name ASC
        LIMIT %s
        """,
        (limit,),
    )
    return c.fetchall()


def get_top_consumed_items_by_category(conn, category=None, limit=5):
    c = conn.cursor()
    if not category or category == "All":
        return get_top_consumed_items(conn, limit=limit)
    c.execute(
        """
        SELECT t.part_id, t.part_name, SUM(t.qty) AS issued_qty
        FROM transactions t
        JOIN parts p ON p.part_id = t.part_id
        WHERE t.tx_type = 'issue' AND p.category = %s
        GROUP BY t.part_id, t.part_name
        ORDER BY issued_qty DESC, t.part_name ASC
        LIMIT %s
        """,
        (category, limit),
    )
    return c.fetchall()


def get_machine_usage(conn, limit=10):
    c = conn.cursor()
    c.execute(
        """
        SELECT machine_sn, SUM(qty) AS issued_lines
        FROM transactions
        WHERE tx_type = 'issue' AND machine_sn <> ''
        GROUP BY machine_sn
        ORDER BY issued_lines DESC, machine_sn ASC
        LIMIT %s
        """,
        (limit,),
    )
    return c.fetchall()


def get_machine_usage_by_category(conn, category=None, limit=10):
    c = conn.cursor()
    if not category or category == "All":
        return get_machine_usage(conn, limit=limit)
    c.execute(
        """
        SELECT t.machine_sn, SUM(t.qty) AS issued_lines
        FROM transactions t
        JOIN parts p ON p.part_id = t.part_id
        WHERE t.tx_type = 'issue' AND t.machine_sn <> '' AND p.category = %s
        GROUP BY t.machine_sn
        ORDER BY issued_lines DESC, t.machine_sn ASC
        LIMIT %s
        """,
        (category, limit),
    )
    return c.fetchall()


def delete_part(conn, part_id):
    """Soft-delete: mark active=0 so history is preserved."""
    c = conn.cursor()
    c.execute(
        "UPDATE parts SET active = 0, updated_at = NOW() WHERE part_id = %s",
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
        "category": str(row.get("category", "Others")).strip() or "Others",
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
        "category": str(row["category"] or "").strip() or "Others",
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
        and (lambda e, p: (str(e["category"] or "").strip() if (hasattr(e, 'keys') and "category" in e.keys()) else "") == p.get("category", "").strip())(existing, payload)
    )


def _diff_part_fields(existing, payload):
    changed = []
    field_labels = {
        "name": "name",
        "description": "description",
        "unit": "unit",
        "category": "category",
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


# -----------------
# Category helpers
# -----------------
CATEGORY_KEYWORDS = {
    "Hardware": [
        "screw",
        "bolt",
        "nut",
        "washer",
        "bracket",
        "coupling",
        "bearing",
        "block",
        "rail",
        "pulley",
        "pully",
        "shaft",
        "spindle",
        "bracket",
        "coupling",
    ],
    "Electronics": [
        "servo",
        "motor",
        "controller",
        "encoder",
        "sensor",
        "cable",
        "wire",
        "driver",
        "pcb",
        "resistor",
        "capacitor",
        "diode",
    ],
    "Metals": [
        "steel",
        "stainless",
        "aluminium",
        "aluminum",
        "brass",
        "copper",
        "rod",
        "bar",
        "plate",
        "sheet",
    ],
}


def guess_category(text):
    t = _normalize_compare_text(text or "")
    scores = {k: 0 for k in CATEGORY_KEYWORDS}
    for cat, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw and (kw in t):
                scores[cat] += 1
    # pick highest score, require at least one match
    best = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    if best and best[0][1] > 0:
        return best[0][0]
    return "Others"


def auto_classify_parts(conn, apply=False):
    """Suggest or apply category classification for existing parts.
    If apply=True, updates the DB and returns the number updated and list of changes.
    If apply=False, returns preview list of suggested changes without committing.
    """
    c = conn.cursor()
    c.execute("SELECT part_id, name, description, category FROM parts")
    rows = c.fetchall()
    suggestions = []
    changed_count = 0
    for row in rows:
        part_id = row["part_id"]
        name = row["name"]
        desc = row["description"] or ""
        current = (row["category"] or "").strip() or "Others"
        suggested = guess_category(" ".join([name or "", desc or ""]))
        suggestions.append({
            "part_id": part_id,
            "name": name,
            "current": current,
            "suggested": suggested,
        })
        if apply and suggested != current:
            c.execute("UPDATE parts SET category = %s, updated_at = NOW() WHERE part_id = %s", (suggested, part_id))
            changed_count += 1
    if apply:
        conn.commit()
        if changed_count:
            sync_parts_snapshot_csv(conn)
    return {"changes": suggestions, "updated": changed_count}


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
    _c = conn.cursor()
    _c.execute("SELECT part_id, name, description, unit, quantity, location, min_level, reorder_qty, active, category FROM parts")
    existing_payloads = [
        _payload_from_existing_row(row)
        for row in _c.fetchall()
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
