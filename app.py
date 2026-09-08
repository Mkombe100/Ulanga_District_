import os
import re
import time
import random
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone

from flask import Flask, request, jsonify, render_template, session, redirect, url_for
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-to-a-random-secret")

# ---------- CONFIG ----------
DATABASE_URL = os.getenv("DATABASE_URL")
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_FROM_NAME = os.getenv("EMAIL_FROM_NAME", "Ulanaga District System")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL must be set in .env")
if not EMAIL_USER or not EMAIL_PASS:
    raise RuntimeError("EMAIL_USER and EMAIL_PASS must be set in .env")

# ---------- DATABASE ----------
engine = create_engine(DATABASE_URL, pool_pre_ping=True)

def test_db():
    with engine.connect() as conn:
        result = conn.execute(text("SELECT NOW()"))
        now = result.scalar()
        print("✅ Database connected:", now)

test_db()

# ---------- EMAIL ----------
def send_verification_email(to_email: str, first_name: str, code: str):
    subject = "🔐 Your Email Verification Code"
    html_content = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background-color: #f9f9f9; border-radius: 8px;">
      <div style="background-color: #fff; padding: 30px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
        <h2 style="color: #333; margin-bottom: 10px;">📧 Email Verification</h2>
        <p style="color: #666; margin-bottom: 20px;">Hello {first_name},</p>
        
        <p style="color: #666; margin-bottom: 20px;">Welcome to the <strong>Ulanaga District System</strong>! Your email verification code is:</p>
        
        <div style="background-color: #007bff; padding: 25px; text-align: center; margin: 25px 0; border-radius: 5px;">
          <h1 style="color: #fff; margin: 0; letter-spacing: 8px; font-family: monospace; font-size: 32px;">{code}</h1>
        </div>
        
        <p style="color: #666; margin: 20px 0; font-size: 14px;">
          ⏱️ <strong>This code expires in 10 minutes</strong>
        </p>
        
        <div style="background-color: #fff3cd; padding: 15px; border-left: 4px solid #ffc107; margin: 20px 0; border-radius: 4px;">
          <p style="color: #856404; margin: 0; font-size: 13px;">
            <strong>💡 Tip:</strong> Never share this code with anyone. The Ulanaga Team will never ask for it.
          </p>
        </div>
        
        <p style="color: #999; font-size: 12px; margin-top: 30px; padding-top: 20px; border-top: 1px solid #eee;">
          If you didn't request this verification code, please ignore this email or contact support.
        </p>
      </div>
    </div>
    """

    msg = MIMEMultipart("alternative")
    msg["From"] = f'"{EMAIL_FROM_NAME}" <{EMAIL_USER}>'
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(html_content, "html"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASS)
        server.send_message(msg)

# ---------- OTP STORE ----------
# Structure: { email_norm: { "type": "signup"|"login", "code": "...", "expires_at": ts, ... } }
otp_store = {}

def generate_code() -> str:
    return str(random.randint(100000, 999999))

def is_valid_email(email: str) -> bool:
    return re.match(r"^\S+@\S+\.\S+$", email) is not None

# ---------- ROUTES: PAGES ----------
@app.route("/")
def index_page():
    return render_template("index.html")

@app.route("/dashboard")
def dashboard_page():
    if "user_id" not in session:
        return redirect(url_for("index_page"))
    user = {
        "id": session.get("user_id"),
        "email": session.get("email"),
        "first_name": session.get("first_name", ""),
        "last_name": session.get("last_name", ""),
    }
    return render_template("dashboard.html", user=user)

# ---------- API: SIGNUP ----------
@app.route("/api/signup/request-code", methods=["POST"])
def signup_request_code():
    data = request.get_json(silent=True) or {}
    first_name = (data.get("firstName") or "").strip()
    last_name = (data.get("lastName") or "").strip()
    email = (data.get("email") or "").strip()
    phone = (data.get("phone") or "").strip()

    if not first_name or not last_name or not email or not phone:
        return jsonify({"error": "All fields are required"}), 400

    email_norm = email.lower()
    phone_norm = phone

    if not is_valid_email(email_norm):
        return jsonify({"error": "Invalid email format"}), 400

    with engine.connect() as conn:
        existing_email = conn.execute(
            text("SELECT id FROM users WHERE email = :email LIMIT 1"),
            {"email": email_norm},
        ).fetchone()

        if existing_email:
            return jsonify({"error": "This email is already registered. Please log in instead."}), 400

        existing_phone = conn.execute(
            text("SELECT id FROM users WHERE phone_number = :phone LIMIT 1"),
            {"phone": phone_norm},
        ).fetchone()

        if existing_phone:
            return jsonify({"error": "This phone number is already registered."}), 400

    code = generate_code()
    expires_at = time.time() + 10 * 60  # 10 minutes

    otp_store[email_norm] = {
        "type": "signup",
        "code": code,
        "expires_at": expires_at,
        "first_name": first_name,
        "last_name": last_name,
        "phone": phone_norm,
    }

    try:
        send_verification_email(email_norm, first_name, code)
        print(f"✅ Signup verification code sent to {email_norm}")
    except Exception as e:
        print("❌ Failed to send verification email:", e)
        otp_store.pop(email_norm, None)
        return jsonify({"error": "Failed to send verification email. Check server logs."}), 500

    return jsonify({"ok": True, "message": "Verification code sent to your email", "email": email_norm})

@app.route("/api/signup/verify-and-create", methods=["POST"])
def signup_verify_and_create():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    code = (data.get("code") or "").strip()

    if not email or not code:
        return jsonify({"error": "Email and code are required"}), 400

    email_norm = email.lower()
    record = otp_store.get(email_norm)

    if not record or record["type"] != "signup":
        return jsonify({"error": "No signup code requested for this email"}), 400

    if time.time() > record["expires_at"]:
        otp_store.pop(email_norm, None)
        return jsonify({"error": "Code expired. Please request a new one."}), 400

    if record["code"] != str(code):
        return jsonify({"error": "Invalid code"}), 400

    with engine.connect() as conn:
        existing_email = conn.execute(
            text("SELECT id FROM users WHERE email = :email LIMIT 1"),
            {"email": email_norm},
        ).fetchone()
        if existing_email:
            otp_store.pop(email_norm, None)
            return jsonify({"error": "This email is already registered. Please log in instead."}), 400

        existing_phone = conn.execute(
            text("SELECT id FROM users WHERE phone_number = :phone LIMIT 1"),
            {"phone": record["phone"]},
        ).fetchone()
        if existing_phone:
            otp_store.pop(email_norm, None)
            return jsonify({"error": "This phone number is already registered."}), 400

        result = conn.execute(
            text("""
                INSERT INTO users (first_name, last_name, email, phone_number, last_login)
                VALUES (:first_name, :last_name, :email, :phone, :now)
                RETURNING id
            """),
            {
                "first_name": record["first_name"],
                "last_name": record["last_name"],
                "email": email_norm,
                "phone": record["phone"],
                "now": datetime.now(timezone.utc),
            },
        )
        user_id = result.scalar()
        conn.commit()

    otp_store.pop(email_norm, None)

    # Set session for logged-in user
    session["user_id"] = user_id
    session["email"] = email_norm
    session["first_name"] = record["first_name"]
    session["last_name"] = record["last_name"]

    return jsonify({
        "ok": True,
        "message": "Account created successfully",
        "userId": user_id,
        "redirect": "/dashboard",
        "user": {
            "id": user_id,
            "email": email_norm,
            "first_name": record["first_name"],
            "last_name": record["last_name"],
        },
    })

# ---------- API: LOGIN ----------
@app.route("/api/login/request-code", methods=["POST"])
def login_request_code():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()

    if not email or not is_valid_email(email):
        return jsonify({"error": "Please enter a valid email address"}), 400

    email_norm = email.lower()

    with engine.connect() as conn:
        user = conn.execute(
            text("SELECT id, first_name, last_name FROM users WHERE email = :email LIMIT 1"),
            {"email": email_norm},
        ).fetchone()

        if not user:
            return jsonify({"error": "No account found with this email. Please sign up."}), 404

    code = generate_code()
    expires_at = time.time() + 10 * 60

    otp_store[email_norm] = {
        "type": "login",
        "code": code,
        "expires_at": expires_at,
        "first_name": user.first_name,
        "last_name": user.last_name,
    }

    try:
        send_verification_email(email_norm, user.first_name or "User", code)
        print(f"✅ Login verification code sent to {email_norm}")
    except Exception as e:
        print("❌ Failed to send verification email:", e)
        otp_store.pop(email_norm, None)
        return jsonify({"error": "Failed to send verification email. Check server logs."}), 500

    return jsonify({"ok": True, "message": "Verification code sent to your email", "email": email_norm})

@app.route("/api/login/verify", methods=["POST"])
def login_verify():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    code = (data.get("code") or "").strip()

    if not email or not code:
        return jsonify({"error": "Email and code are required"}), 400

    email_norm = email.lower()
    record = otp_store.get(email_norm)

    if not record or record["type"] != "login":
        return jsonify({"error": "No login code requested for this email"}), 400

    if time.time() > record["expires_at"]:
        otp_store.pop(email_norm, None)
        return jsonify({"error": "Code expired. Please request a new one."}), 400

    if record["code"] != str(code):
        return jsonify({"error": "Invalid code"}), 400

    with engine.connect() as conn:
        user = conn.execute(
            text("SELECT id, first_name, last_name FROM users WHERE email = :email LIMIT 1"),
            {"email": email_norm},
        ).fetchone()

        if not user:
            otp_store.pop(email_norm, None)
            return jsonify({"error": "No account found with this email."}), 404

        # Update last_login
        conn.execute(
            text("UPDATE users SET last_login = :now WHERE id = :id"),
            {"now": datetime.now(timezone.utc), "id": user.id},
        )
        conn.commit()

    otp_store.pop(email_norm, None)

    session["user_id"] = user.id
    session["email"] = email_norm
    session["first_name"] = user.first_name or ""
    session["last_name"] = user.last_name or ""

    return jsonify({
        "ok": True,
        "message": "Login successful",
        "redirect": "/dashboard",
        "user": {
            "id": user.id,
            "email": email_norm,
            "first_name": user.first_name or "",
            "last_name": user.last_name or "",
        },
    })

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)







