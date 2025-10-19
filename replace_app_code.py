from pathlib import Path

# Full corrected Flask app code
updated_code = """
from flask import Flask, render_template, request, send_file, flash
from io import BytesIO
from datetime import datetime, timedelta
import pdfkit
import os
import sqlite3
from contextlib import closing

app = Flask(__name__)
app.secret_key = 'secret_key_for_flash_messages'

COMPANY_INFO = {
    "name": "A/PHI MOUXOURI ELASTIKA LTD",
    "address1": "41 Archepiskopou Makariou III",
    "address2": "Lakatamia, 2324",
    "phone": "P 22371931 - F 22371945",
    "email": "info@tyrebox.com.cy",
    "vat": "10024124",
    "bank": {
        "name": "Bank of Cyprus",
        "iban": "CY34002001480000000100024000",
        "swift": "BCYPCY2N"
    }
}

INVOICE_COUNTER_FILE = "invoice_number.txt"
DATABASE_FILE = "customer.db"

def init_db():
    with closing(sqlite3.connect(DATABASE_FILE)) as conn:
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

init_db()

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

@app.route('/', methods=['GET', 'POST'])
def invoice():
    if request.method == 'POST':
        data = request.form.to_dict(flat=False)
        tyres = data.get('tyres', [])
        descriptions = data.get('description', [])
        quantities = data.get('quantity', [])
        prices = data.get('price', [])
        discounts = data.get('discount', [])

        company_name = request.form.get('bill_to_line1', '').strip().upper()
        address = request.form.get('bill_to_line2', '').strip().upper()
        city_postal = request.form.get('bill_to_line3', '').strip().upper()
        phone = request.form.get('bill_to_line4', '').strip().upper()
        email = request.form.get('bill_to_line5', '').strip()

        bill_to = "\\n".join([company_name, address, city_postal, phone, email])

        today_dt = datetime.today()
        today = today_dt.strftime('%d-%m-%Y')
        due_date = (today_dt + timedelta(days=30)).strftime('%d-%m-%Y')

        invoice_number = get_next_invoice_number()

        with closing(sqlite3.connect(DATABASE_FILE)) as conn:
            with conn:
                conn.execute('''
                    INSERT INTO bill_to_info (company_name, address, city_postal, phone, email)
                    VALUES (?, ?, ?, ?, ?)
                ''', (company_name, address, city_postal, phone, email))

        items = []
        subtotal = 0.0
        vat_rate = 0.19

        for tyre, desc, qty, price, discount in zip(tyres, descriptions, quantities, prices, discounts):
            try:
                qty_val = float(qty)
                price_val = float(price)
                discount_val = float(discount)
                line_subtotal = max((price_val - discount_val) * qty_val, 0)
                subtotal += line_subtotal
                items.append({
                    'tyres': tyre,
                    'description': desc,
                    'quantity': qty_val,
                    'price': round(price_val, 2),
                    'discount': round(discount_val, 2),
                    'subtotal': round(line_subtotal, 2)
                })
            except ValueError:
                flash("Invalid number, price or discount value.")
                return render_template('invoice_form.html')

        vat_amount = round(subtotal * vat_rate, 2)
        total = round(subtotal + vat_amount, 2)

        rendered = render_template('invoice_template.html',
                                   items=items,
                                   subtotal=round(subtotal, 2),
                                   vat=vat_amount,
                                   total=total,
                                   today=today,
                                   due_date=due_date,
                                   bill_to=bill_to,
                                   invoice_number=invoice_number,
                                   company=COMPANY_INFO)

        config = None
        if os.name == 'nt':
            wkhtmltopdf_path = r'C:\\Program Files\\wkhtmltopdf\\bin\\wkhtmltopdf.exe'
            if os.path.exists(wkhtmltopdf_path):
                config = pdfkit.configuration(wkhtmltopdf=wkhtmltopdf_path)
            else:
                flash("wkhtmltopdf not found. Check your path.")
                return render_template('invoice_form.html')

        options = {
            'enable-local-file-access': '',
            'page-size': 'A4',
            'encoding': 'UTF-8'
        }

        pdf_output = pdfkit.from_string(rendered, False, configuration=config, options=options)

        output_dir = os.path.join(os.getcwd(), 'invoices')
        os.makedirs(output_dir, exist_ok=True)
        safe_bill_to = company_name.strip().replace(" ", "_").replace("\\n", "_")[:30] or "unknown"
        output_filename = f"invoice_{invoice_number}_{safe_bill_to}.pdf"
        output_path = os.path.join(output_dir, output_filename)

        with open(output_path, 'wb') as f:
            f.write(pdf_output)

        return send_file(BytesIO(pdf_output), as_attachment=True, download_name=output_filename)

    return render_template('invoice_form.html')

if __name__ == '__main__':
    app.run(debug=True)
"""

# ⚠️ Write it directly to your actual Windows path
Path("C:/flask_project/app.py").write_text(updated_code.strip(), encoding="utf-8")
