#!/usr/bin/env python3

import smtplib
import ssl
import random
import sqlite3
from email.message import EmailMessage
from datetime import datetime

# ==============================
# EMAIL CONFIGURATION
# ==============================
SMTP_SERVER = "smtp.mail.yahoo.com"
SMTP_PORT = 465

EMAIL_SENDER = "anwarbm1989@yahoo.com"
EMAIL_PASSWORD = "ytwcuremorltdkrm"  # App password Yahoo
EMAIL_RECEIVER = "oit.comitesfax@gmail.com"
SENDER_NAME = "Robot d’accès d’entrée"
REPLY_TO = EMAIL_SENDER

# ==============================
# DATABASE CONFIGURATION
# ==============================
DB_FILE = "membres.db"

# ==============================
# CONNECT TO DATABASE
# ==============================
conn = sqlite3.connect(DB_FILE)
cursor = conn.cursor()

# CREATE TABLES IF NOT EXISTS
cursor.execute("""
CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    code_membre TEXT NOT NULL,
    created_at DATETIME NOT NULL
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS code_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code_membre TEXT NOT NULL,
    created_at DATETIME NOT NULL
)
""")
conn.commit()

# ==============================
# GET LAST CODE
# ==============================
cursor.execute("SELECT code_membre FROM code_history ORDER BY created_at DESC LIMIT 1")
last_code_row = cursor.fetchone()
last_code = last_code_row[0] if last_code_row else None

# ==============================
# GENERATE NEW 4-DIGIT CODE (NOT EQUAL TO LAST)
# ==============================
while True:
    visitor_code = f"{random.randint(0, 9999):04}"  # Always 4-digit
    if visitor_code != last_code:
        break

created_at = datetime.now()

# ==============================
# SAVE NEW CODE IN MEMBERS (REMOVE OLD)
# ==============================
cursor.execute("DELETE FROM members")  # Remove old active code
cursor.execute("""
INSERT INTO members (first_name, last_name, code_membre, created_at)
VALUES (?, ?, ?, ?)
""", ("Visiteur", "TEO Sfax", visitor_code, created_at))

# ==============================
# SAVE IN HISTORY
# ==============================
cursor.execute("""
INSERT INTO code_history (code_membre, created_at)
VALUES (?, ?)
""", (visitor_code, created_at))

conn.commit()
conn.close()

# ==============================
# EMAIL CONTENT
# ==============================
today = created_at.strftime("%d/%m/%Y")
time_now = created_at.strftime("%H:%M")

msg = EmailMessage()
msg["From"] = f"{SENDER_NAME} <{EMAIL_SENDER}>"
msg["To"] = EMAIL_RECEIVER
msg["Reply-To"] = REPLY_TO
msg["Subject"] = "🔐 Code d’accès visiteur – Hebdomadaire"

# Plain text
msg.set_content(f"""
Bonjour,

Ceci est votre code d'accès hebdomadaire généré automatiquement par le système de contrôle d'accès.

🔐 Code visiteur : {visitor_code}
📅 Date de génération : {today}
🕗 Heure : {time_now}

Ce code est strictement personnel et valable pour une semaine. 
Il sera remplacé automatiquement par un nouveau code la semaine prochaine.

Cordialement,
Robot d’accès d’entrée
""")

# HTML version
msg.add_alternative(f"""
<html>
  <body style="font-family: Arial, sans-serif; line-height:1.5;">
    <h2 style="color:#2E86C1;">🔐 Code d’accès visiteur – Hebdomadaire</h2>
    <p>Bonjour,</p>
    <p>Ceci est votre code d'accès hebdomadaire généré automatiquement par le système de contrôle d'accès.</p>
    <p style="font-size:18px;">
      <strong>Code visiteur :</strong>
      <span style="color:#D35400;">{visitor_code}</span>
    </p>
    <p>
      <strong>Date de génération :</strong> {today}<br>
      <strong>Heure :</strong> {time_now}
    </p>
    <p style="color:gray;">
      Ce code est strictement personnel et valable pour une semaine. 
      Il sera remplacé automatiquement par un nouveau code la semaine prochaine.
    </p>
    <p>Cordialement,<br><strong>Robot d’accès d’entrée</strong></p>
  </body>
</html>
""", subtype="html")

# ==============================
# SEND EMAIL
# ==============================
context = ssl.create_default_context()

try:
    with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=context) as server:
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.send_message(msg)
        print(f"✅ Email hebdomadaire envoyé avec succès : {visitor_code}")
except Exception as e:
    print("❌ Erreur lors de l’envoi :", e)
