import sqlite3

conn = sqlite3.connect("customer.db")
cur = conn.cursor()

cur.execute("SELECT invoice_number, total_amount FROM accounts_receivable")
invoices = cur.fetchall()
for invoice_number, total_amount in invoices:
    cur.execute("SELECT SUM(amount_paid) FROM receipt_payments WHERE invoice_number=?", (invoice_number,))
    paid = cur.fetchone()[0] or 0.0
    new_balance = float(total_amount) - float(paid)
    cur.execute("UPDATE accounts_receivable SET current_balance=? WHERE invoice_number=?", (new_balance, invoice_number))

conn.commit()
conn.close()
