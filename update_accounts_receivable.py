import sqlite3

# 1. Define your desired column order and new columns
columns = [
    ('invoice_number', 'TEXT'),
    ('invoice_date', 'TEXT'),
    ('due_date', 'TEXT'),
    ('company_name', 'TEXT'),
    ('phone', 'TEXT'),
    ('product', 'TEXT'),
    ('description', 'TEXT'),
    ('units', 'INTEGER'),
    ('price', 'REAL'),
    ('discount', 'REAL'),
    ('subtotal', 'REAL'),
    ('vat', 'REAL'),
    ('total_amount', 'REAL'),
    ('current_balance', 'REAL'),
    ('paid_status', 'TEXT')
]

db_file = 'customer.db'

def column_exists(cur, table, column):
    cur.execute(f"PRAGMA table_info({table})")
    return column in [row[1] for row in cur.fetchall()]

with sqlite3.connect(db_file) as conn:
    cur = conn.cursor()

    # 2. Add any missing columns to existing table
    for col_name, col_type in columns:
        if not column_exists(cur, 'accounts_receivable', col_name):
            print(f"Adding missing column: {col_name}")
            cur.execute(f'ALTER TABLE accounts_receivable ADD COLUMN {col_name} {col_type}')

    # 3. Create new table with desired order
    col_defs = ',\n    '.join([f'{name} {dtype}' for name, dtype in columns])
    col_names = ', '.join([c[0] for c in columns])

    cur.execute(f'''
        CREATE TABLE IF NOT EXISTS accounts_receivable_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            {col_defs}
        )
    ''')

    # 4. Copy data into new table (order must match!)
    existing_cols = [row[1] for row in cur.execute("PRAGMA table_info(accounts_receivable)").fetchall()]
    copy_cols = [c[0] for c in columns if c[0] in existing_cols]

    insert_cols = ', '.join(copy_cols)
    select_cols = ', '.join(copy_cols)

    cur.execute(f'''
        INSERT INTO accounts_receivable_new ({insert_cols})
        SELECT {select_cols}
        FROM accounts_receivable
    ''')

    conn.commit()

    # 5. Drop old table and rename new one
    cur.execute('DROP TABLE accounts_receivable')
    cur.execute('ALTER TABLE accounts_receivable_new RENAME TO accounts_receivable')

    conn.commit()

print("Done! Your accounts_receivable table is updated and reordered.")
