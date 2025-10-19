from flask import Flask, render_template, request, send_file, flash, jsonify, abort, url_for, session, redirect
from io import BytesIO
from datetime import datetime, timedelta
import pdfkit
import os
import glob
import sqlite3
import smtplib
import re
import shutil
import pandas as pd
from flask_babel import Babel, _
from email.message import EmailMessage
from contextlib import closing
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from flask import redirect, url_for, flash
import pytesseract
from PIL import Image
import pdf2image
import tempfile
import re

import os
import sys
import shutil
import sqlite3

def resource_path(relative_path):
    """
    Get absolute path to resource, works for dev and PyInstaller.
    """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)


import os
import sqlite3
import shutil

APP_NAME = "Papaiacovou"
DB_NAME = "customer.db"

def get_user_db_folder():
    return os.path.join(os.environ.get("APPDATA"), APP_NAME)

def get_user_db_path():
    return os.path.join(get_user_db_folder(), DB_NAME)

def get_dist_db_path():
    # This is the read-only copy shipped with your EXE (Inno/Installer puts it here)
    app_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(app_dir, DB_NAME)

def ensure_user_db():
    """
    Make sure user's %APPDATA%\Papaiacovou\customer.db exists.
    If not, copy from program directory (dist folder).
    """
    user_db_folder = get_user_db_folder()
    user_db_path = get_user_db_path()
    dist_db_path = get_dist_db_path()
    if not os.path.exists(user_db_folder):
        os.makedirs(user_db_folder)
    if not os.path.exists(user_db_path):
        if os.path.exists(dist_db_path):
            shutil.copy2(dist_db_path, user_db_path)
            print(f"Copied DB from {dist_db_path} to {user_db_path}")
        else:
            # This is a fresh install, or somehow no DB shipped - you can create a blank db here if needed.
            print(f"DB missing at {dist_db_path}! Creating new empty DB at {user_db_path}")
            open(user_db_path, "w").close()
    else:
        print(f"DB found at {user_db_path}")

# Call this ONCE at the top, before anything DB-related runs:
ensure_user_db()

def db_connect():
    db_path = get_user_db_path()
    print("Connecting to DB:", db_path)
    return sqlite3.connect(db_path)


# --- Optional: Outlook automation (pip install pywin32) ---
try:
    import win32com.client as win32  # requires: pywin32
except Exception:
    win32 = None

app = Flask(__name__)
app.secret_key = 'secret_key_for_flash_messages'

app.config['BABEL_DEFAULT_LOCALE'] = 'en'

babel = Babel(app)

def get_locale():
    return session.get('lang', 'en')

babel.locale_selector_func = get_locale  # <-- This line sets your selector
# Make company info available to every template automatically
@app.context_processor
def inject_company():
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM company_info ORDER BY updated_at DESC LIMIT 1")
        row = cur.fetchone()
        if row:
            keys = [d[0] for d in cur.description]
            company = dict(zip(keys, row))
        else:
            company = {}
    return {"company": company}

# ------------- AUTH HELPERS -------------
def current_user():
    if 'user_id' not in session:
        return None
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute('SELECT id, username, role FROM users WHERE id=?', (session['user_id'],))
        row = cur.fetchone()
        if row:
            return {'id': row[0], 'username': row[1], 'role': row[2]}
    return None

@app.context_processor
def inject_user():
    return dict(current_user=current_user())

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user():
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = current_user()
        if not user or user['role'] != 'admin':
            flash("Admin access required.", "danger")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# ------------- DB HELPERS -------------
INVOICE_COUNTER_FILE = "invoice_number.txt"
DATABASE_FILE = "customer.db"
QUOTATION_COUNTER_FILE = "quotation_number.txt"

def log_login(username):
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO login_session (username, login_time) VALUES (?, ?)
        """, (username, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        conn.commit()

def log_logout(username):
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        # Step 1: Find the latest session id for this user with NULL logout_time
        cur.execute("""
            SELECT id FROM login_session
             WHERE username = ? AND logout_time IS NULL
             ORDER BY login_time DESC LIMIT 1
        """, (username,))
        row = cur.fetchone()
        if row:
            session_id = row[0]
            # Step 2: Update that session row
            cur.execute("""
                UPDATE login_session
                   SET logout_time = ?
                 WHERE id = ?
            """, (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), session_id))
            conn.commit()


def db_connect(row_factory=None):
    conn = sqlite3.connect(DATABASE_FILE, timeout=15, isolation_level=None)
    if row_factory:
        conn.row_factory = row_factory
    with conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=15000;")
        conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def get_company_info_from_db():
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM company_info ORDER BY updated_at DESC LIMIT 1")
        row = cur.fetchone()
        if row:
            keys = [d[0] for d in cur.description]
            return dict(zip(keys, row))
        return {'company_name': 'Company Name'}


def _table_has_column(conn, table, column):
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1].lower() == column.lower() for row in cur.fetchall())
    
def migrate_users_add_names():
    """Ensure users table has first_name and last_name columns."""
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(users)")
        columns = [row[1] for row in cur.fetchall()]
        if "first_name" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN first_name TEXT")
        if "last_name" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN last_name TEXT")

def init_db():
    with closing(db_connect()) as conn:
        with conn:
            # --- YOUR EXISTING DB SETUP CODE HERE ---
            conn.execute('''
                CREATE TABLE IF NOT EXISTS bill_to_info (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_name TEXT,
                    address TEXT,
                    city_postal TEXT,
                    phone TEXT,
                    email TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.execute('''
                CREATE TABLE IF NOT EXISTS company_info (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_name TEXT,
                    email TEXT,
                    address TEXT,
                    city TEXT,
                    postal TEXT,
                    country TEXT,
                    phone TEXT,
                    vat_rate REAL,
                    vat_no TEXT,
                    bank_name TEXT,
                    account_number TEXT,
                    swift TEXT,
                    iban TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('admin','user')),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Ensure there is at least one admin user
            cur = conn.execute('SELECT COUNT(*) FROM users WHERE role="admin"')
            if cur.fetchone()[0] == 0:
                conn.execute('''
                    INSERT INTO users (username, password_hash, role)
                    VALUES (?, ?, ?)
                ''', ('admin', generate_password_hash('i4ipapa'), 'admin'))
            
            # ... any other table setup you have ...

            # Safe migration for voiding fields (etc) ...
            if not _table_has_column(conn, "accounts_receivable", "voided"):
                conn.execute("ALTER TABLE accounts_receivable ADD COLUMN voided INTEGER DEFAULT 0")
            if not _table_has_column(conn, "accounts_receivable", "voided_at"):
                conn.execute("ALTER TABLE accounts_receivable ADD COLUMN voided_at TEXT")
            if not _table_has_column(conn, "accounts_receivable", "void_reason"):
                conn.execute("ALTER TABLE accounts_receivable ADD COLUMN void_reason TEXT")
    print("Database initialized / migrated.")

    # After DB creation, migrate users table to add names if not present
    migrate_users_add_names()


# ------------- DB SETUP -------------
def init_db():
    with closing(db_connect()) as conn:
        with conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS bill_to_info (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_name TEXT,
                    address TEXT,
                    city_postal TEXT,
                    phone TEXT,
                    email TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('admin','user')),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Ensure there is at least one admin user
            cur = conn.execute('SELECT COUNT(*) FROM users WHERE role="admin"')
            if cur.fetchone()[0] == 0:
                conn.execute('''
                    INSERT INTO users (username, password_hash, role)
                    VALUES (?, ?, ?)
                ''', ('admin', generate_password_hash('i4ipapa'), 'admin'))

            conn.execute('''
                CREATE TABLE IF NOT EXISTS accounts_receivable (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    invoice_number TEXT,
                    company_name TEXT,
                    invoice_date TEXT,
                    due_date TEXT,
                    total_amount REAL,
                    paid_status TEXT DEFAULT 'UNPAID',
                    current_balance REAL,
                    phone TEXT,
                    product TEXT,
                    description TEXT,
                    units REAL,
                    price REAL,
                    discount REAL,
                    subtotal REAL,
                    vat REAL
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS quotations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    quotation_number TEXT,
                    company_name TEXT,
                    quotation_date TEXT,
                    due_date TEXT,
                    total_amount REAL,
                    phone TEXT,
                    product TEXT,
                    description TEXT,
                    units REAL,
                    price REAL,
                    discount REAL,
                    subtotal REAL,
                    vat REAL
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS receipts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    receipt_number INTEGER,
                    date TEXT,
                    company_name TEXT,
                    bill_to TEXT
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS receipt_payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    receipt_id INTEGER,
                    invoice_number TEXT,
                    amount_paid REAL,
                    method TEXT,
                    check_number TEXT,
                    bank TEXT,
                    total_exc_vat REAL,
                    FOREIGN KEY(receipt_id) REFERENCES receipts(id)
                )
            ''')
            conn.execute('''
                CREATE UNIQUE INDEX IF NOT EXISTS idx_receipts_receipt_number
                ON receipts(receipt_number)
            ''')
            # Safe migration for voiding fields
            if not _table_has_column(conn, "accounts_receivable", "voided"):
                conn.execute("ALTER TABLE accounts_receivable ADD COLUMN voided INTEGER DEFAULT 0")
            if not _table_has_column(conn, "accounts_receivable", "voided_at"):
                conn.execute("ALTER TABLE accounts_receivable ADD COLUMN voided_at TEXT")
            if not _table_has_column(conn, "accounts_receivable", "void_reason"):
                conn.execute("ALTER TABLE accounts_receivable ADD COLUMN void_reason TEXT")
    print("Database initialized / migrated.")


init_db()
# ---------------------- HELPERS ----------------------
def current_user():
    if 'user_id' not in session:
        return None
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute('SELECT id, username, role FROM users WHERE id=?', (session['user_id'],))
        row = cur.fetchone()
        if row:
            return {'id': row[0], 'username': row[1], 'role': row[2]}
    return None

from datetime import datetime
from contextlib import closing

def this_month_invoice_counts():
    from datetime import datetime
    now = datetime.now()
    year_str = str(now.year)
    month_str = f"{now.month:02d}"

    # Build the correct YYYY-MM-DD range for this month
    first_day = f"{year_str}-{month_str}-01"
    if now.month == 12:
        next_month = f"{now.year + 1}-01-01"
    else:
        next_month = f"{year_str}-{int(month_str)+1:02d}-01"

    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN paid_status = 'PAID' THEN 1 ELSE 0 END) as paid,
                SUM(CASE WHEN paid_status = 'UNPAID' THEN 1 ELSE 0 END) as unpaid,
                SUM(CASE WHEN paid_status = 'PARTIAL' THEN 1 ELSE 0 END) as partial
            FROM accounts_receivable
            WHERE
                (
                    (invoice_date >= ? AND invoice_date < ?)
                    OR
                    (substr(invoice_date,4,2) = ? AND substr(invoice_date,7,4) = ?)
                )
                AND (voided IS NULL OR voided = 0)
                AND (paid_status IS NULL OR paid_status != 'VOID')
        """, (first_day, next_month, month_str, year_str))
        row = cur.fetchone()
        return {
            'total': row[0] or 0,
            'paid': row[1] or 0,
            'unpaid': row[2] or 0,
            'partial': row[3] or 0,
        }


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user():
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = current_user()
        if not user or user['role'] != 'admin':
            flash("Admin access required.", "danger")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def get_next_invoice_number():
    if not os.path.exists(INVOICE_COUNTER_FILE):
        with open(INVOICE_COUNTER_FILE, "w") as f:
            f.write("0000")
    with open(INVOICE_COUNTER_FILE, "r+") as f:
        last_number = f.read().strip()
        try:
            next_number = int(last_number) + 1
        except ValueError:
            next_number = 1
        f.seek(0)
        f.write(f"{next_number:04d}")
        f.truncate()
    return f"{next_number:04d}"

def get_next_quotation_number():
    if not os.path.exists(QUOTATION_COUNTER_FILE):
        with open(QUOTATION_COUNTER_FILE, "w") as f:
            f.write("0000")
    with open(QUOTATION_COUNTER_FILE, "r+") as f:
        last_number = f.read().strip()
        try:
            next_number = int(last_number) + 1
        except ValueError:
            next_number = 1
        f.seek(0)
        f.write(f"{next_number:04d}")
        f.truncate()
    return f"{next_number:04d}"

def peek_next_quotation_number():
    if not os.path.exists(QUOTATION_COUNTER_FILE):
        return "0001"
    with open(QUOTATION_COUNTER_FILE, "r") as f:
        last_number = f.read().strip()
        try:
            next_number = int(last_number) + 1
        except ValueError:
            next_number = 1
    return f"{next_number:04d}"

def get_company_info():
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM company_info ORDER BY updated_at DESC LIMIT 1")
        row = cur.fetchone()
        if row:
            keys = [d[0] for d in cur.description]
            return dict(zip(keys, row))
        return {}

# ======== VISUAL TRENDS DATA HELPERS ========
from collections import defaultdict

def get_revenue_by_customer():
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute('''
            SELECT company_name, SUM(total_amount)
            FROM accounts_receivable
            WHERE company_name IS NOT NULL AND TRIM(company_name) != ""
            GROUP BY company_name
        ''')
        data = cur.fetchall()
    return {
        "labels": [row[0] for row in data],
        "data": [row[1] for row in data]
    }

def get_revenue_by_product():
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute('''
            SELECT product, SUM(total_amount)
            FROM accounts_receivable
            WHERE product IS NOT NULL AND TRIM(product) != ""
            GROUP BY product
        ''')
        data = cur.fetchall()
    return {
        "labels": [row[0] for row in data],
        "data": [row[1] for row in data]
    }

def get_revenue_this_month():
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute('''
            SELECT company_name, SUM(total_amount)
            FROM accounts_receivable
            WHERE strftime('%Y-%m', invoice_date) = strftime('%Y-%m', date('now'))
            GROUP BY company_name
        ''')
        data = cur.fetchall()
    return {
        "labels": [row[0] for row in data],
        "data": [row[1] for row in data]
    }

def get_paid_vs_unpaid_over_time():
    # Returns paid/unpaid/voided by month for the last 12 months
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute('''
            SELECT 
                strftime('%Y-%m', invoice_date) as month,
                paid_status,
                SUM(total_amount)
            FROM accounts_receivable
            WHERE invoice_date >= date('now','-12 months')
            GROUP BY month, paid_status
            ORDER BY month ASC
        ''')
        rows = cur.fetchall()
    # Group by month and paid_status
    data = defaultdict(lambda: {"PAID":0, "UNPAID":0, "VOID":0})
    for month, status, amount in rows:
        data[month][(status or "UNPAID").upper()] += amount or 0
    months = sorted(data.keys())
    return {
        "labels": months,
        "paid":   [data[m]["PAID"] for m in months],
        "unpaid": [data[m]["UNPAID"] for m in months],
        "void":   [data[m]["VOID"] for m in months],
    }

def get_inventory_trends():
    # Just counts number of receivables per product for demo.
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute('''
            SELECT product, COUNT(*) 
            FROM accounts_receivable
            WHERE product IS NOT NULL AND TRIM(product) != ""
            GROUP BY product
        ''')
        rows = cur.fetchall()
    return {
        "labels": [r[0] for r in rows],
        "data":   [r[1] for r in rows]
    }
# =============================================



def get_next_receipt_number_seq() -> int:
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT COALESCE(MAX(receipt_number), 0) FROM receipts")
        last = cur.fetchone()[0] or 0
        return int(last) + 1

def fmt_receipt_no(n: int) -> str:
    return f"{int(n):04d}"

def extract_email(text_or_company: str, prefer_billto: bool = True) -> str:
    """Try to pull an email from a free-text Bill-To block; if none, look up the company's latest email."""
    if prefer_billto and text_or_company:
        m = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text_or_company, re.I)
        if m:
            return m.group(0).strip()
    company = (text_or_company or "").strip().upper()
    if not company:
        return ""
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT email FROM bill_to_info
             WHERE company_name = ?
             ORDER BY id DESC LIMIT 1
        """, (company,))
        row = cur.fetchone()
        if row and row[0]:
            return row[0].strip()
    return ""

def _save_eml_locally_and_open(msg: EmailMessage, number: str, to_email: str, prefix: str = "invoice") -> str:
    """Save a ready-to-send .eml and try to open it (prefix='invoice' or 'receipt')."""
    out_dir = r"C:\flask_project - Setup - Setup\outbox"
    os.makedirs(out_dir, exist_ok=True)
    safe_to = (to_email or "unknown").replace("@", "_at_").replace(">", "").replace("<", "").replace(".", "_")
    eml_path = os.path.join(out_dir, f"{prefix}_{number}_{safe_to}.eml")
    with open(eml_path, "wb") as f:
        f.write(msg.as_bytes())
    try:
        os.startfile(eml_path)  # Windows: open draft in default mail client
    except Exception:
        pass
    return eml_path

def open_outlook_compose(to_email: str, subject: str, body: str, attachment_path: str) -> bool:
    """Open Outlook compose window with populated fields/attachment. Returns True if Outlook was used."""
    if win32 is None:
        return False
    try:
        outlook = win32.Dispatch("Outlook.Application")
        mail = outlook.CreateItem(0)  # olMailItem
        mail.To = to_email or ""
        mail.Subject = subject
        mail.Body = body
        if os.path.exists(attachment_path):
            mail.Attachments.Add(attachment_path)
        mail.Display(True)
        return True
    except Exception:
        return False

def send_email_with_pdf(to_email, subject, body, pdf_bytes, filename, from_email, smtp_server, smtp_port, smtp_user, smtp_pass):
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.application import MIMEApplication
    import smtplib

    msg = MIMEMultipart()
    msg["From"] = from_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    # Attach the PDF
    part = MIMEApplication(pdf_bytes, Name=filename)
    part['Content-Disposition'] = f'attachment; filename="{filename}"'
    msg.attach(part)

    try:
        with smtplib.SMTP_SSL(smtp_server, smtp_port) as server:
            server.login(smtp_user, smtp_pass)
            server.sendmail(from_email, [to_email], msg.as_string())
        return True
    except Exception as e:
        print("Failed to send email:", e)
        return False

# ---- Void helper ----
def void_invoice(invoice_number: str, reason: str = "") -> bool:
    with closing(db_connect(sqlite3.Row)) as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, voided FROM accounts_receivable WHERE invoice_number=?", (invoice_number,))
        inv = cur.fetchone()
        if not inv or int(inv["voided"] or 0) == 1:
            return False
        cur.execute("SELECT COUNT(*) FROM receipt_payments WHERE invoice_number=?", (invoice_number,))
        if (cur.fetchone()[0] or 0) > 0:
            return False
        cur.execute("""
            UPDATE accounts_receivable
               SET voided=1,
                   paid_status='VOID',
                   current_balance=0.0,
                   voided_at=datetime('now'),
                   void_reason=?
             WHERE invoice_number=?
        """, (reason, invoice_number))
        conn.commit()
    ...
    return True



# ---------------------- ROUTES ----------------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    # Always get usernames for the dropdown (even on POST)
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute('SELECT username FROM users ORDER BY username ASC')
        usernames = [row[0] for row in cur.fetchall()]

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        with closing(db_connect()) as conn:
            cur = conn.cursor()
            # Only allow login if active=1
            cur.execute('SELECT id, username, password_hash, role, must_change_password FROM users WHERE username=? AND active=1', (username,))
            row = cur.fetchone()
            if row and check_password_hash(row[2], password):
                session['user_id'] = row[0]
                session['username'] = row[1]
                session['role'] = row[3]

                # --- Log login session here ---
                log_login(row[1])  # row[1] is the username

                if row[4]:  # <---- This is critical!
                    flash("Please change your password or confirm to keep it.", "warning")
                    return redirect(url_for('force_change_password'))
                flash("Login successful.", "success")
                return redirect(url_for('dashboard'))
            else:
                flash("Invalid username or password.", "danger")
    return render_template('login.html', usernames=usernames, company=get_company_info())

@app.route('/logout')
def logout():
    username = session.get('username')
    if username:
        log_logout(username)  # Log the logout time for the user

    session.pop('user_id', None)
    session.pop('username', None)  # Clear username as well
    session.pop('role', None)      # (Optional) Clear role too
    flash("Logged out.", "success")
    return redirect(url_for('login'))




@app.route('/users')
@admin_required
def users():
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute('SELECT id, username, first_name, last_name, department, role, created_at, active FROM users ORDER BY username ASC')
        users = [
            {
                'id': row[0],
                'username': row[1],
                'first_name': row[2] or '',
                'last_name': row[3] or '',
                'department': row[4] or '',   # <-- department here
                'role': row[5],
                'created_at': row[6],
                'active': row[7]
            }
            for row in cur.fetchall()
        ]
    return render_template('users.html', users=users)



import pandas as pd
from werkzeug.security import generate_password_hash
from flask import request, render_template, redirect, url_for, flash
from contextlib import closing

def get_departments():
    df = pd.read_excel('department.xlsx')
    if 'Department' in df.columns:
        return df['Department'].dropna().unique().tolist()
    else:
        return df.iloc[:,0].dropna().unique().tolist()

@app.route('/edit_user/<int:user_id>', methods=['GET', 'POST'])
@admin_required
def edit_user(user_id):
    departments = get_departments()  # <-- Get the departments for the dropdown

    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute('SELECT id, username, first_name, last_name, role, department FROM users WHERE id=?', (user_id,))
        row = cur.fetchone()
        if not row:
            flash("User not found.", "danger")
            return redirect(url_for('users'))
        user = {
            'id': row[0],
            'username': row[1],
            'first_name': row[2] or '',
            'last_name': row[3] or '',
            'role': row[4],
            'department': row[5] or ''
        }
        if request.method == 'POST':
            username = request.form.get('username', '').strip()
            first_name = request.form.get('first_name', '').strip()
            last_name = request.form.get('last_name', '').strip()
            role = request.form.get('role', 'user')
            department = request.form.get('department', '').strip()  # <-- Get from form
            new_password = request.form.get('password', '')

            if not username:
                flash("Username is required.", "danger")
            else:
                try:
                    # Only update password if a new one is provided
                    if new_password:
                        password_hash = generate_password_hash(new_password)
                        cur.execute(
                            'UPDATE users SET username=?, first_name=?, last_name=?, role=?, department=?, password_hash=? WHERE id=?',
                            (username, first_name, last_name, role, department, password_hash, user_id)
                        )
                    else:
                        cur.execute(
                            'UPDATE users SET username=?, first_name=?, last_name=?, role=?, department=? WHERE id=?',
                            (username, first_name, last_name, role, department, user_id)
                        )
                    flash("User updated!", "success")
                    return redirect(url_for('users'))
                except sqlite3.IntegrityError:
                    flash("Username already exists.", "danger")
        return render_template('edit_user.html', user=user, departments=departments)


import pandas as pd
from werkzeug.security import generate_password_hash
from flask import flash, render_template, redirect, url_for, request
from contextlib import closing

def get_departments():
    df = pd.read_excel('department.xlsx')
    if 'Department' in df.columns:
        return df['Department'].dropna().unique().tolist()
    else:
        return df.iloc[:,0].dropna().unique().tolist()

@app.route('/add_user', methods=['GET', 'POST'])
@admin_required
def add_user():
    departments = get_departments()  # <--- NEW: get list for dropdown
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        role = request.form.get('role', 'user')
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        department = request.form.get('department', '').strip()  # <--- NEW
        if not username or not password:
            flash("Username and password are required.", "danger")
        else:
            with closing(db_connect()) as conn:
                cur = conn.cursor()
                try:
                    # Save department too!
                    cur.execute('''
                        INSERT INTO users (username, password_hash, role, first_name, last_name, department, must_change_password)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (username, generate_password_hash(password), role, first_name, last_name, department, 1))
                    flash("User added!", "success")
                    return redirect(url_for('users'))
                except sqlite3.IntegrityError:
                    flash("Username already exists.", "danger")
    return render_template('add_user.html', departments=departments)


@app.route('/deactivate_user/<int:user_id>', methods=['POST'])
@admin_required
def deactivate_user(user_id):
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        # Get the current status
        cur.execute("SELECT active FROM users WHERE id=?", (user_id,))
        row = cur.fetchone()
        if not row:
            flash("User not found.", "danger")
            return redirect(url_for('users'))

        new_status = 0 if row[0] else 1  # Toggle active
        cur.execute("UPDATE users SET active=? WHERE id=?", (new_status, user_id))
        conn.commit()
        flash("User activated." if new_status else "User deactivated.", "info")
    return redirect(url_for('users'))
    

@app.route('/delete_user/<username>', methods=['POST'])
@admin_required  # Only admins can delete users!
def delete_user(username):
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM users WHERE username=?', (username,))
        conn.commit()
    flash(f'User {username} deleted successfully!', 'success')
    return redirect(url_for('users'))

@app.route('/toggle_user_active/<int:user_id>', methods=['POST'])
@admin_required
def toggle_user_active(user_id):
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        # Get current status
        cur.execute("SELECT active FROM users WHERE id=?", (user_id,))
        row = cur.fetchone()
        if not row:
            flash("User not found.", "danger")
        else:
            new_status = 0 if row[0] else 1
            cur.execute("UPDATE users SET active=? WHERE id=?", (new_status, user_id))
            conn.commit()
            flash("User status updated.", "success")
    return redirect(url_for('users'))


@app.route('/')
@login_required
def dashboard():
    company_info = get_company_info()
    year = datetime.now().year

    invoices_this_month = 0
    outstanding_payments = 0
    customer_count = 0
    low_inventory_count = 0
    paid_this_month = unpaid_this_month = partial_this_month = 0

    user = current_user()
    counts = {'paid': 0, 'unpaid': 0, 'partial': 0, 'total': 0}
    if user and user['role'] == 'admin':
        # ✅ Use the single source of truth for counts
        counts = this_month_invoice_counts()
        invoices_this_month = counts['total']
        paid_this_month     = counts['paid']
        unpaid_this_month   = counts['unpaid']
        partial_this_month  = counts['partial']

        with closing(db_connect()) as conn:
            cur = conn.cursor()
            cur.execute("SELECT SUM(current_balance) FROM accounts_receivable WHERE paid_status IN ('UNPAID', 'PARTIAL')")
            outstanding_payments = cur.fetchone()[0] or 0
            cur.execute("SELECT COUNT(DISTINCT company_name) FROM bill_to_info")
            customer_count = cur.fetchone()[0] or 0
            cur.execute('SELECT COUNT(*) FROM product_inventory WHERE "Quantity in stock" < "Reorder level"')
            low_inventory_count = cur.fetchone()[0] or 0

    return render_template(
        'dashboard.html',
        company_name=company_info.get('company_name', 'My Company'),
        year=year,
        current_user=user,
        invoices_this_month=invoices_this_month,
        outstanding_payments=outstanding_payments,
        customer_count=customer_count,
        low_inventory_count=low_inventory_count,
        paid_count=counts["paid"],
        unpaid_count=counts["unpaid"],
        partial_count=counts["partial"],
    )

# ---- Add/Edit/Delete Customers ----
@app.route('/edit_customers', methods=['GET', 'POST'])
def edit_customers():
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        if request.method == 'POST':
            action = (request.form.get('action') or '').lower()
            if action == 'add':
                company_name = (request.form.get('company_name') or '').strip().upper()
                address      = (request.form.get('address') or '').strip().upper()
                city_postal  = (request.form.get('city_postal') or '').strip().upper()
                phone        = (request.form.get('phone') or '').strip().upper()
                email        = (request.form.get('email') or '').strip()
                if not company_name:
                    flash("Company name is required.", "error")
                else:
                    cur.execute('SELECT 1 FROM bill_to_info WHERE company_name = ? OR email = ? LIMIT 1',
                                (company_name, email))
                    if cur.fetchone():
                        flash("Customer already exists in the database.", "info")
                    else:
                        cur.execute('''
                            INSERT INTO bill_to_info (company_name, address, city_postal, phone, email)
                            VALUES (?, ?, ?, ?, ?)
                        ''', (company_name, address, city_postal, phone, email))
                        flash(f"Customer '{company_name}' added.", "success")
            elif action == 'update':
                try:
                    cid = int(request.form.get('id', '0'))
                except ValueError:
                    cid = 0
                company_name = (request.form.get('company_name') or '').strip().upper()
                address      = (request.form.get('address') or '').strip().upper()
                city_postal  = (request.form.get('city_postal') or '').strip().upper()
                phone        = (request.form.get('phone') or '').strip().upper()
                email        = (request.form.get('email') or '').strip()
                if cid <= 0 or not company_name:
                    flash("Invalid update request.", "error")
                else:
                    cur.execute('''
                        SELECT 1 FROM bill_to_info
                         WHERE (company_name = ? OR email = ?)
                           AND id <> ?
                         LIMIT 1
                    ''', (company_name, email, cid))
                    if cur.fetchone():
                        flash("Another customer already uses that company or email.", "warning")
                    else:
                        cur.execute('''
                            UPDATE bill_to_info
                               SET company_name = ?, address = ?, city_postal = ?, phone = ?, email = ?
                             WHERE id = ?
                        ''', (company_name, address, city_postal, phone, email, cid))
                        flash("Customer updated.", "success")
            elif action == 'delete':
                try:
                    cid = int(request.form.get('id', '0'))
                except ValueError:
                    cid = 0
                if cid <= 0:
                    flash("Invalid delete request.", "error")
                else:
                    cur.execute('DELETE FROM bill_to_info WHERE id = ?', (cid,))
                    flash("Customer deleted.", "success")
        cur.execute('SELECT id, company_name, address, city_postal, phone, email, created_at '
                    'FROM bill_to_info ORDER BY company_name ASC')
        customers = cur.fetchall()
    return render_template('edit_customers.html', customers=customers)
    
@app.route('/get_usernames')
def get_usernames():
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute('SELECT username FROM users ORDER BY username ASC')
        usernames = [row[0] for row in cur.fetchall()]
    return jsonify(usernames)


@app.route('/get_inventory_descriptions')
def get_inventory_descriptions():
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute('SELECT DISTINCT "Description" FROM product_inventory WHERE "Description" IS NOT NULL AND TRIM("Description") != "" ORDER BY "Description" ASC')
        descs = [r[0] for r in cur.fetchall() if r[0]]
    return jsonify(descs)



@app.route('/accounts_receivable')
def accounts_receivable():
    this_month = request.args.get('this_month')  # e.g., "1"
    filt = (request.args.get('filter') or '').lower()  # 'unpaid', 'customers', etc.

    # Build WHERE clauses
    where_clauses = []
    params = []

    # Month window (local time); normalize dd-mm-YYYY → ISO for comparison
    date_expr = """
        date(
          CASE
            WHEN length(invoice_date)=10 AND substr(invoice_date,3,1)='-'
                 THEN substr(invoice_date,7,4)||'-'||substr(invoice_date,4,2)||'-'||substr(invoice_date,1,2)
            ELSE invoice_date
          END
        )
    """

    if this_month:
        where_clauses.append(f"""{date_expr} >= date('now','start of month','localtime')""")
        where_clauses.append(f"""{date_expr} <  date('now','start of month','+1 month','localtime')""")

    if filt == 'unpaid':
        where_clauses.append("paid_status IN ('UNPAID','PARTIAL')")

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    order_sql = f"ORDER BY {date_expr} DESC, id DESC"

    with closing(db_connect(sqlite3.Row)) as conn:
        cur = conn.cursor()

        # Main table
        cur.execute(f"""
            SELECT
                id, invoice_number, company_name, invoice_date, due_date,
                total_amount, paid_status, current_balance,
                phone, product, description, units, price, discount, subtotal, vat,
                COALESCE(voided,0) AS voided, voided_at, void_reason
            FROM accounts_receivable
            {where_sql}
            {order_sql}
        """, params)
        receivables = cur.fetchall()

        # Summary tiles (use same filters for consistency)
        cur.execute(f"""
            SELECT COALESCE(SUM(total_amount),0)
            FROM accounts_receivable
            {where_sql}
        """, params)
        total_amount_sum = cur.fetchone()[0] or 0.0

        # <-- NEW: SUM VAT -->
        cur.execute(f"""
            SELECT COALESCE(SUM(vat),0)
            FROM accounts_receivable
            {where_sql}
        """, params)
        total_vat_sum = cur.fetchone()[0] or 0.0
        # <-- END NEW -->

        cur.execute(f"""
            SELECT COALESCE(SUM(current_balance),0)
            FROM accounts_receivable
            {where_sql}
        """, params)
        current_balance_sum = cur.fetchone()[0] or 0.0

        # Counts by status (respect month filter if applied)
        def _count_status(statuses):
            placeholders = ",".join("?"*len(statuses))
            cur.execute(f"""
                SELECT COUNT(*)
                FROM accounts_receivable
                {where_sql + (" AND " if where_sql else "WHERE ")} paid_status IN ({placeholders})
            """, params + statuses)
            return cur.fetchone()[0] or 0

        unpaid_count = _count_status(['UNPAID'])
        paid_count = _count_status(['PAID'])
        partial_count = _count_status(['PARTIAL'])
        void_count = _count_status(['VOID'])

    return render_template(
        'accounts_receivable.html',
        receivables=receivables,
        total_amount_sum=total_amount_sum,
        total_vat_sum=total_vat_sum,  # <-- pass to template!
        current_balance_sum=current_balance_sum,
        unpaid_count=unpaid_count,
        paid_count=paid_count,
        partial_count=partial_count,
        void_count=void_count
    )

# ---------- Void route ----------
@app.route('/void_invoice', methods=['POST'])
def void_invoice_route():
    invoice_number = (request.form.get('invoice_number') or '').strip()
    reason = (request.form.get('reason') or '').strip()
    if not invoice_number:
        flash("Missing invoice number.", "warning")
        return accounts_receivable()
    ok = void_invoice(invoice_number, reason)
    if ok:
        flash(f"Invoice {invoice_number} has been voided.", "success")
    else:
        flash("Cannot void: not found, already voided, or payments exist.", "warning")
    return accounts_receivable()

# ---------- INVOICE ----------
from flask import render_template, request, send_file, flash
from datetime import datetime, timedelta
import os
from io import BytesIO
from contextlib import closing

INVOICE_COUNTER_FILE = "invoice_number.txt"

def peek_next_invoice_number():
    if not os.path.exists(INVOICE_COUNTER_FILE):
        return "0001"
    with open(INVOICE_COUNTER_FILE, "r") as f:
        last_number = f.read().strip()
        try:
            next_number = int(last_number) + 1
        except ValueError:
            next_number = 1
    return f"{next_number:04d}"

@app.route('/invoice', methods=['GET', 'POST'])
@login_required
def invoice():
    if request.method == 'POST':
        action = (request.form.get('action') or 'download').lower()  # download | print | send
        data = request.form.to_dict(flat=False)
        products = data.get('product', [])
        descriptions = data.get('description', [])
        units = data.get('units', [])
        prices = data.get('price', [])
        discounts = data.get('discount', [])
        company_name = request.form.get('bill_to_line1', '').strip().upper()
        address = request.form.get('bill_to_line2', '').strip().upper()
        city_postal = request.form.get('bill_to_line3', '').strip().upper()
        phone = request.form.get('bill_to_line4', '').strip().upper()
        email = request.form.get('bill_to_line5', '').strip()
        bill_to = "\n".join([company_name, address, city_postal, phone, email])
        today_dt = datetime.today()
        today = today_dt.strftime('%d-%m-%Y')
        due_date = request.form.get('due_date') or (today_dt + timedelta(days=30)).strftime('%d-%m-%Y')
        invoice_number = get_next_invoice_number()

        duplicate = False
        with closing(db_connect()) as conn:
            with conn:
                cur = conn.cursor()
                cur.execute('SELECT 1 FROM bill_to_info WHERE company_name = ? OR email = ? LIMIT 1',
                            (company_name, email))
                exists = cur.fetchone()
                if not exists and company_name:
                    cur.execute('''
                        INSERT INTO bill_to_info (company_name, address, city_postal, phone, email)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (company_name, address, city_postal, phone, email))
                else:
                    duplicate = True
        if duplicate:
            flash("Customer already exists in the database.", "info")

        items = []
        subtotal_sum = 0.0
        vat_rate = 0.19
        for product, desc, unit, price, discount in zip(products, descriptions, units, prices, discounts):
            try:
                units_val = float(unit)
                price_val = float(price)
                discount_val = float(discount)
            except ValueError:
                flash("Invalid number, price or discount value.")
                return render_template('invoice_form.html')
            line_subtotal = max((price_val - discount_val) * units_val, 0.0)
            subtotal_sum += line_subtotal
            items.append({
                'product': product,
                'description': desc,
                'units': units_val,
                'price': round(price_val, 2),
                'discount': round(discount_val, 2),
                'subtotal': round(line_subtotal, 2)
            })
        vat_amount = round(subtotal_sum * vat_rate, 2)
        total = round(subtotal_sum + vat_amount, 2)

        first = items[0] if items else {'product': '', 'description': '', 'units': 0.0, 'price': 0.0, 'discount': 0.0, 'subtotal': 0.0}
        with closing(db_connect()) as conn:
            with conn:
                cur = conn.cursor()
                cur.execute('''
                    INSERT INTO accounts_receivable
                    (invoice_number, company_name, invoice_date, due_date,
                     total_amount, paid_status, current_balance,
                     phone, product, description, units, price, discount, subtotal, vat, voided)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    invoice_number, company_name, today, due_date,
                    total, 'UNPAID', total,
                    phone, first['product'], first['description'],
                    float(first['units']), float(first['price']), float(first['discount']),
                    float(first['subtotal']), float(vat_amount), 0
                ))

        # INVENTORY update
        with closing(db_connect()) as conn:
            cur = conn.cursor()
            for item in items:
                cur.execute("""
                    UPDATE product_inventory
                    SET "Quantity in stock" = MAX(COALESCE("Quantity in stock", 0) - ?, 0)
                    WHERE "Inventory ID" = ? AND "Description" = ?
                """, (item['units'], item['product'], item['description']))
            conn.commit()

        company = get_company_info()
        rendered = render_template(
            'invoice_template.html',
            items=items,
            subtotal=round(subtotal_sum, 2),
            vat=vat_amount,
            total=total,
            today=today,
            due_date=due_date,
            bill_to=bill_to,
            invoice_number=invoice_number,
            company=company
        )

        config = None
        if os.name == 'nt':
            wkhtmltopdf_path = r'C:\\Program Files\\wkhtmltopdf\\bin\\wkhtmltopdf.exe'
            if os.path.exists(wkhtmltopdf_path):
                config = pdfkit.configuration(wkhtmltopdf=wkhtmltopdf_path)
            else:
                flash("wkhtmltopdf not found. Check your path.")
                return render_template('invoice_form.html')
        options = {'enable-local-file-access': '', 'page-size': 'A4', 'encoding': 'UTF-8'}
        pdf_bytes = pdfkit.from_string(rendered, False, configuration=config, options=options)

        output_dir = r"C:\flask_project - Setup - Setup\invoices"
        os.makedirs(output_dir, exist_ok=True)
        safe_bill_to = company_name.strip().replace(" ", "_").replace("\n", "_")[:30] or "unknown"
        output_filename = f"invoice_{invoice_number}_{safe_bill_to}.pdf"
        output_path = os.path.join(output_dir, output_filename)
        with open(output_path, 'wb') as f:
            f.write(pdf_bytes)

        if action == 'print':
            return render_template('print_invoice.html', invoice_number=invoice_number)

        if action == 'send':
            to_email = email or extract_email(bill_to, prefer_billto=True) or extract_email(company_name, prefer_billto=False)
            subject = f"Invoice {invoice_number} from {company.get('company_name','')}"
            body = (
                f"Dear {company_name or 'Customer'},\n\n"
                f"Please find attached invoice {invoice_number}.\n\n"
                f"Best regards,\n{company.get('company_name','')}\n{company.get('email','')}"
            )
            # -- SEND EMAIL DIRECTLY VIA SMTP --
            import smtplib
            from email.message import EmailMessage

            # Set these at the top of your file (global, or here for quick test)
            SMTP_USER = company.get('email', '')  # Or your actual email
            SMTP_PASSWORD = "your_app_password_here"  # Set your real app password!

            msg = EmailMessage()
            msg['Subject'] = subject
            msg['From'] = SMTP_USER
            msg['To'] = to_email
            msg.set_content(body)
            msg.add_attachment(pdf_bytes, maintype='application', subtype='pdf', filename=output_filename)
            try:
                with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
                    smtp.login(SMTP_USER, SMTP_PASSWORD)
                    smtp.send_message(msg)
                flash("Invoice sent directly to customer by email.", "success")
            except Exception as e:
                flash(f"Failed to send email: {e}", "danger")
            return render_template('invoice_form.html')

        return send_file(BytesIO(pdf_bytes), as_attachment=True, download_name=output_filename)

    # GET: Show form
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute('SELECT DISTINCT "Inventory ID" FROM product_inventory WHERE "Inventory ID" IS NOT NULL AND TRIM("Inventory ID") != ""')
        inventory_ids = [row[0] for row in cur.fetchall()]
        cur.execute('SELECT DISTINCT "Description" FROM product_inventory WHERE "Description" IS NOT NULL AND TRIM("Description") != ""')
        descriptions = [row[0] for row in cur.fetchall()]

    return render_template(
        'invoice_form.html',
        invoice_number=peek_next_invoice_number(),
        today=datetime.today().strftime('%d-%m-%Y'),
        inventory_ids=inventory_ids,
        descriptions=descriptions
    )
    



# ------- API/HELPER ROUTES --------
@app.route('/get_customers', methods=['GET'])
def get_customers():
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute('SELECT DISTINCT company_name FROM bill_to_info ORDER BY company_name ASC')
        rows = cur.fetchall()
        names = [row[0] for row in rows if row[0]]
    return jsonify(names)

@app.route('/get_customer_details', methods=['GET'])
def get_customer_details():
    name = request.args.get('name', '').strip().upper()
    if not name:
        return jsonify({})
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute('''SELECT company_name, address, city_postal, phone, email
                       FROM bill_to_info
                       WHERE company_name = ?
                       ORDER BY id DESC LIMIT 1''', (name,))
        row = cur.fetchone()
        if row:
            return jsonify({
                'company_name': row[0] or '',
                'address': row[1] or '',
                'city_postal': row[2] or '',
                'phone': row[3] or '',
                'email': row[4] or ''
            })
    return jsonify({})

@app.route('/get_billto')
def get_billto():
    name = request.args.get('name', '').strip().upper()
    if not name:
        return jsonify({"bill_to": ""})
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute('''
            SELECT company_name, address, city_postal, phone, email
            FROM bill_to_info
            WHERE company_name=?
            ORDER BY id DESC LIMIT 1
        ''', (name,))
        row = cur.fetchone()
    if row:
        return jsonify({"bill_to": "\n".join([str(x) for x in row if x])})
    return jsonify({"bill_to": ""})

@app.route('/get_unpaid_invoices')
def get_unpaid_invoices():
    company = request.args.get('company', '').strip().upper()
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        if company:
            cur.execute("""
                SELECT invoice_number, total_amount, current_balance, paid_status
                  FROM accounts_receivable
                 WHERE paid_status IN ('UNPAID', 'PARTIAL')
                   AND company_name=?
            """, (company,))
        else:
            cur.execute("""
                SELECT invoice_number, total_amount, current_balance, paid_status
                  FROM accounts_receivable
                 WHERE paid_status IN ('UNPAID', 'PARTIAL')
            """)
        rows = cur.fetchall()
    return jsonify([
        {"invoice_number": r[0], "total_amount": r[1], "current_balance": r[2], "paid_status": r[3]}
        for r in rows
    ])

@app.route('/get_receipts_for_invoice')
def get_receipts_for_invoice():
    invoice_number = request.args.get('invoice_number')
    if not invoice_number:
        return jsonify([])
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute('''
            SELECT r.receipt_number, r.date, rp.amount_paid, rp.method, rp.check_number, rp.bank
              FROM receipt_payments rp
              JOIN receipts r ON rp.receipt_id = r.id
             WHERE rp.invoice_number = ?
             ORDER BY r.date ASC
        ''', (invoice_number,))
        receipts = cur.fetchall()
    return jsonify([
        {"receipt_number": row[0], "date": row[1], "amount_paid": row[2], "method": row[3],
         "check_number": row[4], "bank": row[5]}
        for row in receipts
    ])

# ---- invoice-driven helpers for Receipt form ----
@app.route('/get_invoice_info')
def get_invoice_info():
    invoice_number = request.args.get('invoice_number', '').strip()
    if not invoice_number:
        return jsonify({"success": False, "error": "No invoice number provided."})
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute('SELECT company_name FROM accounts_receivable WHERE invoice_number=?', (invoice_number,))
        row = cur.fetchone()
        if not row:
            return jsonify({"success": False, "error": "Invoice not found."})
        company_name = row[0] or ""
        cur.execute('''
            SELECT address, city_postal, phone, email
              FROM bill_to_info
             WHERE company_name=?
             ORDER BY id DESC LIMIT 1
        ''', (company_name,))
        bt = cur.fetchone()
    return jsonify({
        "success": True,
        "company_name": company_name,
        "address": bt[0] if bt else "",
        "city_postal": bt[1] if bt else "",
        "phone": bt[2] if bt else "",
        "email": bt[3] if bt else ""
    })

@app.route('/get_invoice_balance')
def get_invoice_balance():
    invoice_number = request.args.get('invoice_number', '').strip()
    if not invoice_number:
        return jsonify({"success": False, "error": "No invoice number provided."})
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute('''
            SELECT total_amount, current_balance
              FROM accounts_receivable
             WHERE invoice_number=?
        ''', (invoice_number,))
        row = cur.fetchone()
    if not row:
        return jsonify({"success": False, "error": "Invoice not found."})
    total_amount = float(row[0] or 0.0)
    current_balance = float(row[1] or 0.0)
    return jsonify({"success": True, "total_amount": total_amount, "current_balance": current_balance})

# ---------- Products & Sizes ----------
@app.route('/get_products')
def get_products():
    t = (request.args.get('type') or '').strip().lower()
    if t not in ('tyres', 'services'):
        return jsonify([])
    column = 'tyres' if t == 'tyres' else 'services'
    try:
        with closing(db_connect()) as conn:
            cur = conn.cursor()
            cur.execute(f"""
                SELECT DISTINCT {column}
                  FROM products
                 WHERE {column} IS NOT NULL AND TRIM({column}) <> ''
                 ORDER BY {column} ASC
            """)
            rows = cur.fetchall()
        return jsonify([r[0] for r in rows])
    except sqlite3.OperationalError:
        return jsonify([])

@app.route('/get_sizes')
def get_sizes():
    try:
        with closing(db_connect()) as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT DISTINCT size
                  FROM products
                 WHERE size IS NOT NULL AND TRIM(size) <> ''
                 ORDER BY size ASC
            """)
            rows = cur.fetchall()
        return jsonify([r[0] for r in rows])
    except sqlite3.OperationalError:
        return jsonify([])

# ---------- Invoice PDF viewer for printing ----------
@app.route('/view_invoice/<invoice_number>')
def view_invoice(invoice_number):
    output_dir = r"C:\flask_project - Setup - Setup\invoices"
    pattern = os.path.join(output_dir, f"invoice_{invoice_number}_*.pdf")
    matches = glob.glob(pattern)
    if not matches:
        abort(404)
    path = max(matches, key=os.path.getmtime)
    return send_file(path, mimetype='application/pdf', as_attachment=False,
                     download_name=os.path.basename(path))

# ---------- Receipt PDF viewer for printing ----------
@app.route('/view_receipt/<receipt_number>')
def view_receipt(receipt_number):
    output_dir = r"C:\flask_project - Setup - Setup\receipts"
    pattern = os.path.join(output_dir, f"receipt_{receipt_number}_*.pdf")
    matches = glob.glob(pattern)
    if not matches:
        abort(404)
    path = max(matches, key=os.path.getmtime)
    return send_file(path, mimetype='application/pdf', as_attachment=False,
                     download_name=os.path.basename(path))

# ---------- RECEIPT FORM ----------
@app.route('/create_receipt', methods=['GET', 'POST'])
def create_receipt():
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute('SELECT DISTINCT company_name FROM bill_to_info ORDER BY company_name ASC')
        customer_names = [row[0] for row in cur.fetchall()]
    today = datetime.today().strftime('%Y-%m-%d')

    if request.method == 'POST':
        action = (request.form.get('action') or 'download').lower()  # download | print | send

        raw_number = (request.form.get('receipt_number') or '').strip()
        if raw_number:
            try:
                receipt_number = int(raw_number)
            except ValueError:
                flash("Receipt Number must be numeric.", "warning")
                return render_template('receipt_form.html', customer_names=customer_names, today=today,
                                       next_receipt_number=fmt_receipt_no(get_next_receipt_number_seq()))
        else:
            receipt_number = get_next_receipt_number_seq()

        date = (request.form.get('date') or today).strip()
        company_name = (request.form.get('company_name') or '').strip()
        bill_to = (request.form.get('bill_to') or '').strip()

        invoice_numbers = request.form.getlist('invoice_number')
        amounts_paid   = request.form.getlist('amount_paid')
        methods        = request.form.getlist('method')
        check_numbers  = request.form.getlist('check_number')
        banks          = request.form.getlist('bank')
        totals_exc_vat = request.form.getlist('total_exc_vat')

        if not date or not company_name or not bill_to:
            flash("Please fill in Date, Company and Bill To.", "warning")
            return render_template('receipt_form.html', customer_names=customer_names, today=today,
                                   next_receipt_number=fmt_receipt_no(get_next_receipt_number_seq()))

        with closing(db_connect()) as conn:
            cur = conn.cursor()
            cur.execute('SELECT 1 FROM receipts WHERE receipt_number = ? LIMIT 1', (receipt_number,))
            if cur.fetchone():
                flash(f"Receipt number {fmt_receipt_no(receipt_number)} already exists. Please try again.", "error")
                return render_template('receipt_form.html', customer_names=customer_names, today=today,
                                       next_receipt_number=fmt_receipt_no(get_next_receipt_number_seq()))

        with closing(db_connect(sqlite3.Row)) as conn:
            cur = conn.cursor()
            attempts = 0
            while True:
                try:
                    cur.execute('''
                        INSERT INTO receipts (receipt_number, date, company_name, bill_to)
                        VALUES (?, ?, ?, ?)
                    ''', (receipt_number, date, company_name, bill_to))
                    receipt_id = cur.lastrowid
                    break
                except sqlite3.IntegrityError:
                    attempts += 1
                    if attempts >= 3:
                        flash("Could not generate a unique receipt number. Please try again.", "error")
                        return render_template('receipt_form.html', customer_names=customer_names, today=today,
                                               next_receipt_number=fmt_receipt_no(get_next_receipt_number_seq()))
                    receipt_number = get_next_receipt_number_seq()
                    continue

            payments = []
            total_paid = 0.0
            original_invoice_amount = 0.0

            for i in range(len(invoice_numbers)):
                inv = (invoice_numbers[i] or '').strip()
                if not inv:
                    continue
                try:
                    amt_paid = float(amounts_paid[i] or 0)
                except (ValueError, IndexError):
                    amt_paid = 0.0
                method = methods[i] if i < len(methods) else ''
                chk = check_numbers[i] if i < len(check_numbers) else ''
                bank = banks[i] if i < len(banks) else ''
                try:
                    total_exc_vat = float(totals_exc_vat[i] or 0)
                except (ValueError, IndexError):
                    total_exc_vat = 0.0

                cur.execute('''
                    INSERT INTO receipt_payments
                        (receipt_id, invoice_number, amount_paid, method, check_number, bank, total_exc_vat)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (receipt_id, inv, amt_paid, method, chk, bank, total_exc_vat))

                cur.execute('SELECT total_amount FROM accounts_receivable WHERE invoice_number=?', (inv,))
                r = cur.fetchone()
                invoice_total = float(r['total_amount']) if r and r['total_amount'] is not None else 0.0
                original_invoice_amount += invoice_total

                cur.execute('SELECT COALESCE(SUM(amount_paid), 0) FROM receipt_payments WHERE invoice_number=?', (inv,))
                paid_sum = float(cur.fetchone()[0] or 0.0)
                new_balance = max(invoice_total - paid_sum, 0.0)
                status = 'PAID' if new_balance == 0 else ('PARTIAL' if paid_sum > 0 else 'UNPAID')
                cur.execute('UPDATE accounts_receivable SET current_balance=?, paid_status=? WHERE invoice_number=?',
                            (new_balance, status, inv))

                payments.append({
                    "invoice_number": inv,
                    "amount_paid": amt_paid,
                    "method": method,
                    "check_number": chk,
                    "bank": bank,
                    "total_exc_vat": total_exc_vat,
                    "invoice_total": invoice_total
                })
                total_paid += amt_paid

        amount_currently_owe = 0.0
        with closing(db_connect()) as conn2:
            cur2 = conn2.cursor()
            for inv in invoice_numbers:
                inv = (inv or '').strip()
                if not inv:
                    continue
                cur2.execute('SELECT current_balance FROM accounts_receivable WHERE invoice_number=?', (inv,))
                row = cur2.fetchone()
                if row and row[0] is not None:
                    amount_currently_owe += float(row[0])
        balance_due = amount_currently_owe

        # Render receipt HTML
        rendered = render_template(
            'receipt_template.html',
            company=get_company_info(),
            receipt_number=fmt_receipt_no(receipt_number),
            date=date,
            bill_to=bill_to,
            payments=payments,
            subtotal=float(total_paid),
            amount_currently_owe=float(amount_currently_owe),
            balance_due=float(balance_due),
            original_invoice_amount=float(original_invoice_amount)
        )

        # Create PDF
        config = None
        if os.name == 'nt':
            wkhtmltopdf_path = r'C:\\Program Files\\wkhtmltopdf\\bin\\wkhtmltopdf.exe'
            if os.path.exists(wkhtmltopdf_path):
                config = pdfkit.configuration(wkhtmltopdf=wkhtmltopdf_path)
            else:
                flash("wkhtmltopdf not found. Check your path.", "error")
                return render_template('receipt_form.html', customer_names=customer_names, today=today,
                                       next_receipt_number=fmt_receipt_no(get_next_receipt_number_seq()))
        options = {'enable-local-file-access': '', 'page-size': 'A4', 'encoding': 'UTF-8'}
        pdf_bytes = pdfkit.from_string(rendered, False, configuration=config, options=options)

        # Save to disk
        output_dir = r"C:\flask_project - Setup - Setup\receipts"
        os.makedirs(output_dir, exist_ok=True)
        safe_company = (company_name or "unknown").strip().replace(" ", "_").replace("\n", "_")[:40]
        rec_no_str = fmt_receipt_no(receipt_number)
        output_filename = f"receipt_{rec_no_str}_{safe_company}.pdf"
        output_path = os.path.join(output_dir, output_filename)
        with open(output_path, 'wb') as f:
            f.write(pdf_bytes)

        # --- branch by action for receipts ---
        if action == 'print':
            return render_template('print_receipt.html', receipt_number=rec_no_str)

        if action == 'send':
            to_email = extract_email(request.form.get('bill_to') or company_name, prefer_billto=True)
            subject = f"Receipt {rec_no_str} from {COMPANY_INFO['name']}"
            body = (
                f"Dear {company_name or 'Customer'},\n\n"
                f"Please find attached receipt {rec_no_str}.\n\n"
                f"Best regards,\n{COMPANY_INFO['name']}\n{COMPANY_INFO['email']}"
            )
            opened = open_outlook_compose(to_email, subject, body, output_path)
            if opened:
                flash("Opened in Outlook. Review and press Send.", "success")
            else:
                msg = make_eml(to_email, subject, body, pdf_bytes, f"receipt_{rec_no_str}.pdf")
                _save_eml_locally_and_open(msg, rec_no_str, to_email, prefix="receipt")
                flash("Draft created. Your email client should open with the receipt attached.", "success")
            return render_template('receipt_form.html', customer_names=customer_names, today=today,
                                   next_receipt_number=fmt_receipt_no(get_next_receipt_number_seq()))

        # default: download
        return send_file(BytesIO(pdf_bytes), as_attachment=True, download_name=output_filename)

    # GET
    next_receipt_number = fmt_receipt_no(get_next_receipt_number_seq())
    return render_template('receipt_form.html',
                           customer_names=customer_names,
                           today=today,
                           next_receipt_number=next_receipt_number)

@app.route('/quotation', methods=['GET', 'POST'])
def quotation():
    if request.method == 'POST':
        action = (request.form.get('action') or 'download').lower()
        data = request.form.to_dict(flat=False)
        products = data.get('product', [])
        descriptions = data.get('description', [])
        units = data.get('units', [])
        prices = data.get('price', [])
        discounts = data.get('discount', [])
        company_name = request.form.get('bill_to_line1', '').strip().upper()
        address = request.form.get('bill_to_line2', '').strip().upper()
        city_postal = request.form.get('bill_to_line3', '').strip().upper()
        phone = request.form.get('bill_to_line4', '').strip().upper()
        email = request.form.get('bill_to_line5', '').strip()
        bill_to = "\n".join([company_name, address, city_postal, phone, email])
        today_dt = datetime.today()
        today = today_dt.strftime('%d-%m-%Y')
        due_date = (today_dt + timedelta(days=30)).strftime('%d-%m-%Y')
        quotation_number = get_next_quotation_number()

        # Items & totals
        items = []
        subtotal_sum = 0.0
        vat_rate = 0.19
        for product, desc, unit, price, discount in zip(products, descriptions, units, prices, discounts):
            try:
                units_val = float(unit)
                price_val = float(price)
                discount_val = float(discount)
            except ValueError:
                flash("Invalid number, price or discount value.")
                return render_template('quotation_form.html', quotation_number=quotation_number, today=today)
            line_subtotal = max((price_val - discount_val) * units_val, 0.0)
            subtotal_sum += line_subtotal
            items.append({
                'product': product,
                'description': desc,
                'units': units_val,
                'price': round(price_val, 2),
                'discount': round(discount_val, 2),
                'subtotal': round(line_subtotal, 2)
            })
        vat_amount = round(subtotal_sum * vat_rate, 2)
        total = round(subtotal_sum + vat_amount, 2)

        # DB insert (optional, as in your original)
        first = items[0] if items else {'product': '', 'description': '', 'units': 0.0, 'price': 0.0, 'discount': 0.0, 'subtotal': 0.0}
        try:
            with closing(db_connect()) as conn:
                cur = conn.cursor()
                cur.execute('''
                    INSERT INTO quotations
                    (quotation_number, company_name, quotation_date, due_date,
                     total_amount, phone, product, description, units, price, discount, subtotal, vat)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    quotation_number, company_name, today, due_date,
                    total, phone, first['product'], first['description'],
                    float(first['units']), float(first['price']), float(first['discount']),
                    float(first['subtotal']), float(vat_amount)
                ))
                conn.commit()
        except Exception as e:
            flash(f"Error saving quotation: {e}", "danger")
            print(e)

        # --------- PDF Generation ---------
        company = get_company_info()
        rendered = render_template(
            'quotation_template.html',
            items=items,
            subtotal=round(subtotal_sum, 2),
            vat=vat_amount,
            total=total,
            today=today,
            due_date=due_date,
            bill_to=bill_to,
            quotation_number=quotation_number,
            company=company
        )

        # Build PDF
        config = None
        if os.name == 'nt':
            wkhtmltopdf_path = r'C:\\Program Files\\wkhtmltopdf\\bin\\wkhtmltopdf.exe'
            if os.path.exists(wkhtmltopdf_path):
                config = pdfkit.configuration(wkhtmltopdf=wkhtmltopdf_path)
            else:
                flash("wkhtmltopdf not found. Check your path.")
                return render_template('quotation_form.html', quotation_number=quotation_number, today=today)
        options = {'enable-local-file-access': '', 'page-size': 'A4', 'encoding': 'UTF-8'}
        pdf_bytes = pdfkit.from_string(rendered, False, configuration=config, options=options)

        # Save on disk
        output_dir = r"C:\flask_project - Setup\quotations"
        os.makedirs(output_dir, exist_ok=True)
        safe_bill_to = company_name.strip().replace(" ", "_").replace("\n", "_")[:30] or "unknown"
        output_filename = f"quotation_{quotation_number}_{safe_bill_to}.pdf"
        output_path = os.path.join(output_dir, output_filename)
        with open(output_path, 'wb') as f:
            f.write(pdf_bytes)

        # Branch by action
        if action == 'print':
            return render_template('print_quotation.html', quotation_number=quotation_number)

        if action == 'send':
            flash("Sending quotations by email not yet implemented.", "warning")
            return render_template('quotation_form.html', quotation_number=quotation_number, today=today)

        # default: download
        return send_file(BytesIO(pdf_bytes), as_attachment=True, download_name=output_filename)

    # GET: Only show the *next* number without incrementing
    return render_template(
        'quotation_form.html',
        quotation_number=peek_next_quotation_number(),
        today=datetime.today().strftime('%d-%m-%Y')
    )

   

@app.route('/find_invoice', methods=['GET', 'POST'])
def find_invoice():
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute('SELECT invoice_number, company_name, invoice_date, total_amount FROM accounts_receivable ORDER BY invoice_number DESC')
        rows = cur.fetchall()
    all_invoices = [
        {
            "invoice_number": r[0],
            "company_name": r[1],
            "invoice_date": r[2],
            "total_amount": "{:.2f}".format(r[3]) if r[3] is not None else "0.00"
        } for r in rows
    ]
    # Always provide company_names (for dropdown and JS)
    company_names = sorted(list({row["company_name"] for row in all_invoices if row["company_name"]}))
    invoices = []
    search_type = None
    search_value = None

    if request.method == 'POST':
        search_type = request.form.get('search_type')
        search_value = request.form.get('search_value', '').strip()
        invoice_dir = r"C:\flask_project - Setup\invoices"
        matches = []
        if search_type == 'invoice_number' and search_value:
            pattern = os.path.join(invoice_dir, f"invoice_{search_value}_*.pdf")
            matches = glob.glob(pattern)
        elif search_type == 'company_name' and search_value:
            safe = search_value.replace(" ", "_").replace("\n", "_")
            pattern = os.path.join(invoice_dir, f"invoice_*_{safe}*.pdf")
            matches = glob.glob(pattern)
        invoices = matches

    # Always return company_names and all_invoices to template!
    return render_template(
        'find_invoice.html',
        all_invoices=all_invoices,
        company_names=company_names,
        invoices=invoices,
        search_type=search_type,
        search_value=search_value,
    )





    
@app.route('/get_invoices_for_company')
def get_invoices_for_company():
    company = request.args.get('company', '').strip().upper()
    if not company:
        return jsonify([])
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute('''SELECT invoice_number, invoice_date, total_amount 
                       FROM accounts_receivable 
                       WHERE company_name = ? 
                       ORDER BY invoice_date DESC''', (company,))
        rows = cur.fetchall()
    return jsonify([
        {"invoice_number": r[0], "invoice_date": r[1], "total_amount": r[2]}
        for r in rows
    ])



@app.route('/get_all_invoices')
def get_all_invoices():
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute('SELECT invoice_number, company_name, invoice_date, total_amount FROM accounts_receivable ORDER BY invoice_number DESC')
        rows = cur.fetchall()
    return jsonify([
        {
            "invoice_number": r[0],
            "company_name": r[1],
            "invoice_date": r[2],
            "total_amount": "{:.2f}".format(r[3]) if r[3] is not None else "0.00"
        } for r in rows
    ])


@app.route('/find_receipt', methods=['GET', 'POST'])
def find_receipt():
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute('SELECT receipt_number, company_name, date FROM receipts ORDER BY receipt_number DESC')
        rows = cur.fetchall()
    all_receipts = [
        {
            "receipt_number": f"{r[0]:04d}" if isinstance(r[0], int) else str(r[0]),
            "company_name": r[1],
            "date": r[2]
        }
        for r in rows
    ]
    company_names = sorted(list({row["company_name"] for row in all_receipts if row["company_name"]}))
    receipts = []
    search_type = None
    search_value = None

    if request.method == 'POST':
        search_type = request.form.get('search_type')
        search_value = request.form.get('search_value', '').strip()
        receipt_dir = r"C:\flask_project - Setup\receipts"
        matches = []
        if search_type == 'receipt_number' and search_value:
            pattern = os.path.join(receipt_dir, f"receipt_{search_value}_*.pdf")
            matches = glob.glob(pattern)
        elif search_type == 'company_name' and search_value:
            # FILTER company (matching company name in all_receipts)
            safe = search_value.replace(" ", "_").replace("\n", "_")
            pattern = os.path.join(receipt_dir, f"receipt_*_{safe}*.pdf")
            matches = glob.glob(pattern)
        receipts = matches

    return render_template(
        'find_receipt.html',
        all_receipts=all_receipts,
        company_names=company_names,
        receipts=receipts,
        search_type=search_type,
        search_value=search_value,
    )



@app.route('/find_quotation', methods=['GET', 'POST'])
def find_quotation():
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute('SELECT quotation_number, company_name, quotation_date FROM quotations ORDER BY quotation_number DESC')
        rows = cur.fetchall()
    all_quotations = [
        {
            "quotation_number": f"{r[0]:04d}" if isinstance(r[0], int) else str(r[0]),
            "company_name": r[1],
            "quotation_date": r[2]
        }
        for r in rows
    ]
    company_names = sorted(list({row["company_name"] for row in all_quotations if row["company_name"]}))

    quotations = []
    search_type = None
    search_value = None

    if request.method == 'POST':
        search_type = request.form.get('search_type')
        search_value = request.form.get('search_value', '').strip()
        with closing(db_connect()) as conn:
            cur = conn.cursor()
            if search_type == 'quotation_number' and search_value:
                cur.execute('''
                    SELECT quotation_number, company_name, quotation_date
                    FROM quotations WHERE quotation_number = ?
                ''', (search_value,))
                rows = cur.fetchall()
            elif search_type == 'company_name' and search_value:
                cur.execute('''
                    SELECT quotation_number, company_name, quotation_date
                    FROM quotations WHERE company_name = ?
                    ORDER BY quotation_number DESC
                ''', (search_value,))
                rows = cur.fetchall()
            else:
                rows = []
        # Now build result list with PDF check
        quotation_dir = r"C:\flask_project - Setup\quotations"
        quotations = []
        for r in rows:
            q_no = f"{r[0]:04d}" if isinstance(r[0], int) else str(r[0])
            cname = r[1]
            qdate = r[2]
            safe_bill_to = (cname or "unknown").strip().replace(" ", "_").replace("\n", "_")[:30]
            pattern = os.path.join(quotation_dir, f"quotation_{q_no}_{safe_bill_to}.pdf")
            pdf_path = pattern if os.path.exists(pattern) else None
            quotations.append({
                "quotation_number": q_no,
                "company_name": cname,
                "quotation_date": qdate,
                "pdf_path": pdf_path
            })

    return render_template(
        'find_quotation.html',
        all_quotations=all_quotations,
        company_names=company_names,
        quotations=quotations,
        search_type=search_type,
        search_value=search_value,
    )


@app.route('/view_quotation/<quotation_number>')
def view_quotation(quotation_number):
    output_dir = r"C:\flask_project - Setup\quotations"
    pattern = os.path.join(output_dir, f"quotation_{quotation_number}_*.pdf")
    matches = glob.glob(pattern)
    if not matches:
        abort(404)
    path = max(matches, key=os.path.getmtime)
    return send_file(path, mimetype='application/pdf', as_attachment=False,
                     download_name=os.path.basename(path))



@app.route('/backup_db')
@admin_required
def backup_db():
    # Ensure backup directory exists
    os.makedirs(BACKUP_DIR, exist_ok=True)
    now_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup_filename = f"customer_backup_{now_str}.db"
    backup_path = os.path.join(BACKUP_DIR, backup_filename)
    try:
        shutil.copy2(DATABASE_FILE, backup_path)
        flash(f"Database backed up as <b>{backup_filename}</b>.", "success")
    except Exception as e:
        flash(f"Backup failed: {e}", "danger")
    return redirect(url_for('dashboard'))  # Change if you want a different page



@app.route('/restore_db', methods=['GET', 'POST'])
@admin_required
def restore_db():
    # Ensure backup directory exists
    os.makedirs(BACKUP_DIR, exist_ok=True)
    # List .db files in backup dir
    files = [f for f in os.listdir(BACKUP_DIR) if f.lower().endswith('.db')]
    files.sort(reverse=True)  # Show latest first if files have timestamp names

    if request.method == 'POST':
        chosen_file = request.form.get('backup_file')
        if not chosen_file or chosen_file not in files:
            flash("Invalid file selected.", "danger")
            return redirect(url_for('restore_db'))
        # FULL PATHS
        src = os.path.join(BACKUP_DIR, chosen_file)
        dst = DATABASE_FILE

        # Make a backup of current db before restore (optional but smart!)
        backup_before_restore = os.path.join(BACKUP_DIR, "currentdb_before_restore.db")
        try:
            if os.path.exists(dst):
                shutil.copy2(dst, backup_before_restore)
            shutil.copy2(src, dst)
            flash(f"Database restored from <b>{chosen_file}</b>.", "success")
        except Exception as e:
            flash(f"Restore failed: {e}", "danger")
        return redirect(url_for('dashboard'))

    return render_template('restore_db.html', backup_files=files)

@app.route('/company_setup', methods=['GET', 'POST'])
@admin_required
def company_setup():
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        if request.method == 'POST':
            data = request.form
            action = request.form.get('action')
            cur.execute("SELECT id FROM company_info ORDER BY updated_at DESC LIMIT 1")
            company_row = cur.fetchone()
            if action == "create":
                # Always delete old and insert new
                cur.execute("DELETE FROM company_info")
                cur.execute('''
                    INSERT INTO company_info (
                        company_name, email, address, city, postal, country, phone, vat_rate, vat,
                        bank_name, account_number, swift, iban
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    data.get('company_name'),
                    data.get('email'),
                    data.get('address'),
                    data.get('city'),
                    data.get('postal'),
                    data.get('country'),
                    data.get('phone'),
                    data.get('vat_rate'),
                    data.get('vat'),
                    data.get('bank_name'),
                    data.get('account_number'),
                    data.get('swift'),
                    data.get('iban'),
                ))
                conn.commit()
                flash("Company profile created.", "success")
            elif action == "update":
                if not company_row:
                    flash("No company exists. Please create first.", "warning")
                else:
                    cur.execute('''
                        UPDATE company_info SET
                            company_name=?, email=?, address=?, city=?, postal=?, country=?, phone=?, vat_rate=?, vat=?,
                            bank_name=?, account_number=?, swift=?, iban=?, updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                    ''', (
                        data.get('company_name'),
                        data.get('email'),
                        data.get('address'),
                        data.get('city'),
                        data.get('postal'),
                        data.get('country'),
                        data.get('phone'),
                        data.get('vat_rate'),
                        data.get('vat'),
                        data.get('bank_name'),
                        data.get('account_number'),
                        data.get('swift'),
                        data.get('iban'),
                        company_row[0],
                    ))
                    conn.commit()
                    flash("Company profile updated.", "success")
        # Always load the latest company info for the form (so fields are never blank)
        cur.execute("SELECT * FROM company_info ORDER BY updated_at DESC LIMIT 1")
        company = cur.fetchone()
        keys = [d[0] for d in cur.description]
        company_dict = dict(zip(keys, company)) if company else {}
    return render_template('company_setup_form.html', company=company_dict)


DATABASE = 'customer.db'

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

    
from flask import render_template, request, redirect, url_for, flash
from contextlib import closing

from flask import render_template, request, redirect, url_for, flash
from contextlib import closing

@app.route('/product_inventory', methods=['GET', 'POST'])
def product_inventory():
    imported_count = None  # Always define at the start

    # --- Handle Excel import, if needed ---
    if request.method == 'POST' and 'excel_file' in request.files:
        excel_file = request.files['excel_file']
        # Implement your Excel import logic here...
        # imported_count = <number_imported>
        pass

    # --- Handle Product Add Form ---
    elif request.method == 'POST':
        # Get fields from form
        low_inv = request.form.get('low_inv')
        Inventory_ID = request.form.get('Inventory_ID')
        company = request.form.get('company')
        description = request.form.get('description')
        unit_price = float(request.form.get('unit_price', 0) or 0)
        quantity_in_stock = int(request.form.get('quantity_in_stock', 0) or 0)
        reorder_level = int(request.form.get('reorder_level', 0) or 0)
        reorder_time_in_days = int(request.form.get('reorder_time_in_days', 0) or 0)
        quantity_in_reorder = int(request.form.get('quantity_in_reorder', 0) or 0)
        discontinued = request.form.get('discontinued')

        # Always calculate inventory value
        inventory_value = unit_price * quantity_in_stock

        # Insert into DB
        with closing(db_connect()) as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO product_inventory
                ("Low Inv", "Inventory ID", "Company", "Description", "Unit price",
                 "Quantity in stock", "Inventory value", "Reorder level",
                 "Reorder time in days", "Quantity in reorder", "Discontinued")
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                low_inv, Inventory_ID, company, description, unit_price,
                quantity_in_stock, inventory_value, reorder_level,
                reorder_time_in_days, quantity_in_reorder, discontinued
            ))
            conn.commit()
        flash('Product added to inventory!', 'success')
        return redirect(url_for('product_inventory'))

    # --- Handle GET: filter logic for Low Inventory ---
    filter_type = request.args.get('filter', None)

    with closing(db_connect()) as conn:
        cur = conn.cursor()
        # Get filtered items (if any)
        if filter_type == 'low_stock':
            cur.execute("SELECT * FROM product_inventory WHERE [Low Inv] = 1")
        else:
            cur.execute("SELECT * FROM product_inventory")
        inventory_items = cur.fetchall()

        # Get total inventory value (sum)
        cur.execute("SELECT SUM([Inventory value]) FROM product_inventory")
        result = cur.fetchone()
        total_value = result[0] or 0

        # Get total quantity in stock (sum)
        cur.execute("SELECT SUM([Quantity in stock]) FROM product_inventory")
        result = cur.fetchone()
        total_quantity = result[0] or 0

    return render_template(
        'product_inventory.html',
        inventory_items=inventory_items,
        inventory_count=len(inventory_items),
        total_quantity=total_quantity,
        total_value=total_value,
        imported_count=imported_count
    )


@app.route('/get_unit_price')
def get_unit_price():
    inventory_id = request.args.get('inventory_id')
    description = request.args.get('description')
    con = get_db()
    cur = con.cursor()
    cur.execute(
        'SELECT "Unit price" FROM product_inventory WHERE "Inventory ID"=? AND "Description"=?',
        (inventory_id, description)
    )
    row = cur.fetchone()
    if row:
        return jsonify({'unit_price': row[0]})
    else:
        return jsonify({'unit_price': None})


@app.route('/merge_inventory_duplicates')
def merge_inventory_duplicates():
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        # Clean whitespace and make case consistent
        cur.execute("""
            UPDATE product_inventory
            SET 
              "Inventory ID" = TRIM(UPPER("Inventory ID")),
              "Company" = TRIM(UPPER("Company")),
              "Description" = TRIM(UPPER("Description"))
        """)
        conn.commit()

        # Find duplicates (case/whitespace-insensitive now)
        cur.execute("""
            SELECT "Inventory ID", "Company", "Description", COUNT(*)
            FROM product_inventory
            GROUP BY "Inventory ID", "Company", "Description"
            HAVING COUNT(*) > 1
        """)
        dupes = cur.fetchall()
        merged, deleted = 0, 0
        for inv_id, company, desc, count in dupes:
            cur.execute("""
                SELECT id, "Quantity in stock", "Unit price"
                FROM product_inventory
                WHERE "Inventory ID"=? AND "Company"=? AND "Description"=?
                ORDER BY id
            """, (inv_id, company, desc))
            rows = cur.fetchall()
            if len(rows) < 2:
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
                deleted += 1
        conn.commit()
    return f"Merged {merged} sets, deleted {deleted} duplicates. <a href='/product_inventory'>Back</a>"


from flask import render_template, request
import pandas as pd
import sqlite3

@app.route('/delete_inventory', methods=['POST'])
def delete_inventory():
    item_id = request.form.get('id')
    if not item_id:
        flash('Missing item ID!', 'danger')
        return redirect(url_for('product_inventory'))

    import sqlite3
    conn = sqlite3.connect('customer.db')
    cur = conn.cursor()
    cur.execute("DELETE FROM product_inventory WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    flash('Item deleted!', 'success')
    return redirect(url_for('product_inventory'))

@app.route('/import_inventory', methods=['POST'])
def import_inventory():
    from flask import session, redirect, url_for, request
    import pandas as pd
    import sqlite3

    file = request.files.get('excel_file')
    if file and file.filename.endswith('.xlsx'):
        try:
            df = pd.read_excel(file)
            # ... process/rename columns as needed ...
            # Insert each row to DB as usual
            conn = sqlite3.connect('customer.db')
            cursor = conn.cursor()
            for _, row in df.iterrows():
                cursor.execute("""
                    INSERT INTO product_inventory (
                        "Low Inv", "Inventory ID", "Company", "Description", "Unit price",
                        "Quantity in stock", "Inventory value", "Reorder level",
                        "Reorder time in days", "Quantity in reorder", "Discontinued"
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    row.get("Low Inv"),
                    row.get("Inventory ID"),
                    row.get("Company"),
                    row.get("Description"),
                    row.get("Unit price"),
                    row.get("Quantity in stock"),
                    row.get("Inventory value"),
                    row.get("Reorder level"),
                    row.get("Reorder time in days"),
                    row.get("Quantity in reorder"),
                    row.get("Discontinued")
                ))
            conn.commit()
            conn.close()
            imported_count = len(df)
        except Exception as e:
            imported_count = -1
    else:
        imported_count = -1

    # Store count in session and redirect so message shows up on inventory page
    session['imported_count'] = imported_count
    return redirect(url_for('product_inventory'))


@app.route('/delete_all_inventory', methods=['POST'])
def delete_all_inventory():
    conn = sqlite3.connect('customer.db')
    cursor = conn.cursor()
    cursor.execute('DELETE FROM product_inventory')
    conn.commit()
    conn.close()
    return redirect('/product_inventory')
    
@app.route('/edit_inventory', methods=['GET', 'POST'])
def edit_inventory():
    # Get the product ID from query or form
    id = request.values.get('id')
    if not id:
        flash("No inventory item selected for editing.", "danger")
        return redirect(url_for('product_inventory'))
    
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        if request.method == 'POST':
            # Get all updated form fields
            Inventory_ID = request.form.get('Inventory_ID', '').strip()
            company = request.form.get('company', '').strip()
            description = request.form.get('description', '').strip()
            try:
                unit_price = float(request.form.get('unit_price', 0))
            except:
                unit_price = 0
            try:
                quantity_in_stock = int(request.form.get('quantity_in_stock', 0))
            except:
                quantity_in_stock = 0

            # *** Prevent negative quantity in stock ***
            if quantity_in_stock < 0:
                quantity_in_stock = 0

            try:
                reorder_level = int(request.form.get('reorder_level', 0))
            except:
                reorder_level = 0
            try:
                reorder_time_in_days = int(request.form.get('reorder_time_in_days', 0))
            except:
                reorder_time_in_days = 0
            try:
                quantity_in_reorder = int(request.form.get('quantity_in_reorder', 0))
            except:
                quantity_in_reorder = 0
            discontinued = request.form.get('discontinued', '0')
            
            # Calculate new Inventory Value
            inventory_value = float(unit_price) * int(quantity_in_stock)
            
            # Recalculate Low Inv flag
            low_inv = 1 if quantity_in_stock < reorder_level else 0

            # Update in DB
            cur.execute('''
                UPDATE product_inventory
                SET 
                    "Low Inv" = ?,
                    "Inventory ID" = ?,
                    "Company" = ?,
                    "Description" = ?,
                    "Unit price" = ?,
                    "Quantity in stock" = ?,
                    "Inventory value" = ?,
                    "Reorder level" = ?,
                    "Reorder time in days" = ?,
                    "Quantity in reorder" = ?,
                    "Discontinued" = ?
                WHERE id = ?
            ''', (
                low_inv,
                Inventory_ID,
                company,
                description,
                unit_price,
                quantity_in_stock,
                inventory_value,
                reorder_level,
                reorder_time_in_days,
                quantity_in_reorder,
                discontinued,
                id
            ))
            conn.commit()
            flash("Inventory item updated.", "success")
            return redirect(url_for('product_inventory'))

        # GET method: fetch row to populate form
        cur.execute("SELECT * FROM product_inventory WHERE id = ?", (id,))
        row = cur.fetchone()
        if not row:
            flash("Inventory item not found.", "danger")
            return redirect(url_for('product_inventory'))

    return render_template('edit_inventory.html', row=row)

@app.route('/add_new_product', methods=['GET', 'POST'])
def add_new_product():
    if request.method == 'POST':
        Inventory_ID = request.form.get('Inventory_ID', '').strip()
        company = request.form.get('company', '').strip()
        description = request.form.get('description', '').strip()
        try:
            unit_price = float(request.form.get('unit_price', 0) or 0)
        except:
            unit_price = 0
        try:
            quantity_in_stock = int(request.form.get('quantity_in_stock', 0) or 0)
        except:
            quantity_in_stock = 0
        try:
            reorder_level = int(request.form.get('reorder_level', 0) or 0)
        except:
            reorder_level = 0
        try:
            reorder_time_in_days = int(request.form.get('reorder_time_in_days', 0) or 0)
        except:
            reorder_time_in_days = 0
        try:
            quantity_in_reorder = int(request.form.get('quantity_in_reorder', 0) or 0)
        except:
            quantity_in_reorder = 0

        # Calculate inventory_value
        inventory_value = unit_price * quantity_in_stock

        # Optionally: Calculate Low Inv (if your DB needs it)
        low_inv = 1 if quantity_in_stock < reorder_level else 0

        # Insert into DB
        with closing(db_connect()) as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO product_inventory
                ("Low Inv", "Inventory ID", "Company", "Description", "Unit price", 
                 "Quantity in stock", "Inventory value", "Reorder level", 
                 "Reorder time in days", "Quantity in reorder", "Discontinued")
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                low_inv, Inventory_ID, company, description, unit_price,
                quantity_in_stock, inventory_value, reorder_level,
                reorder_time_in_days, quantity_in_reorder, 'No'
            ))
            conn.commit()
        flash("Product added!", "success")
        return redirect(url_for('add_new_product'))

    return render_template('add_new_product.html')

@app.route('/add_product', methods=['GET', 'POST'])
def add_product():
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute('SELECT DISTINCT "Inventory ID" FROM product_inventory WHERE "Inventory ID" IS NOT NULL AND TRIM("Inventory ID") != ""')
        Inventory_IDs = [row[0] for row in cur.fetchall()]
        cur.execute('SELECT DISTINCT "Company" FROM product_inventory WHERE "Company" IS NOT NULL AND TRIM("Company") != ""')
        companies = [row[0] for row in cur.fetchall()]
        cur.execute('SELECT DISTINCT "Description" FROM product_inventory WHERE "Description" IS NOT NULL AND TRIM("Description") != ""')
        descriptions = [row[0] for row in cur.fetchall()]

    if request.method == 'POST':
        Inventory_ID = (request.form.get('Inventory_ID') or '').strip().upper()
        company = (request.form.get('company') or '').strip().upper()
        description = (request.form.get('description') or '').strip().upper()
        try:
            unit_price = float(request.form.get('unit_price') or 0)
        except:
            unit_price = 0
        try:
            quantity = int(request.form.get('quantity') or 0)
        except:
            quantity = 0

        print("Form Values:")
        print("Inventory_ID:", Inventory_ID)
        print("Company:", company)
        print("Description:", description)

        conn = db_connect()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, "Quantity in stock" FROM product_inventory
            WHERE TRIM(UPPER("Inventory ID"))=? AND TRIM(UPPER("Company"))=? AND TRIM(UPPER("Description"))=?
        """, (Inventory_ID, company, description))
        row = cur.fetchone()
        print("Row found in DB:", row)

        if row:
            product_id, qty_in_stock = row
            new_qty = (qty_in_stock or 0) + quantity
            cur.execute("""
                UPDATE product_inventory
                SET "Quantity in stock"=?, "Unit price"=?
                WHERE id=?
            """, (new_qty, unit_price, product_id))
            print("Updated existing product!")
        else:
            inventory_value = unit_price * quantity
            cur.execute("""
                INSERT INTO product_inventory
                ("Low Inv", "Inventory ID", "Company", "Description", "Unit price", 
                "Quantity in stock", "Inventory value", "Reorder level", 
                "Reorder time in days", "Quantity in reorder", "Discontinued")
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                0, Inventory_ID, company, description, unit_price,
                quantity, inventory_value, 0, 0, 0, 'No'
            ))
            print("Inserted new product!")

        conn.commit()
        conn.close()

        flash("Product quantity added!", "success")
        return redirect(url_for('add_product'))

    return render_template('add_product.html',
        Inventory_IDs=Inventory_IDs,
        companies=companies,
        descriptions=descriptions
    )


@app.route('/force_change_password', methods=['GET', 'POST'])
def force_change_password():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    if request.method == 'POST':
        if 'keep_password' in request.form:
            # User wants to keep current password
            with closing(db_connect()) as conn:
                conn.execute("UPDATE users SET must_change_password=0 WHERE id=?", (user_id,))
                conn.commit()
            flash("You kept your current password.", "success")
            return redirect(url_for('dashboard'))
        new_password = request.form.get('new_password', '').strip()
        if new_password:
            # Add your hashing here
            from werkzeug.security import generate_password_hash
            password_hash = generate_password_hash(new_password)
            with closing(db_connect()) as conn:
                conn.execute("UPDATE users SET password_hash=?, must_change_password=0 WHERE id=?", (password_hash, user_id))
                conn.commit()
            flash("Password changed successfully.", "success")
            return redirect(url_for('dashboard'))
        else:
            flash("Please enter a new password or choose to keep your current one.", "danger")
    return render_template('force_change_password.html')


from flask import session, redirect, url_for, flash, render_template, request
from werkzeug.security import generate_password_hash, check_password_hash

@app.route('/change_password', methods=['GET', 'POST'])
def change_password():
    if 'user_id' not in session:
        flash("You must be logged in to change your password.", "danger")
        return redirect(url_for('login'))
    user_id = session['user_id']

    if request.method == 'POST':
        current_password = request.form.get('current_password', '').strip()
        new_password = request.form.get('new_password', '').strip()
        confirm_password = request.form.get('confirm_password', '').strip()

        # Fetch the user's current password hash
        with closing(db_connect()) as conn:
            cur = conn.cursor()
            cur.execute('SELECT password_hash FROM users WHERE id=?', (user_id,))
            row = cur.fetchone()
            if not row or not check_password_hash(row[0], current_password):
                flash("Current password is incorrect.", "danger")
                return render_template('change_password.html')

        if not new_password or len(new_password) < 6:
            flash("New password must be at least 6 characters.", "danger")
            return render_template('change_password.html')
        if new_password != confirm_password:
            flash("Passwords do not match.", "danger")
            return render_template('change_password.html')

        # Update password
        password_hash = generate_password_hash(new_password)
        with closing(db_connect()) as conn:
            conn.execute('UPDATE users SET password_hash=? WHERE id=?', (password_hash, user_id))
            conn.commit()
        flash("Password changed successfully!", "success")
        return redirect(url_for('dashboard'))

    return render_template('change_password.html')


def get_months_years():
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT strftime('%m', invoice_date) AS month, strftime('%Y', invoice_date) AS year
            FROM accounts_receivable
            WHERE invoice_date IS NOT NULL AND LENGTH(invoice_date) >= 7
            ORDER BY year DESC, month DESC
        """)
        return [(row[0], row[1]) for row in cur.fetchall() if row[0] and row[1]]

@app.route('/visual_trends/revenue', methods=['GET', 'POST'])
@login_required
def visual_trends_revenue():
    months_years = get_months_years()
    # Flatten to lists for dropdowns
    months = sorted({m for m, y in months_years})
    years = sorted({y for m, y in months_years}, reverse=True)

    selected_month = request.values.get('month')
    selected_year = request.values.get('year')

    # Fix: treat "All"/None as no filter
    filter_active = (
        selected_month not in (None, '', 'All', 'None') and
        selected_year not in (None, '', 'All', 'None')
    )
    month_str = f"{selected_month}-{selected_year}" if filter_active else None

    with closing(db_connect()) as conn:
        cur = conn.cursor()
        # Revenue by customer
        if month_str:
            cur.execute("""
                SELECT company_name, SUM(total_amount)
                FROM accounts_receivable
                WHERE strftime('%m-%Y', invoice_date) = ?
                  AND paid_status <> 'VOID'
                GROUP BY company_name
                ORDER BY SUM(total_amount) DESC
            """, (month_str,))
        else:
            cur.execute("""
                SELECT company_name, SUM(total_amount)
                FROM accounts_receivable
                WHERE paid_status <> 'VOID'
                GROUP BY company_name
                ORDER BY SUM(total_amount) DESC
            """)
        rows = cur.fetchall()
        revenue_by_customer = {'labels': [r[0] for r in rows], 'data': [float(r[1] or 0) for r in rows]}

        # Revenue by product
        if month_str:
            cur.execute("""
                SELECT product, SUM(subtotal)
                FROM accounts_receivable
                WHERE paid_status <> 'VOID'
                  AND strftime('%m-%Y', invoice_date) = ?
                GROUP BY product
                ORDER BY SUM(subtotal) DESC
            """, (month_str,))
        else:
            cur.execute("""
                SELECT product, SUM(subtotal)
                FROM accounts_receivable
                WHERE paid_status <> 'VOID'
                GROUP BY product
                ORDER BY SUM(subtotal) DESC
            """)
        rows2 = cur.fetchall()
        revenue_by_product = {'labels': [r[0] or 'Unknown' for r in rows2], 'data': [float(r[1] or 0) for r in rows2]}

    return render_template(
        'Visual_Trends_Revenue_Charts.html',
        revenue_by_customer=revenue_by_customer,
        revenue_by_product=revenue_by_product,
        months=months,
        years=years,
        selected_month=selected_month,
        selected_year=selected_year,
        revenue_pie_data=revenue_by_customer
    )



@app.route('/visual_trends/inventory', methods=['GET', 'POST'])
def visual_trends_inventory():
    months_years = get_months_years()
    selected_month = request.values.get('month')
    selected_year = request.values.get('year')
    month_str = f"{selected_month}-{selected_year}" if selected_month and selected_year else None

    with closing(db_connect()) as conn:
        cur = conn.cursor()
        # Filter as above
        if month_str:
            cur.execute("""
                SELECT product, SUM(quantity) as qty
                FROM inventory_trends
                WHERE strftime('%m-%Y', date) = ?
                GROUP BY product
                ORDER BY qty DESC
            """, (month_str,))
        else:
            cur.execute("""
                SELECT product, SUM(quantity) as qty
                FROM inventory_trends
                GROUP BY product
                ORDER BY qty DESC
            """)
        rows = cur.fetchall()
        inventory_trends_data = {'labels': [r[0] for r in rows], 'data': [float(r[1] or 0) for r in rows]}

    return render_template(
        'Visual_Trends_Inventory_Trends.html',
        inventory_trends_data=inventory_trends_data,
        months_years=months_years,
        selected_month=selected_month,
        selected_year=selected_year
    )

@app.route('/visual_trends/payments', methods=['GET', 'POST'])
def visual_trends_payments():
    months_years = get_months_years()
    selected_month = request.values.get('month')
    selected_year = request.values.get('year')
    month_str = f"{selected_month}-{selected_year}" if selected_month and selected_year else None

    with closing(db_connect()) as conn:
        cur = conn.cursor()
        # Filter by selected month/year if chosen
        if month_str:
            cur.execute("""
                SELECT paid_status, SUM(total_amount)
                FROM accounts_receivable
                WHERE strftime('%m-%Y', invoice_date) = ?
                GROUP BY paid_status
                ORDER BY SUM(total_amount) DESC
            """, (month_str,))
        else:
            cur.execute("""
                SELECT paid_status, SUM(total_amount)
                FROM accounts_receivable
                GROUP BY paid_status
                ORDER BY SUM(total_amount) DESC
            """)
        rows = cur.fetchall()
        payment_trends_data = {
            'labels': [r[0] for r in rows],
            'data': [float(r[1] or 0) for r in rows]
        }

    return render_template(
        'Visual_Trends_Payment_Trends.html',
        payment_trends_data=payment_trends_data,
        months_years=months_years,
        selected_month=selected_month,
        selected_year=selected_year
    )


@app.route('/get_sizes_for_inventory_id')
def get_sizes_for_inventory_id():
    inventory_id = request.args.get('inventory_id', '').strip()
    if not inventory_id:
        return jsonify([])  # No id sent!
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute('''
            SELECT DISTINCT "Description"
            FROM product_inventory
            WHERE "Inventory ID" = ?
              AND "Description" IS NOT NULL
              AND TRIM("Description") != ''
        ''', (inventory_id,))
        sizes = [row[0] for row in cur.fetchall()]
    return jsonify(sizes)

@app.route('/get_inventory_ids')
def get_inventory_ids():
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute('SELECT DISTINCT "Inventory ID" FROM product_inventory WHERE "Inventory ID" IS NOT NULL AND TRIM("Inventory ID") != ""')
        ids = [row[0] for row in cur.fetchall()]
    return jsonify(ids)

def send_support_email(user_email, subject, problem_text):
    import smtplib
    from email.mime.text import MIMEText

    sender_email = "ipapaiacovou@gmail.com"
    sender_password = "iznhqayimzmukmmt"
    receiver_email = "ipapaiacovou@gmail.com"

    msg_subject = f"[Support] {subject} (from {user_email})"
    body = f"Support request from: {user_email}\nSubject: {subject}\n\nProblem Description:\n{problem_text}"

    msg = MIMEText(body)
    msg["Subject"] = msg_subject
    msg["From"] = sender_email
    msg["To"] = receiver_email

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, receiver_email, msg.as_string())
    except Exception as e:
        import traceback
        print("Email sending failed:")
        traceback.print_exc()



import sqlite3
from flask import render_template, request, redirect, url_for, flash

@app.route('/support', methods=['GET', 'POST'])
def support():
    import random
    company_name = "Your Company Name"
    try:
        conn = sqlite3.connect('customer.db')
        cursor = conn.cursor()
        cursor.execute("SELECT company_name FROM company_info LIMIT 1;")
        row = cursor.fetchone()
        if row and row[0]:
            company_name = row[0]
        conn.close()
    except Exception as e:
        print("Could not fetch company name from company_info:", e)

    # Generate new CAPTCHA for GET or failed POST
    num1, num2 = random.randint(1, 9), random.randint(1, 9)
    captcha_sum = str(num1 + num2)

    if request.method == 'POST':
        user_email = request.form.get('email')
        subject = request.form.get('subject')
        problem_text = request.form.get('problem')

        # CAPTCHA check
        user_captcha = request.form.get('captcha_answer', '').strip()
        captcha_sum_hidden = request.form.get('captcha_sum', '')
        if not captcha_sum_hidden or user_captcha != captcha_sum_hidden:
            flash("CAPTCHA failed. Please answer the math question correctly.", "error")
            # Re-render form with new CAPTCHA
            return render_template(
                'support.html',
                company_name=company_name,
                num1=num1, num2=num2, captcha_sum=captcha_sum,
                request=request
            )

        send_support_email(user_email, subject, problem_text)
        flash("Your support request has been sent. We will get back to you soon!", "success")
        return redirect(url_for('support'))

    # On GET, render with random math question
    return render_template(
        'support.html',
        company_name=company_name,
        num1=num1, num2=num2, captcha_sum=captcha_sum,
        request=request
    )

@app.route('/about_us')
def about_us():
    import sqlite3
    conn = sqlite3.connect('customer.db')
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    # Get company info (assuming only one row)
    cur.execute('SELECT company_name, email FROM company_info LIMIT 1')
    company_row = cur.fetchone()
    company_name = company_row['company_name'] if company_row else 'Your Company'
    company_email = company_row['email'] if company_row else 'support@example.com'
    # Get users for team
    cur.execute('SELECT * FROM users')
    users = cur.fetchall()
    return render_template(
        'about_us.html',
        company_name=company_name,
        company_email=company_email,
        users=users
    )

@app.route('/get_stock_quantity')
def get_stock_quantity():
    inventory_id = request.args.get('inventory_id')
    description = request.args.get('description')
    if not inventory_id or not description:
        return jsonify({'error': 'Missing params'}), 400
    import sqlite3
    conn = sqlite3.connect('customer.db')
    c = conn.cursor()
    c.execute("""
        SELECT [Quantity in stock]
        FROM product_inventory
        WHERE [Inventory ID] = ? AND [Description] = ?
    """, (inventory_id, description))
    row = c.fetchone()
    conn.close()
    if row and row[0] is not None:
        return jsonify({'quantity': row[0]})
    else:
        return jsonify({'quantity': 0})

@app.route('/debug_inventory')
def debug_inventory():
    import sqlite3
    conn = sqlite3.connect('customer.db')
    c = conn.cursor()
    c.execute("SELECT * FROM product_inventory LIMIT 3")
    rows = c.fetchall()
    colnames = [description[0] for description in c.description]
    conn.close()
    return {'columns': colnames, 'rows': rows}

@app.route('/documentation')
@login_required  # Optional: only if you want to restrict to logged-in users
def documentation():
    return render_template('documentation.html')

def get_db_connection():
    conn = sqlite3.connect('customer.db')
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/suppliers', methods=['GET', 'POST'])
def suppliers():
    # Load company_info as before
    company_info = get_company_info()  # <-- Replace with your logic

    if request.method == 'POST':
        supplier = request.form.get('supplier', '').strip().upper()
        contact = request.form.get('contact', '').strip().upper()
        phone = request.form.get('phone', '').strip().upper()
        fax = request.form.get('fax', '').strip().upper()
        email = request.form.get('email', '').strip().lower()
        website = request.form.get('website', '').strip().lower()
        address = request.form.get('address', '').strip().upper()
        city = request.form.get('city', '').strip().upper()
        region = request.form.get('region', '').strip().upper()
        postal = request.form.get('postal', '').strip().upper()
        country = request.form.get('country', '').strip().upper()

        # Connect to DB
        conn = sqlite3.connect('customer.db')
        c = conn.cursor()
        # Check for duplicate supplier (case-insensitive)
        c.execute('SELECT 1 FROM suppliers WHERE UPPER(supplier) = ?', (supplier,))
        exists = c.fetchone()
        if exists:
            flash('Supplier already exists!', 'error')
            conn.close()
            return render_template('supplier_form.html', company_info=company_info)

        # Insert if not duplicate
        c.execute('''
            INSERT INTO suppliers (
                supplier, contact, phone, fax, email, website,
                address, city, region, postal, country
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            supplier, contact, phone, fax, email, website,
            address, city, region, postal, country
        ))
        conn.commit()
        conn.close()
        flash('Supplier added successfully!', 'success')
        return redirect(url_for('suppliers'))

    return render_template('supplier_form.html', company_info=company_info)

@app.route('/suppliers_list')
def list_suppliers():
    # get_company_info() must return a value, or use a default!
    company_info = get_company_info() if 'get_company_info' in globals() else {}

    import sqlite3
    conn = sqlite3.connect('customer.db')
    conn.row_factory = sqlite3.Row  # so columns are accessible by name
    cur = conn.cursor()
    cur.execute('SELECT * FROM suppliers ORDER BY supplier ASC')
    suppliers = cur.fetchall()
    conn.close()

    # You must return a response!
    return render_template('suppliers.html', suppliers=suppliers, company_info=company_info)

# All imports should be at the very top, not indented
from flask import send_file
import sqlite3
import os
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

@app.route('/export_suppliers_pdf')
def export_suppliers_pdf():
    company_info = get_company_info() if 'get_company_info' in globals() else {}
    company_name = company_info.get("company_name", "Company Name")

    # Get suppliers from DB
    conn = sqlite3.connect('customer.db')
    conn.row_factory = sqlite3.Row
    suppliers = conn.execute('SELECT * FROM suppliers ORDER BY supplier ASC').fetchall()
    conn.close()

    # Output path for saving PDF
    output_dir = r"C:\flask_project - Setup\suppliers-list"
    os.makedirs(output_dir, exist_ok=True)
    pdf_path = os.path.join(output_dir, "suppliers_list.pdf")

    # Create PDF in memory
    from io import BytesIO
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(A4),
        leftMargin=16*mm, rightMargin=16*mm, topMargin=18*mm, bottomMargin=16*mm
    )
    elements = []

    styles = getSampleStyleSheet()
    title = Paragraph(f"<b>{company_name}</b>", styles['Title'])
    subtitle = Paragraph("Supplier List", styles['Heading2'])
    elements.append(title)
    elements.append(subtitle)
    elements.append(Spacer(1, 12))

    # Table data (NO website column)
    data = [
        ['Supplier', 'Contact', 'Phone', 'Fax', 'Email', 'Address', 'City', 'Region', 'Postal', 'Country']
    ]
    for s in suppliers:
        data.append([
            s['supplier'], s['contact'], s['phone'], s['fax'], s['email'],
            s['address'], s['city'], s['region'], s['postal'], s['country']
        ])

    # Adjusted column widths for more space for contact and address, phone is narrower
    col_widths = [
        32*mm,   # Supplier
        36*mm,   # Contact (MORE SPACE)
        18*mm,   # Phone (8 digits fits)
        20*mm,   # Fax
        38*mm,   # Email
        56*mm,   # Address (MORE SPACE)
        23*mm,   # City
        21*mm,   # Region
        15*mm,   # Postal
        22*mm    # Country
    ]

    t = Table(data, repeatRows=1, colWidths=col_widths)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#2359a3")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('ROWHEIGHT', (0, 0), (-1, -1), 15),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 5),
        ('TOPPADDING', (0, 0), (-1, 0), 5),
        ('GRID', (0, 0), (-1, -1), 0.3, colors.HexColor("#b0b6be")),
        ('BACKGROUND', (0, 1), (-1, -1), colors.whitesmoke),
    ]))
    elements.append(t)
    doc.build(elements)
    buffer.seek(0)

    # Save to disk
    with open(pdf_path, "wb") as f:
        f.write(buffer.getbuffer())

    # Return to browser
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name="suppliers_list.pdf", mimetype='application/pdf')



from flask import request, flash, redirect, url_for, render_template
import sqlite3
@app.route('/edit_supplier/<int:supplier_id>', methods=['GET', 'POST'])
def edit_supplier(supplier_id):
    company_info = get_company_info() if 'get_company_info' in globals() else {}

    conn = sqlite3.connect('customer.db')
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    if request.method == 'GET':
        cur.execute('SELECT * FROM suppliers WHERE id = ?', (supplier_id,))
        supplier = cur.fetchone()
        conn.close()
        if supplier:
            return render_template('edit_supplier.html', supplier=supplier, company_info=company_info)
        else:
            flash('Supplier not found.', 'danger')
            return redirect(url_for('list_suppliers'))

    # POST - update supplier
    supplier = request.form.get('supplier', '').upper()
    contact = request.form.get('contact', '').upper()
    phone = request.form.get('phone', '').upper()
    fax = request.form.get('fax', '').upper()
    email = request.form.get('email', '').lower()
    website = request.form.get('website', '').lower()
    address = request.form.get('address', '').upper()
    city = request.form.get('city', '').upper()
    region = request.form.get('region', '').upper()
    postal = request.form.get('postal', '').upper()
    country = request.form.get('country', '').upper()

    cur.execute('''
        UPDATE suppliers
        SET supplier = ?, contact = ?, phone = ?, fax = ?, email = ?, website = ?, address = ?, city = ?, region = ?, postal = ?, country = ?
        WHERE id = ?
    ''', (supplier, contact, phone, fax, email, website, address, city, region, postal, country, supplier_id))
    conn.commit()
    conn.close()
    flash('Supplier updated successfully!', 'success')
    return redirect(url_for('list_suppliers'))

@app.route('/delete_supplier/<int:supplier_id>')
def delete_supplier(supplier_id):
    conn = sqlite3.connect('customer.db')
    cur = conn.cursor()
    cur.execute("DELETE FROM suppliers WHERE id = ?", (supplier_id,))
    conn.commit()
    conn.close()
    flash("Supplier deleted.", "success")
    return redirect(url_for('list_suppliers'))

@app.route('/login_sessions')
@admin_required  # Only admins can view this page!
def login_sessions():
    with closing(db_connect()) as conn:
        cur = conn.cursor()
        cur.execute('''
            SELECT username, login_time, logout_time
              FROM login_session
             ORDER BY login_time DESC
        ''')
        sessions = cur.fetchall()
    return render_template('login_sessions.html', sessions=sessions)


@app.route('/set_language/<lang>')
def set_language(lang):
    session['lang'] = lang
    return redirect(request.referrer or url_for('index'))


@app.route('/test_ocr')
def test_ocr():
    return "OCR Test is working!"

def extract_invoice_number(text):
    # Try the most common label patterns first
    patterns = [
        r'Invoice\s*#\s*[:\-]?\s*([A-Z0-9\-\/]+)',              # Invoice #: 0125
        r'Invoice\s*No\.?\s*[:\-]?\s*([A-Z0-9\-\/]+)',           # Invoice No: 123
        r'Inv(?:oice)?\s*Number\s*[:\-]?\s*([A-Z0-9\-\/]+)',     # Invoice Number: 456
        r'No\.\s*([A-Z0-9\-\/]+)',                              # No. 789
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            return m.group(1).strip()
    return ''

def extract_amount(text):
    patterns = [
        r'Total\s*[:=]\s*€?\s*([\d\.,]+)',
        r'Amount\s*Due\s*[:=]\s*€?\s*([\d\.,]+)',
        r'Balance\s*Due\s*[:=]\s*€?\s*([\d\.,]+)',
        r'€\s*([\d\.,]+)',  # Fallback: just an euro amount
        r'\$\s*([\d\.,]+)', # Fallback: dollar amount
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            return m.group(1).replace(',', '').strip()
    return ''

def extract_vendor(text):
    patterns = [
        r'From:\s*([A-Za-z0-9 &\.\,\-]+)', 
        r'Supplier:\s*([A-Za-z0-9 &\.\,\-]+)',
        r'Vendor:\s*([A-Za-z0-9 &\.\,\-]+)',
        r'\n([A-Z][A-Z0-9 &\.\,\-]{2,})\n',  # ALL CAPS company line
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return m.group(1).strip()
    return ''

def extract_invoice_date(text):
    patterns = [
        r'Invoice\s*Date\s*[:\-]?\s*([0-9]{4}-[0-9]{2}-[0-9]{2})',         # Invoice Date: 2023-12-31
        r'Invoice\s*Date\s*[:\-]?\s*([0-9]{2}/[0-9]{2}/[0-9]{4})',         # Invoice Date: 31/12/2023
        r'Date\s*[:\-]?\s*([0-9]{4}-[0-9]{2}-[0-9]{2})',                   # Date: 2023-12-31
        r'Date\s*[:\-]?\s*([0-9]{2}/[0-9]{2}/[0-9]{4})',                   # Date: 31/12/2023
        r'Issue\s*Date\s*[:\-]?\s*([0-9]{4}-[0-9]{2}-[0-9]{2})',           # Issue Date: 2023-12-31
        r'Issue\s*Date\s*[:\-]?\s*([0-9]{2}/[0-9]{2}/[0-9]{4})',           # Issue Date: 31/12/2023
        r'([0-9]{4}-[0-9]{2}-[0-9]{2})',                                   # Just any yyyy-mm-dd
        r'([0-9]{2}/[0-9]{2}/[0-9]{4})',                                   # Just any dd/mm/yyyy
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return m.group(1)
    return ''


import pytesseract
from PIL import Image
import pdf2image
import tempfile
import re
import os
from flask import request, render_template

@app.route('/ap_ocr', methods=['GET', 'POST'])
def ap_ocr():
    extracted = {}
    ocr_text = ""
    if request.method == 'POST':
        file = request.files.get('invoice_scan')
        if file and file.filename:
            temp_path = os.path.join(tempfile.gettempdir(), file.filename)
            file.save(temp_path)
            if temp_path.lower().endswith('.pdf'):
                images = pdf2image.convert_from_path(
                    temp_path,
                    poppler_path=r"C:\flask_project - Setup\poppler-25.07.0\Library\bin"
                )
                image = images[0]
                text = pytesseract.image_to_string(image)
            else:
                image = Image.open(temp_path)
                text = pytesseract.image_to_string(image)
            ocr_text = text

            # Use the fallback-extractor functions
            invoice_number = extract_invoice_number(text)
            amount = extract_amount(text)
            vendor = extract_vendor(text)

            # Similar fallback for dates if you want

            extracted = {
                'invoice_number': invoice_number,
                'amount': amount,
                'vendor_name': vendor,
                'invoice_date': '',  # add a fallback date extractor if you want
                'due_date': '',
                'currency': 'EUR',
                'payment_status': 'Unpaid',
                'notes': ''
            }

    return render_template('ap_ocr_form.html', extracted=extracted, ocr_text=ocr_text)


@app.route('/accounts_payable', methods=['GET', 'POST'])
def accounts_payable():
    extracted = {}
    ocr_text = ""
    return render_template('accounts_payable.html',
        invoice_number=extracted.get('invoice_number', ''),
        vendor_name=extracted.get('vendor_name', ''),
        invoice_date=extracted.get('invoice_date', ''),
        due_date=extracted.get('due_date', ''),
        amount=extracted.get('amount', ''),
        currency=extracted.get('currency', 'EUR'),
        payment_status=extracted.get('payment_status', 'Unpaid'),
        notes=extracted.get('notes', ''),
        ocr_text=ocr_text
    )

@app.route('/accounts_payable_add', methods=['POST'])
def accounts_payable_add():
    # Get posted data from the form
    invoice_number = request.form.get('invoice_number', '')
    vendor_name = request.form.get('vendor_name', '')
    invoice_date = request.form.get('invoice_date', '')
    due_date = request.form.get('due_date', '')
    amount = request.form.get('amount', '')
    currency = request.form.get('currency', '')
    payment_status = request.form.get('payment_status', '')
    notes = request.form.get('notes', '')
    # -- Add your DB save logic here --
    return f"""
    <h3>AP record received!</h3>
    <ul>
        <li>Invoice Number: {invoice_number}</li>
        <li>Vendor Name: {vendor_name}</li>
        <li>Invoice Date: {invoice_date}</li>
        <li>Due Date: {due_date}</li>
        <li>Amount: {amount}</li>
        <li>Currency: {currency}</li>
        <li>Payment Status: {payment_status}</li>
        <li>Notes: {notes}</li>
    </ul>
    <a href="/accounts_payable">Back to AP Form</a>
    """



def get_company_receipt():
    conn = sqlite3.connect('customer.db')
    c = conn.cursor()
    c.execute("""
        SELECT company_name, address, city, postal, country, phone
        FROM company_info LIMIT 1
    """)
    row = c.fetchone()
    conn.close()
    if row:
        return {
            "company_name": row[0] or "",
            "address": row[1] or "",
            "city": row[2] or "",
            "postal": row[3] or "",
            "country": row[4] or "",
            "phone": row[5] or "",
        }
    else:
        return {key: "" for key in ["company_name", "address", "city", "postal", "country", "phone"]}

@app.route("/receipt", methods=["GET", "POST"])
def receipt():
    company = get_company_receipt()
    if request.method == "POST":
        items = []
        for n, d, q, p, t in zip(
                request.form.getlist("product[]"),
                request.form.getlist("desc[]"),
                request.form.getlist("qty[]"),
                request.form.getlist("price[]"),
                request.form.getlist("total[]")
            ):
            if n.strip():
                items.append({
                    "name": n.strip(),
                    "desc": d.strip(),
                    "qty": int(q) if q else 1,
                    "price": float(p) if p else 0,
                    "total": float(t) if t else 0
                })
        subtotal = sum(item["total"] for item in items)
        tax = round(subtotal * 0.19, 2)
        total = round(subtotal + tax, 2)
        data = {
            "company": company,
            "date": request.form.get("date"),
            "time": request.form.get("time"),
            "items": items,
            "subtotal": subtotal,
            "tax": tax,
            "total": total
        }
        return render_template("cash_receipt_output.html", data=data)
    return render_template("cash_receipt_form.html", company=company)


if __name__ == "__main__":
    app.run(debug=True)
