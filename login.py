import sqlite3

conn = sqlite3.connect('customer.db')
c = conn.cursor()
c.execute('''
    CREATE TABLE IF NOT EXISTS login_time (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        action TEXT,
        timestamp TEXT
    )
''')
conn.commit()
conn.close()
