
import sqlite3
from sqlite3 import Connection

DB_PATH = "inventory.db"

def get_conn() -> Connection:
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
            ("Ballscrew-R25","Ballscrew R25-5-890-05.05 | Make : Hiwin",1,"Shelf 1"),
            ("NutR32","R32-10T3-FSI | Make : Hiwin",1,"Shelf 1"),
            ("Ballscrew-R32","R32-10-1340-1340-0.05 | Make : Hiwin",2,"Shelf 2"),
            ("Ballscrew-R40","R40-10-1340-1340-0.05 | Make : Hiwin",8,"Shelf 2"),
            ("Ballscrew-R50","R50-10-1600-0.05 | Make : Hiwin",4,"Shelf 3"),
        ]
        c.executemany("INSERT INTO parts (part_id, name, quantity, location) VALUES (?, ?, ?, ?)", parts)
        conn.commit()
