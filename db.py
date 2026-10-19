import csv
import difflib
import functools
import json
import os
import threading
import time
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras
import psycopg2.pool
from psycopg2.extensions import TRANSACTION_STATUS_IDLE

# ---------------------------------------------------------------------------
# Database backend: PostgreSQL via Supabase (or any Postgres host).
# Set DATABASE_URL in Streamlit secrets (.streamlit/secrets.toml) or as an
# environment variable.  Format:
#   postgresql://USER:PASSWORD@HOST:PORT/DBNAME
# ---------------------------------------------------------------------------

# Bump this whenever init_db() creates or changes tables. The app re-runs
# schema setup when it changes, even if the server process was not restarted.
SCHEMA_VERSION = 4

ITEMS_SNAPSHOT_CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "items_master_live.csv")
APP_TIMEZONE = "Asia/Kolkata"

# Canonical categories. The real stock data uses upper-case and misspelt
# variants (HARDWARE, METAL, MACHINIG...), so every category that enters the
# database goes through normalize_category() and is mapped onto these.
CATEGORY_OPTIONS = (
    "Hardware",
    "Metals",
    "Electrical",
    "Electronics",
    "Transmission",
    "Machining",
    "Others",
)
CATEGORY_ALIASES = {
    "hardware": "Hardware",
    "metal": "Metals",
    "metals": "Metals",
    "electrical": "Electrical",
    "electricals": "Electrical",
    "electronic": "Electronics",
    "electronics": "Electronics",
    "transmission": "Transmission",
    "machining": "Machining",
    "machinig": "Machining",
    "machined": "Machining",
    "other": "Others",
    "others": "Others",
}


def normalize_category(value):
    """Map any spelling/case of a category onto a canonical name.

    Blank becomes "Others". Unknown categories are kept (title-cased) rather
    than being silently replaced, so no user data is lost.
    """
    text = " ".join(str(value or "").split())
    if not text:
        return "Others"
    return CATEGORY_ALIASES.get(text.lower(), text.title())


class StockConflictError(ValueError):
    """Raised when stock changed underneath an edit (another user issued/inwarded)."""


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


def _connect_kwargs():
    url = _get_database_url()
    # Parse connection string manually to avoid special character issues
    # Format: postgresql://user:password@host:port/database[?sslmode=...]
    from urllib.parse import urlparse, unquote, parse_qs
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    sslmode = (query.get("sslmode") or [os.environ.get("DB_SSLMODE", "require")])[0]
    return {
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "database": parsed.path.lstrip("/"),
        "user": unquote(parsed.username) if parsed.username else None,
        "password": unquote(parsed.password) if parsed.password else None,
        "sslmode": sslmode,
        "connect_timeout": 10,
        # TCP keepalives so idle connections are not silently dropped by the
        # cloud network / Supabase pooler.
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 3,
        "cursor_factory": psycopg2.extras.RealDictCursor,
    }


def _describe_conn_error(kwargs, exc):
    return RuntimeError(
        f"DB connection failed | host={kwargs['host']} port={kwargs['port']} "
        f"user={kwargs['user']} db={kwargs['database']} | raw_error={str(exc).strip()!r}"
    )


def get_conn():
    """Open a single new connection (used by scripts/tests)."""
    kwargs = _connect_kwargs()
    try:
        conn = psycopg2.connect(**kwargs)
        conn.autocommit = False
        return conn
    except psycopg2.OperationalError as e:
        raise _describe_conn_error(kwargs, e) from None


class ConnectionManager:
    """Thread-safe pool of connections shared by all Streamlit sessions.

    Each script run borrows its own connection and returns it at the end, so
    one user's transaction can never commit or roll back another user's work.
    Connections run in autocommit mode for reads (one network round trip per
    query instead of BEGIN + query + ROLLBACK); write functions open an
    explicit transaction via @transactional. A connection idle for a while is
    health-checked before reuse and replaced if the network/Supabase dropped it.
    """

    IDLE_PING_SECONDS = 45

    def __init__(self, minconn=1, maxconn=12):
        self._kwargs = _connect_kwargs()
        self._lock = threading.Lock()
        self._last_used = {}
        try:
            self._pool = psycopg2.pool.ThreadedConnectionPool(minconn, maxconn, **self._kwargs)
        except psycopg2.OperationalError as e:
            raise _describe_conn_error(self._kwargs, e) from None

    def _healthy(self, conn):
        if conn.closed:
            return False
        try:
            if conn.info.transaction_status != TRANSACTION_STATUS_IDLE:
                conn.rollback()
            conn.autocommit = True
            last = self._last_used.get(id(conn), 0)
            if time.monotonic() - last > self.IDLE_PING_SECONDS:
                with conn.cursor() as c:
                    c.execute("SELECT 1")
            return True
        except Exception:
            return False

    def getconn(self):
        last_error = None
        for _ in range(3):
            try:
                with self._lock:
                    conn = self._pool.getconn()
            except psycopg2.pool.PoolError as e:
                raise RuntimeError("Too many people are using the app right now. Please retry in a few seconds.") from e
            except psycopg2.OperationalError as e:
                last_error = e
                continue
            if self._healthy(conn):
                return conn
            self._last_used.pop(id(conn), None)
            with self._lock:
                self._pool.putconn(conn, close=True)
        raise _describe_conn_error(self._kwargs, last_error or "connection unhealthy")

    def mark_all_stale(self):
        """After a dropped connection, health-check every pooled connection on next use."""
        self._last_used.clear()

    def putconn(self, conn):
        if conn is None:
            return
        broken = bool(conn.closed)
        if not broken:
            try:
                if conn.info.transaction_status != TRANSACTION_STATUS_IDLE:
                    conn.rollback()  # never leave a transaction open between runs
                conn.autocommit = True
                self._last_used[id(conn)] = time.monotonic()
            except Exception:
                broken = True
        if broken:
            self._last_used.pop(id(conn), None)
        with self._lock:
            try:
                self._pool.putconn(conn, close=broken)
            except Exception:
                pass


def transactional(fn):
    """Run a write function inside one explicit transaction.

    Commits are done by the function itself; anything left open (an error or
    an early return) is rolled back. The connection's previous autocommit mode
    is restored afterwards.
    """

    @functools.wraps(fn)
    def wrapper(conn, *args, **kwargs):
        previous = conn.autocommit
        if not previous:
            # Already in manual-transaction mode (nested call or a plain
            # get_conn() connection): the function manages commit itself.
            return fn(conn, *args, **kwargs)
        if conn.info.transaction_status != TRANSACTION_STATUS_IDLE:
            conn.rollback()
        conn.autocommit = False
        try:
            return fn(conn, *args, **kwargs)
        finally:
            try:
                if not conn.closed and conn.info.transaction_status != TRANSACTION_STATUS_IDLE:
                    conn.rollback()
                if not conn.closed:
                    conn.autocommit = previous
            except Exception:
                pass

    return wrapper


@transactional
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
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS app_meta (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
        """
    )
    c.execute(SAVE_MASTER_FUNCTION_SQL)
    c.execute(PR_SCHEMA_SQL)
    conn.commit()
    ensure_columns(conn)
    normalize_existing_categories(conn)
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
    # Backfill only rows that genuinely have NULL timestamps — a fast no-op
    # once all rows are populated (avoids full-table scans on every startup).
    c = conn.cursor()
    c.execute("UPDATE parts SET created_at = NOW() WHERE created_at IS NULL")
    c.execute("UPDATE parts SET updated_at = NOW() WHERE updated_at IS NULL")
    c.execute("UPDATE transactions SET created_at = NOW() WHERE created_at IS NULL")
    conn.commit()


def ensure_table_columns(conn, table_name, desired_columns):
    c = conn.cursor()
    c.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = %s",
        (table_name,),
    )
    existing = {row["column_name"] for row in c.fetchall()}
    for column_name, sql_type in desired_columns.items():
        if column_name not in existing:
            c.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {sql_type}")
    conn.commit()


@transactional
def normalize_existing_categories(conn):
    """One-time-safe cleanup: merge HARDWARE/Hardware, METAL/Metals, MACHINIG/Machining..."""
    c = conn.cursor()
    c.execute("SELECT DISTINCT category FROM parts")
    for row in c.fetchall():
        current = row["category"]
        wanted = normalize_category(current)
        if current != wanted:
            if current is None:
                c.execute("UPDATE parts SET category = %s WHERE category IS NULL", (wanted,))
            else:
                c.execute("UPDATE parts SET category = %s WHERE category = %s", (wanted, current))
    conn.commit()


def get_meta(conn, key):
    c = conn.cursor()
    c.execute("SELECT value FROM app_meta WHERE key = %s", (key,))
    row = c.fetchone()
    return row["value"] if row else None


@transactional
def set_meta(conn, key, value):
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO app_meta (key, value, updated_at) VALUES (%s, %s, NOW())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
        """,
        (key, str(value)),
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
    """Load the starting catalogue from the CSV exactly once, ever.

    The previous version re-checked on every new browser session and could
    re-import the (stale) CSV from GitHub or insert demo ballscrews into a
    freshly reset database. Now a flag in app_meta records that bootstrap
    already happened, so a reset or an empty table is respected.
    """
    if get_meta(conn, "catalog_bootstrapped"):
        return 0, 0, 0
    c = conn.cursor()
    c.execute("SELECT COUNT(*) AS cnt FROM parts")
    if c.fetchone()["cnt"] == 0:
        records = load_parts_from_snapshot_csv(csv_path)
        if records:
            import_parts_from_csv(conn, records)
    set_meta(conn, "catalog_bootstrapped", "1")
    return 0, 0, 0


def get_parts(conn, query="", active_only=True):
    c = conn.cursor()
    sql = "SELECT * FROM parts WHERE 1=1"
    params = []
    if active_only:
        sql += " AND active = 1"
    if query:
        sql += " AND (part_id ILIKE %s OR name ILIKE %s OR description ILIKE %s OR location ILIKE %s)"
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


@transactional
def save_part(conn, part_data):
    payload = dict(part_data)
    payload["category"] = normalize_category(payload.get("category"))
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO parts (
            part_id, name, description, unit, quantity, location, min_level, reorder_qty, category, active
        ) VALUES (%(part_id)s, %(name)s, %(description)s, %(unit)s, %(quantity)s, %(location)s,
                  %(min_level)s, %(reorder_qty)s, %(category)s, %(active)s)
        ON CONFLICT (part_id) DO UPDATE SET
            name = EXCLUDED.name, description = EXCLUDED.description, unit = EXCLUDED.unit,
            quantity = EXCLUDED.quantity, location = EXCLUDED.location, min_level = EXCLUDED.min_level,
            reorder_qty = EXCLUDED.reorder_qty, category = EXCLUDED.category, active = EXCLUDED.active,
            updated_at = NOW()
        """,
        payload,
    )
    conn.commit()


def _row_is_effectively_blank(row):
    # "unit" is left out on purpose: new rows default to "Nos", which used to
    # make a freshly added blank row fail validation and vanish on autosave.
    text_fields = ["part_id", "name", "description", "location"]
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


MASTER_EDITABLE_FIELDS = (
    "part_id", "name", "description", "unit", "location",
    "min_level", "reorder_qty", "category", "active",
)


def _clean_master_row(row, row_number):
    part_id = str(row.get("part_id") or "").strip()
    name = str(row.get("name") or "").strip()
    if not part_id:
        raise ValueError(f"Row {row_number}: Item Code is required")
    if not name:
        raise ValueError(f"Row {row_number}: Item name is required")
    return {
        "part_id": part_id,
        "name": name,
        "description": str(row.get("description") or "").strip(),
        "unit": str(row.get("unit") or "Nos").strip() or "Nos",
        "quantity": _coerce_non_negative_int(row.get("quantity"), "Quantity", row_number),
        "location": str(row.get("location") or "").strip(),
        "min_level": _coerce_non_negative_int(row.get("min_level"), "Min stock level", row_number),
        "reorder_qty": _coerce_non_negative_int(row.get("reorder_qty"), "Reorder quantity", row_number),
        "category": normalize_category(row.get("category")),
        "active": _coerce_active_flag(row.get("active")),
    }


def _row_id(row):
    raw = row.get("id")
    if raw in (None, ""):
        return None
    try:
        if raw != raw:  # NaN from pandas
            return None
    except Exception:
        pass
    return int(float(raw))


def save_master_table(conn, rows, original_rows=None, performed_by="manager", performed_role="manager"):
    """Save edits from the Item Master table.

    Only rows that actually changed are written. If ``original_rows`` (the
    table as it was when loaded) is given, quantity edits are applied only if
    the stock in the database still equals what the manager saw - otherwise a
    StockConflictError is raised instead of silently undoing someone's issue
    or inward. Every quantity edit is recorded in history as an 'adjust'.
    """
    if conn.info.transaction_status != TRANSACTION_STATUS_IDLE:
        conn.rollback()

    original_by_id = {}
    for row in original_rows or []:
        rid = _row_id(row)
        if rid is not None:
            original_by_id[rid] = row

    prepared_existing = []
    prepared_new = []
    seen_part_ids = set()
    incomplete_new = 0

    for row_number, row in enumerate(rows, start=1):
        row_id = _row_id(row)
        if row_id is None and _row_is_effectively_blank(row):
            continue
        if row_id is None and (not str(row.get("part_id") or "").strip() or not str(row.get("name") or "").strip()):
            # A new row the manager is still filling in - save it once it has
            # both an Item Code and a Name instead of shouting an error.
            incomplete_new += 1
            continue
        payload = _clean_master_row(row, row_number)
        if payload["part_id"] in seen_part_ids:
            raise ValueError(f"Row {row_number}: Duplicate Item Code '{payload['part_id']}'")
        seen_part_ids.add(payload["part_id"])

        if row_id is None:
            prepared_new.append(payload)
            continue

        original = original_by_id.get(row_id)
        if original is not None:
            original_clean = _clean_master_row(original, row_number)
            if original_clean == payload:
                continue  # untouched row - do not write it at all
            payload["original_quantity"] = original_clean["quantity"]
        payload["id"] = row_id
        payload["row_number"] = row_number
        prepared_existing.append(payload)

    if not prepared_existing and not prepared_new:
        return {"updated": 0, "inserted": 0, "adjusted": 0, "incomplete": incomplete_new}

    edits = []
    for payload in prepared_existing:
        has_original = "original_quantity" in payload
        edits.append({
            "id": payload["id"],
            "part_id": payload["part_id"], "name": payload["name"], "description": payload["description"],
            "unit": payload["unit"], "quantity": payload["quantity"], "location": payload["location"],
            "min_level": payload["min_level"], "reorder_qty": payload["reorder_qty"],
            "category": payload["category"], "active": payload["active"],
            "check_quantity": has_original,
            "original_quantity": payload.get("original_quantity"),
            "qty_changed": (not has_original) or payload["quantity"] != payload["original_quantity"],
        })
    for payload in prepared_new:
        edits.append({**payload, "id": None})

    # The whole save runs inside one database function: a single network
    # round trip (important when the app server is far from the database),
    # and fully atomic - either every edit is saved or none is.
    c = conn.cursor()
    try:
        c.execute(
            "SELECT inv_save_master(%s::jsonb, %s, %s) AS result",
            (json.dumps(edits), performed_by, performed_role),
        )
        result = c.fetchone()["result"]
        if not conn.autocommit:
            conn.commit()
    except psycopg2.IntegrityError as exc:
        if not conn.autocommit:
            conn.rollback()
        detail = getattr(getattr(exc, "diag", None), "message_detail", None) or str(exc).splitlines()[0]
        raise ValueError(f"That Item Code already exists - item codes must be unique. ({detail})") from None
    except psycopg2.Error as exc:
        if not conn.autocommit:
            conn.rollback()
        message = getattr(getattr(exc, "diag", None), "message_primary", None) or str(exc)
        if message.startswith("INV_CONFLICT|"):
            _, name, qty = message.split("|", 2)
            raise StockConflictError(
                f"Stock of '{name}' changed to {qty} while you were editing "
                f"(someone issued or inwarded it). Your quantity edit was not saved - "
                f"the table has been refreshed, please re-check and edit again."
            ) from None
        if message.startswith("INV_MISSING|"):
            raise ValueError(f"Item '{message.split('|', 1)[1]}' no longer exists. Press Refresh and try again.") from None
        raise
    result["incomplete"] = incomplete_new
    return result


SAVE_MASTER_FUNCTION_SQL = r"""
CREATE OR REPLACE FUNCTION inv_save_master(edits jsonb, actor text, actor_role text)
RETURNS jsonb
LANGUAGE plpgsql
AS $fn$
DECLARE
    e jsonb;
    cur parts%ROWTYPE;
    old_codes jsonb := '{}'::jsonb;
    old_code text;
    new_qty integer;
    n_updated integer := 0;
    n_inserted integer := 0;
    n_adjusted integer := 0;
BEGIN
    -- Pass 1: lock every edited row and move renamed codes out of the way
    FOR e IN SELECT value FROM jsonb_array_elements(edits) LOOP
        IF e->>'id' IS NOT NULL THEN
            SELECT * INTO cur FROM parts WHERE id = (e->>'id')::int FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'INV_MISSING|%', e->>'part_id';
            END IF;
            old_codes := old_codes || jsonb_build_object(cur.id::text, cur.part_id);
            IF cur.part_id IS DISTINCT FROM e->>'part_id' THEN
                UPDATE parts SET part_id = '__tmp__' || cur.id || '__' WHERE id = cur.id;
            END IF;
        END IF;
    END LOOP;

    -- Pass 2: apply the edits
    FOR e IN SELECT value FROM jsonb_array_elements(edits) LOOP
        IF e->>'id' IS NOT NULL THEN
            SELECT * INTO cur FROM parts WHERE id = (e->>'id')::int;
            old_code := old_codes->>(cur.id::text);
            new_qty := cur.quantity;
            IF (e->>'qty_changed')::boolean THEN
                IF (e->>'check_quantity')::boolean
                   AND cur.quantity IS DISTINCT FROM (e->>'original_quantity')::int THEN
                    RAISE EXCEPTION 'INV_CONFLICT|%|%', cur.name, cur.quantity;
                END IF;
                new_qty := (e->>'quantity')::int;
            END IF;
            UPDATE parts SET
                part_id = e->>'part_id', name = e->>'name', description = e->>'description',
                unit = e->>'unit', quantity = new_qty, location = e->>'location',
                min_level = (e->>'min_level')::int, reorder_qty = (e->>'reorder_qty')::int,
                category = e->>'category', active = (e->>'active')::int, updated_at = NOW()
            WHERE id = cur.id;
            IF old_code IS DISTINCT FROM e->>'part_id' THEN
                UPDATE transactions SET part_id = e->>'part_id' WHERE part_id = old_code;
                IF to_regclass('pr_lines') IS NOT NULL THEN
                    UPDATE pr_lines SET part_id = e->>'part_id' WHERE part_id = old_code;
                    UPDATE stock_requests SET part_id = e->>'part_id' WHERE part_id = old_code;
                END IF;
            END IF;
            IF new_qty IS DISTINCT FROM cur.quantity THEN
                n_adjusted := n_adjusted + 1;
                INSERT INTO transactions (tx_type, part_id, part_name, qty, unit, performed_by, performed_role,
                                          machine_sn, purpose, note, prev_stock, balance_stock)
                VALUES ('adjust', e->>'part_id', e->>'name', new_qty - COALESCE(cur.quantity, 0), e->>'unit',
                        actor, actor_role, '', 'stock correction', 'Quantity edited in Item Master',
                        COALESCE(cur.quantity, 0), new_qty);
            END IF;
            n_updated := n_updated + 1;
        ELSE
            INSERT INTO parts (part_id, name, description, unit, quantity, location,
                               min_level, reorder_qty, category, active)
            VALUES (e->>'part_id', e->>'name', e->>'description', e->>'unit', (e->>'quantity')::int,
                    e->>'location', (e->>'min_level')::int, (e->>'reorder_qty')::int,
                    e->>'category', (e->>'active')::int);
            IF (e->>'quantity')::int <> 0 THEN
                INSERT INTO transactions (tx_type, part_id, part_name, qty, unit, performed_by, performed_role,
                                          machine_sn, purpose, note, prev_stock, balance_stock)
                VALUES ('adjust', e->>'part_id', e->>'name', (e->>'quantity')::int, e->>'unit',
                        actor, actor_role, '', 'opening stock', 'New item added in Item Master',
                        0, (e->>'quantity')::int);
            END IF;
            n_inserted := n_inserted + 1;
        END IF;
    END LOOP;

    RETURN jsonb_build_object('updated', n_updated, 'inserted', n_inserted, 'adjusted', n_adjusted);
END;
$fn$;
"""


def _lock_part(c, part_id):
    """Fetch a part row and lock it until commit so concurrent stock moves queue up."""
    c.execute("SELECT * FROM parts WHERE part_id = %s FOR UPDATE", (part_id,))
    return c.fetchone()


@transactional
def pick_material(conn, part_id, machine_serials, qty, performed_by, performed_role, purpose, note="", returnable=False):
    try:
        conn.rollback()
    except Exception:
        pass
    if qty is None or int(qty) <= 0:
        raise ValueError("Quantity must be at least 1")
    qty = int(qty)
    machine_serials = [str(s).strip() for s in (machine_serials or []) if str(s).strip()]
    if not machine_serials:
        raise ValueError("At least one serial number is required")
    if len(machine_serials) == 1:
        machine_sn = machine_serials[0]
    elif len(machine_serials) == qty:
        machine_sn = ", ".join(machine_serials)
    else:
        raise ValueError("Number of provided serials does not match quantity")

    c = conn.cursor()
    try:
        part = _lock_part(c, part_id)
        if part is None:
            raise ValueError("Part not found")
        if not part["active"]:
            raise ValueError("Inactive items cannot be issued")
        previous_stock = int(part["quantity"] or 0)
        if previous_stock < qty:
            raise ValueError(f"Insufficient stock - only {previous_stock} {part['unit']} available")
        running_balance = previous_stock - qty
        c.execute(
            "UPDATE parts SET quantity = %s, updated_at = NOW() WHERE part_id = %s",
            (running_balance, part_id),
        )
        c.execute(
            """
            INSERT INTO transactions (
                tx_type, part_id, part_name, qty, unit, performed_by, performed_role,
                machine_sn, purpose, note, prev_stock, balance_stock, returnable
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                "issue", part_id, part["name"], qty, part["unit"], performed_by, performed_role,
                machine_sn, purpose, note, previous_stock, running_balance, 1 if returnable else 0,
            ),
        )
        conn.commit()
        return running_balance
    except Exception:
        conn.rollback()
        raise


@transactional
def deposit_stock(conn, part_id, qty, performed_by, performed_role, note=""):
    try:
        conn.rollback()
    except Exception:
        pass
    if qty is None or int(qty) <= 0:
        raise ValueError("Deposit quantity must be greater than zero")
    qty = int(qty)
    c = conn.cursor()
    try:
        part = _lock_part(c, part_id)
        if part is None:
            raise ValueError("Create the item in Item Master before depositing stock")
        previous_stock = int(part["quantity"] or 0)
        balance_stock = previous_stock + qty
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
                "deposit", part_id, part["name"], qty, part["unit"], performed_by, performed_role,
                "", "stock deposit", note, previous_stock, balance_stock,
            ),
        )
        conn.commit()
        return balance_stock
    except Exception:
        conn.rollback()
        raise


def get_transaction(conn, tx_id):
    c = conn.cursor()
    c.execute("SELECT * FROM transactions WHERE id = %s", (tx_id,))
    return c.fetchone()


@transactional
def return_issue_material(conn, issue_tx_id, performed_by, performed_role, note=""):
    try:
        conn.rollback()
    except Exception:
        pass
    if (performed_role or "").strip().lower() != "manager":
        raise ValueError("Only managers can return material")

    c = conn.cursor()
    try:
        # Lock the issue row so a double-click / two managers cannot return it twice
        c.execute("SELECT * FROM transactions WHERE id = %s FOR UPDATE", (issue_tx_id,))
        issue = c.fetchone()
        if issue is None:
            raise ValueError("Issue record not found")
        if issue["tx_type"] != "issue":
            raise ValueError("Only issue records can be returned")
        if not issue["returnable"]:
            raise ValueError("This issue was not marked as returnable")
        if issue["returned_at"]:
            raise ValueError("This material has already been returned")

        part = _lock_part(c, issue["part_id"])
        if part is None:
            raise ValueError("Part not found")

        previous_stock = int(part["quantity"] or 0)
        balance_stock = previous_stock + int(issue["qty"])
        return_note = (note or "").strip()
        if issue["machine_sn"]:
            prefix = f"Return for machine {issue['machine_sn']}"
            return_note = f"{prefix} | {return_note}" if return_note else prefix

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
                "return", issue["part_id"], issue["part_name"], int(issue["qty"]), issue["unit"],
                performed_by, performed_role, issue["machine_sn"], "returnable material returned",
                return_note, previous_stock, balance_stock, int(issue_tx_id),
            ),
        )
        return_tx_id = c.fetchone()["id"]
        c.execute(
            "UPDATE transactions SET returned_at = NOW(), returned_tx_id = %s WHERE id = %s",
            (return_tx_id, issue_tx_id),
        )
        conn.commit()
        return balance_stock
    except Exception:
        conn.rollback()
        raise


@transactional
def wipe_all_data(conn):
    c = conn.cursor()
    # TRUNCATE bypasses the "PR history is permanent" row trigger on purpose:
    # this is the explicit, code-protected full wipe.
    c.execute("TRUNCATE stock_requests, pr_events, pr_lines, purchase_requisitions, vendors, item_merges, "
              "duplicate_dismissed RESTART IDENTITY")
    c.execute("DELETE FROM transactions")
    c.execute("DELETE FROM parts")
    conn.commit()


def low_stock_alerts(conn):
    c = conn.cursor()
    c.execute("SELECT * FROM parts WHERE active = 1 AND quantity <= min_level ORDER BY quantity, name")
    return c.fetchall()


def list_transactions(conn, tx_type="all", search="", performed_by=None, limit=250, since_days=None):
    c = conn.cursor()
    sql = "SELECT * FROM transactions WHERE 1=1"
    params = []
    if since_days is not None:
        # since_days=0 means "today" in India time
        sql += (
            " AND (created_at AT TIME ZONE 'Asia/Kolkata')::date >= "
            "(NOW() AT TIME ZONE 'Asia/Kolkata')::date - %s"
        )
        params.append(int(since_days))
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
    use_category = category and category != "All"

    # Single query for all parts-level metrics
    if use_category:
        c.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE active = 1) AS total_items,
                COALESCE(SUM(quantity) FILTER (WHERE active = 1), 0) AS total_stock_units,
                COUNT(*) FILTER (WHERE active = 1 AND quantity <= min_level) AS low_stock_items,
                COUNT(*) FILTER (WHERE active = 1 AND quantity = 0) AS out_of_stock_items
            FROM parts
            WHERE category = %s
            """,
            (category,),
        )
    else:
        c.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE active = 1) AS total_items,
                COALESCE(SUM(quantity) FILTER (WHERE active = 1), 0) AS total_stock_units,
                COUNT(*) FILTER (WHERE active = 1 AND quantity <= min_level) AS low_stock_items,
                COUNT(*) FILTER (WHERE active = 1 AND quantity = 0) AS out_of_stock_items
            FROM parts
            """
        )
    parts_row = c.fetchone()

    # Single query for today's transaction metrics
    if use_category:
        c.execute(
            """
            SELECT
                COALESCE(SUM(t.qty) FILTER (WHERE t.tx_type = 'issue'), 0) AS issues_today,
                COALESCE(SUM(t.qty) FILTER (WHERE t.tx_type = 'deposit'), 0) AS deposits_today
            FROM transactions t
            JOIN parts p ON p.part_id = t.part_id
            WHERE (t.created_at AT TIME ZONE 'Asia/Kolkata')::date = (NOW() AT TIME ZONE 'Asia/Kolkata')::date
              AND p.category = %s
            """,
            (category,),
        )
    else:
        c.execute(
            """
            SELECT
                COALESCE(SUM(qty) FILTER (WHERE tx_type = 'issue'), 0) AS issues_today,
                COALESCE(SUM(qty) FILTER (WHERE tx_type = 'deposit'), 0) AS deposits_today
            FROM transactions
            WHERE (created_at AT TIME ZONE 'Asia/Kolkata')::date = (NOW() AT TIME ZONE 'Asia/Kolkata')::date
            """
        )
    tx_row = c.fetchone()

    return {
        "total_items": parts_row["total_items"],
        "total_stock_units": parts_row["total_stock_units"],
        "low_stock_items": parts_row["low_stock_items"],
        "out_of_stock_items": parts_row["out_of_stock_items"],
        "issues_today": tx_row["issues_today"],
        "deposits_today": tx_row["deposits_today"],
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


@transactional
def delete_part(conn, part_id):
    """Soft-delete: mark active=0 so history is preserved."""
    c = conn.cursor()
    c.execute(
        "UPDATE parts SET active = 0, updated_at = NOW() WHERE part_id = %s",
        (part_id,),
    )
    conn.commit()


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
        "quantity": _coerce_non_negative_int(row.get("quantity"), "Quantity", "CSV"),
        "location": str(row.get("location", "")).strip(),
        "min_level": _coerce_non_negative_int(row.get("min_level"), "Min level", "CSV"),
        "reorder_qty": _coerce_non_negative_int(row.get("reorder_qty"), "Reorder qty", "CSV"),
        "active": _coerce_active_flag(row.get("active", 1)),
        "category": normalize_category(row.get("category")),
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
        "category": normalize_category(row["category"]),
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
    "Hardware": ["screw", "bolt", "nut", "washer", "bracket", "block", "rail", "shaft", "spindle", "allen", "stud", "pin"],
    "Transmission": ["belt", "pulley", "pully", "gear", "gearbox", "ballscrew", "ball screw", "coupling", "bearing", "timing", "timming", "sprocket"],
    "Electrical": ["servo", "motor", "drive", "cable", "wire", "smps", "contactor", "relay", "mcb", "switch", "power supply"],
    "Electronics": ["pcb", "encoder", "sensor", "controller", "resistor", "capacitor", "diode", "loadcell", "load cell", "display"],
    "Metals": ["steel", "stainless", "aluminium", "aluminum", "brass", "copper", "rod", "bar", "plate", "sheet", "ms ", "en8", "en24"],
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


@transactional
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
        if current != "Others":
            suggested = current  # never overwrite a category someone already chose
        else:
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
    # Build a fast O(1) lookup — avoids one DB round-trip per CSV row
    existing_by_id = {p["part_id"]: p for p in existing_payloads}
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
                existing = existing_by_id.get(part_id)  # O(1) dict lookup — no DB query

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


@transactional
def import_parts_from_csv(conn, records, pre_analyzed_rows=None, performed_by="manager"):
    """
    Upsert parts from a list of dicts (from CSV import).
    Required keys: item_code/name or legacy part_id/name.
    Optional: description, unit, quantity, location, min_level, reorder_qty, active.

    Pass pre_analyzed_rows (from a previous analyze_parts_import call) to skip
    re-analysis and avoid redundant DB queries when the user has already reviewed
    the preview in the UI.

    Returns a summary dict with inserted/updated/unchanged/duplicate_rows/invalid_rows counts.
    """
    if pre_analyzed_rows is not None:
        rows = pre_analyzed_rows
        summary = {
            "inserted": sum(1 for e in rows if e["action"] == "insert"),
            "updated": sum(1 for e in rows if e["action"] == "update"),
            "unchanged": sum(1 for e in rows if e["action"] == "unchanged"),
            "duplicate_rows": sum(1 for e in rows if e["action"] == "duplicate"),
            "near_duplicate_rows": sum(1 for e in rows if e.get("duplicate_alert")),
            "invalid_rows": sum(1 for e in rows if e["action"] == "invalid"),
            "messages": [e["note"] for e in rows if e["action"] in {"invalid", "duplicate"} and e.get("note")],
            "rows": rows,
            "can_import": True,
            "total_rows": len(records),
        }
    else:
        summary = analyze_parts_import(conn, records)
        rows = summary["rows"]

    # Bulk upsert all insert/update rows in a single transaction — replaces
    # individual save_part() calls (each of which did a SELECT + INSERT/UPDATE +
    # sync_parts_snapshot_csv per row).
    payloads = [
        entry["payload"] for entry in rows
        if entry["action"] in {"insert", "update"} and entry["payload"] is not None
    ]
    if payloads:
        c = conn.cursor()
        try:
            c.execute(
                "SELECT part_id, quantity FROM parts WHERE part_id = ANY(%s) FOR UPDATE",
                ([p["part_id"] for p in payloads],),
            )
            current_qty = {r["part_id"]: int(r["quantity"] or 0) for r in c.fetchall()}
            part_rows = []
            history_rows = []
            for payload in payloads:
                payload["active"] = _coerce_active_flag(payload.get("active", 1))
                payload["category"] = normalize_category(payload.get("category"))
                part_rows.append((
                    payload["part_id"], payload["name"], payload["description"],
                    payload["unit"], payload["quantity"], payload["location"],
                    payload["min_level"], payload["reorder_qty"],
                    payload["category"], payload["active"],
                ))
                before = current_qty.get(payload["part_id"], 0)
                if payload["quantity"] != before:
                    history_rows.append((
                        "adjust", payload["part_id"], payload["name"], payload["quantity"] - before,
                        payload["unit"], performed_by, "manager", "",
                        "opening stock" if payload["part_id"] not in current_qty else "stock correction",
                        "CSV import", before, payload["quantity"],
                    ))
            # One network round trip per 500 rows instead of one per row.
            psycopg2.extras.execute_values(
                c,
                """
                INSERT INTO parts (
                    part_id, name, description, unit, quantity, location,
                    min_level, reorder_qty, category, active
                ) VALUES %s
                ON CONFLICT (part_id) DO UPDATE SET
                    name        = EXCLUDED.name,
                    description = EXCLUDED.description,
                    unit        = EXCLUDED.unit,
                    quantity    = EXCLUDED.quantity,
                    location    = EXCLUDED.location,
                    min_level   = EXCLUDED.min_level,
                    reorder_qty = EXCLUDED.reorder_qty,
                    category    = EXCLUDED.category,
                    active      = EXCLUDED.active,
                    updated_at  = NOW()
                """,
                part_rows,
                page_size=500,
            )
            if history_rows:
                psycopg2.extras.execute_values(
                    c,
                    """
                    INSERT INTO transactions (
                        tx_type, part_id, part_name, qty, unit, performed_by, performed_role,
                        machine_sn, purpose, note, prev_stock, balance_stock
                    ) VALUES %s
                    """,
                    history_rows,
                    page_size=500,
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return summary


# ===========================================================================
# Purchase Requisitions (PR)
# ---------------------------------------------------------------------------
# Every PR action is written to pr_events with the person's name and the
# database server's own clock (NOW()), and that log cannot be edited or deleted
# afterwards (a trigger blocks it). That log is the proof trail.
# ===========================================================================

PR_STATUS_LABELS = {
    "raised": "Raised",
    "ordered": "Ordered",
    "partial": "Part received",
    "received": "Received",
    "closed_short": "Closed (short)",
    "cancelled": "Cancelled",
}
PR_OPEN_STATUSES = ("raised", "ordered", "partial")
PR_PRIORITIES = ("Normal", "Urgent", "Breakdown")

PR_SCHEMA_SQL = r"""
CREATE TABLE IF NOT EXISTS vendors (
    id SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    phone TEXT DEFAULT '',
    email TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    active INTEGER DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS purchase_requisitions (
    id SERIAL PRIMARY KEY,
    pr_no TEXT UNIQUE,
    status TEXT NOT NULL DEFAULT 'raised',
    priority TEXT DEFAULT 'Normal',
    requester TEXT NOT NULL,
    reason TEXT DEFAULT '',
    required_by DATE,
    raised_by TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    vendor_id INTEGER REFERENCES vendors(id),
    vendor_name TEXT DEFAULT '',
    po_ref TEXT DEFAULT '',
    ordered_by TEXT DEFAULT '',
    ordered_at TIMESTAMPTZ,
    expected_date DATE,
    received_at TIMESTAMPTZ,
    closed_by TEXT DEFAULT '',
    closed_at TIMESTAMPTZ,
    close_reason TEXT DEFAULT '',
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS pr_lines (
    id SERIAL PRIMARY KEY,
    pr_id INTEGER NOT NULL REFERENCES purchase_requisitions(id),
    part_id TEXT NOT NULL,
    part_name TEXT DEFAULT '',
    unit TEXT DEFAULT 'Nos',
    qty_requested INTEGER NOT NULL CHECK (qty_requested > 0),
    qty_received INTEGER NOT NULL DEFAULT 0 CHECK (qty_received >= 0),
    rate NUMERIC(14, 2),
    stock_at_request INTEGER DEFAULT 0,
    min_level_at_request INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS pr_events (
    id SERIAL PRIMARY KEY,
    pr_id INTEGER NOT NULL REFERENCES purchase_requisitions(id),
    event TEXT NOT NULL,
    actor TEXT NOT NULL,
    detail TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS stock_requests (
    id SERIAL PRIMARY KEY,
    part_id TEXT NOT NULL,
    part_name TEXT DEFAULT '',
    unit TEXT DEFAULT 'Nos',
    qty INTEGER NOT NULL CHECK (qty > 0),
    requested_by TEXT NOT NULL,
    reason TEXT DEFAULT '',
    stock_at_request INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'open',
    pr_id INTEGER REFERENCES purchase_requisitions(id),
    handled_by TEXT DEFAULT '',
    handled_at TIMESTAMPTZ,
    handle_note TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS item_merges (
    id SERIAL PRIMARY KEY,
    kept_part_id TEXT NOT NULL,
    kept_name TEXT DEFAULT '',
    removed_part_id TEXT NOT NULL,
    removed_name TEXT DEFAULT '',
    kept_qty_before INTEGER DEFAULT 0,
    removed_qty_before INTEGER DEFAULT 0,
    final_qty INTEGER DEFAULT 0,
    moved_history_rows INTEGER DEFAULT 0,
    merged_by TEXT NOT NULL,
    note TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS duplicate_dismissed (
    part_a TEXT NOT NULL,
    part_b TEXT NOT NULL,
    dismissed_by TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (part_a, part_b)
);
CREATE INDEX IF NOT EXISTS idx_pr_lines_pr ON pr_lines(pr_id);
CREATE INDEX IF NOT EXISTS idx_pr_lines_part ON pr_lines(part_id);
CREATE INDEX IF NOT EXISTS idx_pr_events_pr ON pr_events(pr_id);
CREATE INDEX IF NOT EXISTS idx_pr_status ON purchase_requisitions(status);

CREATE OR REPLACE FUNCTION inv_pr_events_readonly() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    RAISE EXCEPTION 'PR history is permanent and cannot be changed or deleted';
END;
$fn$;
DROP TRIGGER IF EXISTS trg_pr_events_readonly ON pr_events;
CREATE TRIGGER trg_pr_events_readonly
    BEFORE UPDATE OR DELETE ON pr_events
    FOR EACH ROW EXECUTE FUNCTION inv_pr_events_readonly();
"""


def init_pr_schema(conn):
    c = conn.cursor()
    c.execute(PR_SCHEMA_SQL)
    if not conn.autocommit:
        conn.commit()


def _clean_name(value, label):
    text = " ".join(str(value or "").split())
    if len(text) < 2:
        raise ValueError(f"{label} is required (this is recorded as proof).")
    return text[:80]


def _pr_event(c, pr_id, event, actor, detail=""):
    c.execute(
        "INSERT INTO pr_events (pr_id, event, actor, detail) VALUES (%s, %s, %s, %s)",
        (pr_id, event, actor, detail),
    )


def _lock_pr(c, pr_id):
    c.execute("SELECT * FROM purchase_requisitions WHERE id = %s FOR UPDATE", (pr_id,))
    pr = c.fetchone()
    if pr is None:
        raise ValueError("PR not found")
    return pr


def _vendor_id(c, vendor_name):
    name = " ".join(str(vendor_name or "").split())
    if not name:
        return None, ""
    c.execute(
        """
        INSERT INTO vendors (name) VALUES (%s)
        ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
        RETURNING id, name
        """,
        (name,),
    )
    row = c.fetchone()
    return row["id"], row["name"]


@transactional
def create_pr(conn, lines, requester, required_by, priority="Normal", reason="",
              raised_by="manager", request_ids=None):
    """Raise a PR. lines = [{"part_id": ..., "qty": ...}, ...]. Returns the PR number."""
    requester = _clean_name(requester, "Requester name")
    if required_by is None:
        raise ValueError("Required-by date is required.")
    if priority not in PR_PRIORITIES:
        priority = "Normal"
    merged = {}
    for line in lines or []:
        pid = str(line.get("part_id") or "").strip()
        qty = int(line.get("qty") or 0)
        if pid and qty > 0:
            merged[pid] = merged.get(pid, 0) + qty
    if not merged:
        raise ValueError("Add at least one item with a quantity above zero.")

    c = conn.cursor()
    try:
        c.execute("SELECT * FROM parts WHERE part_id = ANY(%s)", (list(merged),))
        parts = {r["part_id"]: r for r in c.fetchall()}
        missing = [p for p in merged if p not in parts]
        if missing:
            raise ValueError(f"Item not found: {', '.join(missing)}")

        # Serialise numbering so two PRs raised at the same moment never share a number.
        c.execute("SELECT pg_advisory_xact_lock(72011)")
        c.execute(
            """
            SELECT COUNT(*) AS n FROM purchase_requisitions
            WHERE date_part('year', created_at AT TIME ZONE 'Asia/Kolkata')
                = date_part('year', NOW() AT TIME ZONE 'Asia/Kolkata')
            """
        )
        seq = c.fetchone()["n"] + 1
        c.execute("SELECT to_char(NOW() AT TIME ZONE 'Asia/Kolkata', 'YYYY') AS y")
        pr_no = f"PR-{c.fetchone()['y']}-{seq:04d}"

        c.execute(
            """
            INSERT INTO purchase_requisitions (pr_no, status, priority, requester, reason, required_by, raised_by)
            VALUES (%s, 'raised', %s, %s, %s, %s, %s) RETURNING id
            """,
            (pr_no, priority, requester, str(reason or "").strip(), required_by, raised_by),
        )
        pr_id = c.fetchone()["id"]
        summary = []
        for pid, qty in merged.items():
            p = parts[pid]
            c.execute(
                """
                INSERT INTO pr_lines (pr_id, part_id, part_name, unit, qty_requested,
                                      stock_at_request, min_level_at_request)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (pr_id, pid, p["name"], p["unit"], qty, int(p["quantity"] or 0), int(p["min_level"] or 0)),
            )
            summary.append(f"{p['name']} × {qty}")
        _pr_event(
            c, pr_id, "raised", requester,
            f"Raised ({priority}), required by {required_by:%d-%b-%Y}. Items: " + "; ".join(summary)
            + (f". Reason: {reason.strip()}" if str(reason or "").strip() else ""),
        )
        if request_ids:
            c.execute(
                """
                UPDATE stock_requests SET status = 'converted', pr_id = %s, handled_by = %s,
                       handled_at = NOW(), handle_note = %s
                WHERE id = ANY(%s) AND status = 'open'
                RETURNING requested_by, part_name, qty
                """,
                (pr_id, raised_by, f"Converted to {pr_no}", list(request_ids)),
            )
            for r in c.fetchall():
                _pr_event(c, pr_id, "note", requester,
                          f"Includes request from {r['requested_by']}: {r['part_name']} × {r['qty']}")
        conn.commit()
        return pr_no
    except Exception:
        conn.rollback()
        raise


@transactional
def mark_pr_ordered(conn, pr_id, ordered_by, expected_date, vendor_name="", po_ref="", rates=None, note=""):
    ordered_by = _clean_name(ordered_by, "Ordered-by name")
    if expected_date is None:
        raise ValueError("Expected delivery date is required.")
    c = conn.cursor()
    try:
        pr = _lock_pr(c, pr_id)
        if pr["status"] != "raised":
            raise ValueError(f"{pr['pr_no']} is already {PR_STATUS_LABELS.get(pr['status'], pr['status'])}.")
        vid, vname = _vendor_id(c, vendor_name)
        c.execute(
            """
            UPDATE purchase_requisitions
            SET status = 'ordered', vendor_id = %s, vendor_name = %s, po_ref = %s, ordered_by = %s,
                ordered_at = NOW(), expected_date = %s, updated_at = NOW()
            WHERE id = %s
            """,
            (vid, vname, str(po_ref or "").strip(), ordered_by, expected_date, pr_id),
        )
        for line_id, rate in (rates or {}).items():
            if rate not in (None, "") and float(rate) > 0:
                c.execute("UPDATE pr_lines SET rate = %s WHERE id = %s AND pr_id = %s", (float(rate), int(line_id), pr_id))
        bits = [f"Ordered, expected {expected_date:%d-%b-%Y}"]
        if vname:
            bits.append(f"vendor {vname}")
        if str(po_ref or "").strip():
            bits.append(f"PO/ref {str(po_ref).strip()}")
        if str(note or "").strip():
            bits.append(str(note).strip())
        _pr_event(c, pr_id, "ordered", ordered_by, " · ".join(bits))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


@transactional
def receive_pr(conn, pr_id, receipts, received_by, grn_note=""):
    """Receive goods against a PR. receipts = {line_id: qty}. Stock goes up and Inward history is written."""
    received_by = _clean_name(received_by, "Received-by name")
    grn_note = str(grn_note or "").strip()
    if not grn_note:
        raise ValueError("GRN / challan / invoice reference is required.")
    receipts = {int(k): int(v or 0) for k, v in (receipts or {}).items() if int(v or 0) > 0}
    if not receipts:
        raise ValueError("Enter the quantity received for at least one item.")
    c = conn.cursor()
    try:
        pr = _lock_pr(c, pr_id)
        if pr["status"] not in ("raised", "ordered", "partial"):
            raise ValueError(f"{pr['pr_no']} is {PR_STATUS_LABELS.get(pr['status'], pr['status'])} — nothing to receive.")
        c.execute("SELECT * FROM pr_lines WHERE pr_id = %s ORDER BY id FOR UPDATE", (pr_id,))
        lines = {r["id"]: r for r in c.fetchall()}
        summary = []
        for line_id, qty in receipts.items():
            line = lines.get(line_id)
            if line is None:
                raise ValueError("Item line not found on this PR.")
            pending = line["qty_requested"] - line["qty_received"]
            if qty > pending:
                raise ValueError(
                    f"{line['part_name']}: receiving {qty} but only {pending} pending on {pr['pr_no']}. "
                    "Use Inward for extra quantity."
                )
            part = _lock_part(c, line["part_id"])
            if part is None:
                raise ValueError(f"Item {line['part_id']} no longer exists.")
            before = int(part["quantity"] or 0)
            after = before + qty
            c.execute("UPDATE parts SET quantity = %s, updated_at = NOW() WHERE part_id = %s", (after, line["part_id"]))
            c.execute(
                """
                INSERT INTO transactions (tx_type, part_id, part_name, qty, unit, performed_by, performed_role,
                                          machine_sn, purpose, note, prev_stock, balance_stock)
                VALUES ('deposit', %s, %s, %s, %s, %s, 'manager', '', %s, %s, %s, %s)
                """,
                (line["part_id"], part["name"], qty, part["unit"], received_by, f"PR receipt {pr['pr_no']}",
                 f"{pr['pr_no']} · {grn_note}", before, after),
            )
            c.execute("UPDATE pr_lines SET qty_received = qty_received + %s WHERE id = %s", (qty, line_id))
            line["qty_received"] += qty
            summary.append(f"{line['part_name']} × {qty}")
        all_done = all(l["qty_received"] >= l["qty_requested"] for l in lines.values())
        new_status = "received" if all_done else "partial"
        c.execute(
            """
            UPDATE purchase_requisitions SET status = %s, updated_at = NOW(),
                received_at = CASE WHEN %s THEN NOW() ELSE received_at END,
                closed_at = CASE WHEN %s THEN NOW() ELSE closed_at END,
                closed_by = CASE WHEN %s THEN %s ELSE closed_by END
            WHERE id = %s
            """,
            (new_status, all_done, all_done, all_done, received_by, pr_id),
        )
        pending_txt = "" if all_done else " · still pending: " + "; ".join(
            f"{l['part_name']} × {l['qty_requested'] - l['qty_received']}"
            for l in lines.values() if l["qty_received"] < l["qty_requested"]
        )
        _pr_event(c, pr_id, "received", received_by,
                  f"Received {'; '.join(summary)} · GRN {grn_note}"
                  + (" · PR complete" if all_done else pending_txt))
        conn.commit()
        return new_status
    except Exception:
        conn.rollback()
        raise


@transactional
def close_pr(conn, pr_id, actor, reason, cancel=False):
    """Cancel (nothing received yet) or close short (stop waiting for the rest)."""
    actor = _clean_name(actor, "Your name")
    reason = str(reason or "").strip()
    if len(reason) < 3:
        raise ValueError("A reason is required.")
    c = conn.cursor()
    try:
        pr = _lock_pr(c, pr_id)
        if pr["status"] not in PR_OPEN_STATUSES:
            raise ValueError(f"{pr['pr_no']} is already {PR_STATUS_LABELS.get(pr['status'], pr['status'])}.")
        c.execute("SELECT COALESCE(SUM(qty_received), 0) AS r FROM pr_lines WHERE pr_id = %s", (pr_id,))
        received_any = c.fetchone()["r"] > 0
        if cancel and received_any:
            raise ValueError("Some goods were already received — use 'Close short' instead of cancel.")
        status = "cancelled" if cancel else "closed_short"
        c.execute(
            """
            UPDATE purchase_requisitions SET status = %s, closed_by = %s, closed_at = NOW(),
                   close_reason = %s, updated_at = NOW()
            WHERE id = %s
            """,
            (status, actor, reason, pr_id),
        )
        _pr_event(c, pr_id, status, actor, reason)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


@transactional
def update_pr_expected_date(conn, pr_id, actor, new_date, reason):
    actor = _clean_name(actor, "Your name")
    reason = str(reason or "").strip()
    if len(reason) < 3:
        raise ValueError("Give the reason for the new date (e.g. vendor said dispatch delayed).")
    c = conn.cursor()
    try:
        pr = _lock_pr(c, pr_id)
        if pr["status"] not in ("ordered", "partial"):
            raise ValueError("Expected date can be changed only after the PR is ordered.")
        old = pr["expected_date"]
        c.execute("UPDATE purchase_requisitions SET expected_date = %s, updated_at = NOW() WHERE id = %s",
                  (new_date, pr_id))
        _pr_event(c, pr_id, "date_changed", actor,
                  f"Expected date {old:%d-%b-%Y} → {new_date:%d-%b-%Y} · {reason}" if old else
                  f"Expected date set to {new_date:%d-%b-%Y} · {reason}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


@transactional
def add_pr_note(conn, pr_id, actor, note):
    actor = _clean_name(actor, "Your name")
    note = str(note or "").strip()
    if not note:
        raise ValueError("Write the note first.")
    c = conn.cursor()
    try:
        _lock_pr(c, pr_id)
        _pr_event(c, pr_id, "note", actor, note)
        c.execute("UPDATE purchase_requisitions SET updated_at = NOW() WHERE id = %s", (pr_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def list_prs(conn, statuses=None, search="", limit=500):
    c = conn.cursor()
    sql = """
        SELECT pr.*,
               COUNT(l.id) AS line_count,
               COALESCE(SUM(l.qty_requested), 0) AS qty_requested,
               COALESCE(SUM(l.qty_received), 0) AS qty_received,
               COALESCE(SUM(l.rate * l.qty_requested), 0) AS value,
               string_agg(l.part_name || ' × ' || l.qty_requested, '; ' ORDER BY l.id) AS items,
               (NOW() AT TIME ZONE 'Asia/Kolkata')::date - (pr.created_at AT TIME ZONE 'Asia/Kolkata')::date AS days_open
        FROM purchase_requisitions pr
        LEFT JOIN pr_lines l ON l.pr_id = pr.id
        WHERE 1=1
    """
    params = []
    if statuses:
        sql += " AND pr.status = ANY(%s)"
        params.append(list(statuses))
    if search:
        like = f"%{search}%"
        sql += """ AND (pr.pr_no ILIKE %s OR pr.requester ILIKE %s OR pr.vendor_name ILIKE %s OR pr.po_ref ILIKE %s
                   OR EXISTS (SELECT 1 FROM pr_lines x WHERE x.pr_id = pr.id
                              AND (x.part_name ILIKE %s OR x.part_id ILIKE %s)))"""
        params.extend([like] * 6)
    sql += " GROUP BY pr.id ORDER BY pr.created_at DESC LIMIT %s"
    params.append(limit)
    c.execute(sql, params)
    return c.fetchall()


def get_pr_detail(conn, pr_id):
    c = conn.cursor()
    c.execute("SELECT * FROM purchase_requisitions WHERE id = %s", (pr_id,))
    pr = c.fetchone()
    if pr is None:
        return None
    c.execute(
        """
        SELECT l.*, p.quantity AS stock_now, COALESCE(p.description, '') AS description
        FROM pr_lines l LEFT JOIN parts p ON p.part_id = l.part_id
        WHERE l.pr_id = %s ORDER BY l.id
        """,
        (pr_id,),
    )
    lines = c.fetchall()
    c.execute("SELECT * FROM pr_events WHERE pr_id = %s ORDER BY created_at, id", (pr_id,))
    events = c.fetchall()
    return {"pr": dict(pr), "lines": [dict(l) for l in lines], "events": [dict(e) for e in events]}


def on_order_map(conn):
    """part_id -> {"qty": pending qty, "prs": [(pr_no, due_date)]} for every open PR."""
    c = conn.cursor()
    c.execute(
        """
        SELECT l.part_id, pr.pr_no, COALESCE(pr.expected_date, pr.required_by) AS due,
               l.qty_requested - l.qty_received AS pending
        FROM pr_lines l JOIN purchase_requisitions pr ON pr.id = l.pr_id
        WHERE pr.status IN ('raised', 'ordered', 'partial') AND l.qty_requested > l.qty_received
        ORDER BY due NULLS LAST
        """
    )
    result = {}
    for r in c.fetchall():
        entry = result.setdefault(r["part_id"], {"qty": 0, "prs": []})
        entry["qty"] += int(r["pending"])
        entry["prs"].append((r["pr_no"], r["due"]))
    return result


def item_purchase_history(conn, part_ids):
    """Last rate, vendor and lead time per item (from received PRs)."""
    if not part_ids:
        return {}
    c = conn.cursor()
    c.execute(
        """
        SELECT DISTINCT ON (l.part_id) l.part_id, l.rate, pr.vendor_name, pr.pr_no,
               (pr.received_at AT TIME ZONE 'Asia/Kolkata')::date
                 - (pr.ordered_at AT TIME ZONE 'Asia/Kolkata')::date AS lead_days,
               pr.ordered_at
        FROM pr_lines l JOIN purchase_requisitions pr ON pr.id = l.pr_id
        WHERE l.part_id = ANY(%s) AND pr.ordered_at IS NOT NULL
        ORDER BY l.part_id, pr.ordered_at DESC
        """,
        (list(part_ids),),
    )
    return {r["part_id"]: dict(r) for r in c.fetchall()}


def vendor_report(conn):
    c = conn.cursor()
    c.execute(
        """
        SELECT pr.vendor_name AS vendor,
               COUNT(*) AS orders,
               COUNT(*) FILTER (WHERE pr.status IN ('ordered', 'partial')) AS open_orders,
               COUNT(*) FILTER (WHERE pr.received_at IS NOT NULL) AS completed,
               COUNT(*) FILTER (WHERE pr.received_at IS NOT NULL
                                AND (pr.received_at AT TIME ZONE 'Asia/Kolkata')::date <= pr.expected_date) AS on_time,
               COUNT(*) FILTER (WHERE pr.status IN ('ordered', 'partial')
                                AND pr.expected_date < (NOW() AT TIME ZONE 'Asia/Kolkata')::date) AS overdue_now,
               ROUND(AVG((pr.received_at AT TIME ZONE 'Asia/Kolkata')::date
                         - (pr.ordered_at AT TIME ZONE 'Asia/Kolkata')::date)
                     FILTER (WHERE pr.received_at IS NOT NULL), 1) AS avg_lead_days,
               MAX(pr.ordered_at) AS last_order
        FROM purchase_requisitions pr
        WHERE pr.ordered_at IS NOT NULL AND COALESCE(pr.vendor_name, '') <> ''
        GROUP BY pr.vendor_name
        ORDER BY orders DESC, vendor
        """
    )
    return c.fetchall()


def list_vendors(conn):
    c = conn.cursor()
    c.execute("SELECT * FROM vendors ORDER BY name")
    return c.fetchall()


@transactional
def save_vendor(conn, name, phone="", email="", notes="", vendor_id=None):
    name = " ".join(str(name or "").split())
    if not name:
        raise ValueError("Vendor name is required.")
    c = conn.cursor()
    try:
        if vendor_id:
            c.execute("UPDATE vendors SET name = %s, phone = %s, email = %s, notes = %s WHERE id = %s",
                      (name, phone, email, notes, vendor_id))
        else:
            c.execute(
                """
                INSERT INTO vendors (name, phone, email, notes) VALUES (%s, %s, %s, %s)
                ON CONFLICT (name) DO UPDATE SET phone = EXCLUDED.phone, email = EXCLUDED.email, notes = EXCLUDED.notes
                """,
                (name, phone, email, notes),
            )
        conn.commit()
    except psycopg2.IntegrityError:
        conn.rollback()
        raise ValueError("A vendor with that name already exists.") from None
    except Exception:
        conn.rollback()
        raise


# ── Operator stock requests ────────────────────────────────────────────────
@transactional
def create_stock_request(conn, part_id, qty, requested_by, reason=""):
    requested_by = _clean_name(requested_by, "Your name")
    qty = int(qty or 0)
    if qty <= 0:
        raise ValueError("Quantity must be at least 1.")
    c = conn.cursor()
    try:
        part = get_part(conn, part_id)
        if part is None:
            raise ValueError("Item not found.")
        c.execute(
            """
            SELECT requested_by, created_at FROM stock_requests
            WHERE part_id = %s AND status = 'open' ORDER BY created_at LIMIT 1
            """,
            (part_id,),
        )
        already = c.fetchone()
        if already:
            when = already["created_at"].astimezone(ZoneInfo(APP_TIMEZONE)).strftime("%d-%b %H:%M")
            raise ValueError(f"Already requested by {already['requested_by']} on {when} — the manager has it.")
        c.execute(
            """
            INSERT INTO stock_requests (part_id, part_name, unit, qty, requested_by, reason, stock_at_request)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (part_id, part["name"], part["unit"], qty, requested_by, str(reason or "").strip(),
             int(part["quantity"] or 0)),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def list_stock_requests(conn, status="open"):
    c = conn.cursor()
    c.execute(
        """
        SELECT r.*, p.quantity AS stock_now, p.reorder_qty
        FROM stock_requests r LEFT JOIN parts p ON p.part_id = r.part_id
        WHERE r.status = %s ORDER BY r.created_at
        """,
        (status,),
    )
    return c.fetchall()


@transactional
def dismiss_stock_request(conn, request_id, actor, note):
    actor = _clean_name(actor, "Your name")
    if len(str(note or "").strip()) < 3:
        raise ValueError("Give a reason for dismissing the request.")
    c = conn.cursor()
    c.execute(
        """
        UPDATE stock_requests SET status = 'dismissed', handled_by = %s, handled_at = NOW(), handle_note = %s
        WHERE id = %s AND status = 'open'
        """,
        (actor, str(note).strip(), request_id),
    )
    conn.commit()


def list_pending_pr_lines(conn):
    """Every item still awaited on an open PR, with the dates needed to chase it."""
    c = conn.cursor()
    c.execute(
        """
        SELECT pr.id AS pr_id, pr.pr_no, pr.status, pr.priority, pr.requester, pr.created_at,
               pr.required_by, pr.expected_date, pr.ordered_at, pr.vendor_name, pr.po_ref,
               l.part_id, l.part_name, l.unit, l.qty_requested, l.qty_received,
               l.qty_requested - l.qty_received AS pending,
               p.quantity AS stock_now,
               (NOW() AT TIME ZONE 'Asia/Kolkata')::date
                 - COALESCE(pr.expected_date, pr.required_by) AS days_late,
               (NOW() AT TIME ZONE 'Asia/Kolkata')::date
                 - (pr.created_at AT TIME ZONE 'Asia/Kolkata')::date AS days_open
        FROM pr_lines l
        JOIN purchase_requisitions pr ON pr.id = l.pr_id
        LEFT JOIN parts p ON p.part_id = l.part_id
        WHERE pr.status IN ('raised', 'ordered', 'partial') AND l.qty_requested > l.qty_received
        ORDER BY COALESCE(pr.expected_date, pr.required_by) NULLS LAST, pr.pr_no, l.id
        """
    )
    return c.fetchall()



# ── Duplicate items: merge & dismiss ───────────────────────────────────────
@transactional
def merge_parts(conn, keep_part_id, remove_part_ids, final_qty, actor, note=""):
    """Merge duplicate items into one.

    All history, PR lines and requests of the removed items move to the kept
    item, the kept item's stock is set to ``final_qty`` (recorded in History
    as a 'duplicate merge' correction), and the removed items are switched off
    (never deleted) with a note saying where they went.
    """
    actor = _clean_name(actor, "Your name")
    remove_part_ids = [p for p in dict.fromkeys(remove_part_ids or []) if p and p != keep_part_id]
    if not remove_part_ids:
        raise ValueError("Pick at least one duplicate to merge.")
    final_qty = int(final_qty)
    if final_qty < 0:
        raise ValueError("Stock after merge cannot be negative.")
    c = conn.cursor()
    try:
        c.execute(
            "SELECT * FROM parts WHERE part_id = ANY(%s) ORDER BY id FOR UPDATE",
            ([keep_part_id] + remove_part_ids,),
        )
        rows = {r["part_id"]: r for r in c.fetchall()}
        keep = rows.get(keep_part_id)
        if keep is None:
            raise ValueError("The item to keep no longer exists.")
        missing = [p for p in remove_part_ids if p not in rows]
        if missing:
            raise ValueError(f"Item not found: {', '.join(missing)}")

        keep_before = int(keep["quantity"] or 0)
        stamp = "NOW() AT TIME ZONE 'Asia/Kolkata'"
        merged_names = []
        for pid in remove_part_ids:
            r = rows[pid]
            qty = int(r["quantity"] or 0)
            c.execute("UPDATE transactions SET part_id = %s WHERE part_id = %s", (keep_part_id, pid))
            moved = c.rowcount
            c.execute("UPDATE pr_lines SET part_id = %s WHERE part_id = %s", (keep_part_id, pid))
            c.execute("UPDATE stock_requests SET part_id = %s WHERE part_id = %s", (keep_part_id, pid))
            c.execute(
                f"""
                UPDATE parts SET active = 0, quantity = 0, updated_at = NOW(),
                       description = '[MERGED into ' || %s || ' on ' || to_char({stamp}, 'DD-Mon-YYYY') ||
                                     ' by ' || %s || '] ' || COALESCE(description, '')
                WHERE part_id = %s
                """,
                (keep_part_id, actor, pid),
            )
            if qty:
                c.execute(
                    """
                    INSERT INTO transactions (tx_type, part_id, part_name, qty, unit, performed_by, performed_role,
                                              machine_sn, purpose, note, prev_stock, balance_stock)
                    VALUES ('adjust', %s, %s, %s, %s, %s, 'manager', '', 'duplicate merge', %s, %s, 0)
                    """,
                    (pid, r["name"], -qty, r["unit"], actor, f"Merged into {keep_part_id} ({keep['name']})", qty),
                )
            c.execute(
                """
                INSERT INTO item_merges (kept_part_id, kept_name, removed_part_id, removed_name, kept_qty_before,
                                         removed_qty_before, final_qty, moved_history_rows, merged_by, note)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (keep_part_id, keep["name"], pid, r["name"], keep_before, qty, final_qty, moved, actor,
                 str(note or "").strip()),
            )
            merged_names.append(f"{pid} ({r['name']}, stock {qty})")

        # fill gaps on the kept item from the duplicates
        location = keep["location"] or next((rows[p]["location"] for p in remove_part_ids if rows[p]["location"]), "")
        min_level = max([int(keep["min_level"] or 0)] + [int(rows[p]["min_level"] or 0) for p in remove_part_ids])
        reorder = max([int(keep["reorder_qty"] or 0)] + [int(rows[p]["reorder_qty"] or 0) for p in remove_part_ids])
        c.execute(
            """
            UPDATE parts SET quantity = %s, location = %s, min_level = %s, reorder_qty = %s, active = 1,
                   updated_at = NOW()
            WHERE part_id = %s
            """,
            (final_qty, location, min_level, reorder, keep_part_id),
        )
        c.execute(
            """
            INSERT INTO transactions (tx_type, part_id, part_name, qty, unit, performed_by, performed_role,
                                      machine_sn, purpose, note, prev_stock, balance_stock)
            VALUES ('adjust', %s, %s, %s, %s, %s, 'manager', '', 'duplicate merge', %s, %s, %s)
            """,
            (keep_part_id, keep["name"], final_qty - keep_before, keep["unit"], actor,
             "Merged duplicates: " + "; ".join(merged_names) + (f". {note.strip()}" if str(note or "").strip() else ""),
             keep_before, final_qty),
        )
        conn.commit()
        return {"kept": keep_part_id, "removed": remove_part_ids, "final_qty": final_qty}
    except Exception:
        conn.rollback()
        raise


@transactional
def dismiss_duplicate(conn, part_a, part_b, actor=""):
    a, b = sorted([str(part_a), str(part_b)])
    c = conn.cursor()
    c.execute(
        "INSERT INTO duplicate_dismissed (part_a, part_b, dismissed_by) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
        (a, b, str(actor or "")),
    )
    conn.commit()


def list_dismissed_duplicates(conn):
    c = conn.cursor()
    c.execute("SELECT part_a, part_b FROM duplicate_dismissed")
    return {(r["part_a"], r["part_b"]) for r in c.fetchall()}


def list_item_merges(conn, limit=200):
    c = conn.cursor()
    c.execute("SELECT * FROM item_merges ORDER BY created_at DESC LIMIT %s", (limit,))
    return c.fetchall()
