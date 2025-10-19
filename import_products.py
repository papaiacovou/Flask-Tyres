import sqlite3
import pandas as pd
import math
from pathlib import Path

# ------------------ CONFIG ------------------
EXCEL_PATH = r"C:\flask_project\products.xlsx"
DB_PATH    = r"C:\flask_project\customer.db"
SHEET_NAME = None  # leave None to auto-pick first sheet

# ------------------ HELPERS ------------------
def clean_cell(v):
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    s = str(v).strip()
    return s if s else None

def normalize_key(s):
    """Normalize for uniqueness: trim, collapse spaces, uppercase."""
    if s is None:
        return None
    s = " ".join(str(s).strip().split())
    return s.upper() if s else None

def pick_column(df, *candidates):
    cols_lower = {c.lower().strip(): c for c in df.columns}
    for cand in candidates:
        key = cand.lower().strip()
        if key in cols_lower:
            return cols_lower[key]
    return None

# ------------------ LOAD EXCEL ------------------
excel_file = Path(EXCEL_PATH)
if not excel_file.exists():
    raise FileNotFoundError(f"Excel not found: {EXCEL_PATH}")

if SHEET_NAME is None:
    xls = pd.ExcelFile(EXCEL_PATH)
    if not xls.sheet_names:
        raise ValueError("No sheets found in Excel.")
    SHEET_NAME = xls.sheet_names[0]
    print(f"Auto-detected first sheet: {SHEET_NAME}")

df = pd.read_excel(EXCEL_PATH, sheet_name=SHEET_NAME)
print("Excel loaded.")
print("Columns:", list(df.columns))
print("Row count:", len(df))
if df.empty:
    raise ValueError("DataFrame is empty — check the sheet name and file contents.")

# Resolve columns (case-insensitive)
col_tyres    = pick_column(df, "Tyres", "Tyre")
col_services = pick_column(df, "Services", "Service")
col_size     = pick_column(df, "Size")

missing = [name for name, col in {
    "Tyres": col_tyres, "Services": col_services, "Size": col_size
}.items() if col is None]
if missing:
    raise KeyError(f"Missing columns in Excel: {missing}")

# Build cleaned DataFrame (display cols) + normalized keys for dedupe
df_use = df[[col_tyres, col_services, col_size]].copy()
df_use.columns = ["tyres", "services", "size"]

for c in ["tyres", "services", "size"]:
    df_use[c] = df_use[c].apply(clean_cell)

df_use["tyres_norm"]    = df_use["tyres"].apply(normalize_key)
df_use["services_norm"] = df_use["services"].apply(normalize_key)
df_use["size_norm"]     = df_use["size"].apply(normalize_key)

# Drop rows that are completely empty after cleaning
before_clean = len(df_use)
df_use = df_use.dropna(how="all", subset=["tyres", "services", "size"])
print(f"Rows after cleaning empties: {len(df_use)} (from {before_clean})")

if df_use.empty:
    raise ValueError("No usable rows after cleaning. Check your data.")

# Deduplicate by normalized triple; keep the **last** occurrence from Excel
df_use = df_use.drop_duplicates(subset=["tyres_norm", "services_norm", "size_norm"], keep="last")

# Final records to insert (preserve original display text)
records = list(df_use[["tyres", "services", "size"]].itertuples(index=False, name=None))
print(f"Prepared rows to insert (after dedupe): {len(records)}")

# ------------------ DB WORK (FULL REFRESH) ------------------
conn = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
cur = conn.cursor()
cur.execute("PRAGMA journal_mode=WAL;")
cur.execute("PRAGMA busy_timeout=15000;")
cur.execute("PRAGMA foreign_keys=ON;")

# Ensure table exists with expected schema
cur.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tyres TEXT,
        services TEXT,
        size TEXT
    )
""")

# FULL REFRESH: clear table and insert anew
cur.execute("DELETE FROM products")

if records:
    cur.executemany("""
        INSERT INTO products (tyres, services, size)
        VALUES (?, ?, ?)
    """, records)

# Stats
cur.execute("SELECT COUNT(*) FROM products")
count_after = cur.fetchone()[0]
print(f"Inserted: {count_after} rows (full refresh).")

# Sample a few rows
cur.execute("""
    SELECT id, tyres, services, size
      FROM products
     ORDER BY id DESC
     LIMIT 5
""")
rows = cur.fetchall()
if rows:
    print("Sample rows (latest first):")
    for r in rows:
        print(r)

conn.close()
print("Done: products table fully refreshed from Excel.")
