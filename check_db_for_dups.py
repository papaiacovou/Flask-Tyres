import sqlite3
from contextlib import closing

DATABASE_FILE = "customer.db"

def db_connect():
    return sqlite3.connect(DATABASE_FILE)

with closing(db_connect()) as conn:
    cur = conn.cursor()
    # Normalize fields (trim & uppercase)
    print("Normalizing inventory fields...")
    cur.execute("""
        UPDATE product_inventory
        SET 
            "Inventory ID" = TRIM(UPPER("Inventory ID")),
            "Company" = TRIM(UPPER("Company")),
            "Description" = TRIM(UPPER("Description"))
    """)
    conn.commit()
    print("Normalization done.")

    # Find duplicates
    print("Searching for duplicate groups...")
    cur.execute("""
        SELECT "Inventory ID", "Company", "Description", COUNT(*)
        FROM product_inventory
        GROUP BY "Inventory ID", "Company", "Description"
        HAVING COUNT(*) > 1
    """)
    dupes = cur.fetchall()
    if not dupes:
        print("No duplicates found.")
    else:
        print(f"Found {len(dupes)} groups with duplicates.")

    merged, deleted = 0, 0
    for inv_id, company, desc, count in dupes:
        print(f"Processing group: {inv_id} | {company} | {desc} ({count} duplicates)")
        cur.execute("""
            SELECT id, "Quantity in stock", "Unit price"
            FROM product_inventory
            WHERE "Inventory ID"=? AND "Company"=? AND "Description"=?
            ORDER BY id
        """, (inv_id, company, desc))
        rows = cur.fetchall()
        if len(rows) < 2:
            print(" - Only one row. Skipping.")
            continue
        first_id = rows[0][0]
        total_qty = sum(r[1] or 0 for r in rows)
        unit_price = rows[0][2] or 0
        total_value = unit_price * total_qty
        # Update first row
        cur.execute("""
            UPDATE product_inventory
            SET "Quantity in stock"=?, "Inventory value"=?
            WHERE id=?
        """, (total_qty, total_value, first_id))
        merged += 1
        # Delete the other rows
        ids_to_delete = [r[0] for r in rows[1:]]
        for id_del in ids_to_delete:
            cur.execute("DELETE FROM product_inventory WHERE id=?", (id_del,))
            print(f" - Deleted duplicate row id={id_del}")
            deleted += 1
    conn.commit()
    print(f"Done. Merged {merged} sets, deleted {deleted} rows.")
