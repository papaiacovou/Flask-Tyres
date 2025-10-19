import smtplib
from email.mime.text import MIMEText

sender_email = "your_outlook_email@outlook.com"
sender_password = "your_password"
receiver_email = "ipapaiacovou@gmail.com"

subject = "Test from Flask App (Outlook SMTP)"
body = "This is a test email sent via Outlook SMTP from Python."

msg = MIMEText(body)
msg["Subject"] = subject
msg["From"] = sender_email
msg["To"] = receiver_email

try:
    with smtplib.SMTP("smtp.office365.com", 587) as server:
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, receiver_email, msg.as_string())
    print("Email sent successfully!")
except Exception as e:
    print("Email sending failed:", e)
