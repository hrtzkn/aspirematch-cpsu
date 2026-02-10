from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify, send_file, current_app
from ..db import get_db_connection
import os
import pandas as pd
import psycopg2
import base64
import json
import re
from werkzeug.utils import secure_filename
from PIL import Image
from weasyprint import HTML
from io import BytesIO
from psycopg2.extras import RealDictCursor
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta, timezone
from flask import request
from collections import Counter
from ..description import letter_descriptions, preferred_program_map, short_letter_descriptions
from math import ceil
from groq import Groq
import smtplib
from email.message import EmailMessage
import random
import time

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

admin_bp = Blueprint('admin', __name__, template_folder='../../frontend/templates/admin')

DEFAULT_ADMIN = {
    "id": "1000",
    "fullname": "hertzkin",
    "username": "hk",
    "password": "hk",
    "campus": "Kabankalan Campus"

}

ALLOWED_EXTENSIONS = {"xlsx", "xls"}

UPLOAD_FOLDER = os.path.join(
    os.path.dirname(__file__),
    "..", "..", "uploads"
)

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def is_password_strong(pw):
    return (
        len(pw) >= 8 and
        re.search(r"[A-Z]", pw) and
        re.search(r"[a-z]", pw) and
        re.search(r"[0-9]", pw) and
        re.search(r"[^A-Za-z0-9]", pw)
    )

def image_to_base64(filename):
    path = os.path.join(
        current_app.static_folder,
        "images",
        filename
    )
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()
    
def student_photo_to_base64(filename):
    if not filename:
        return None

    path = os.path.join(
        current_app.static_folder,
        "uploads",
        "students",
        filename
    )

    if not os.path.exists(path):
        return None

    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()
    
def format_ai_explanation_for_pdf(text):
    if not text:
        return ""

    sections = [
        "Career Letter Explanation",
        "Strengths",
        "Weaknesses",
        "Personalized Career Advice"
    ]

    formatted = text.strip()

    for title in sections:
        formatted = formatted.replace(
            title,
            f'<div class="ai-subtitle">{title}</div>'
        )

    lines = formatted.split("\n")
    html_lines = []
    in_list = False

    for line in lines:
        if line.strip().startswith("•"):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{line.replace('•', '').strip()}</li>")
        else:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<p>{line}</p>")

    if in_list:
        html_lines.append("</ul>")

    return f'<div class="ai-content">{"".join(html_lines)}</div>'

def get_client_ip():
    return request.headers.get("X-Forwarded-For", request.remote_addr)

def send_security_alert(ip, username):
    msg = EmailMessage()
    msg["Subject"] = "⚠️ Admin Login Alert"
    msg["From"] = "aspirematch2@gmail.com"
    msg["To"] = "hertzkin@gmail.com"

    msg.set_content(f"""
    Suspicious admin login detected.

    Username: {username}
    IP Address: {ip}
    Time: {datetime.now(timezone.utc)}
    """)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login("aspirematch2@gmail.com", "bvti ptud ebch pmee")
        smtp.send_message(msg)

def generate_otp():
    """Generate a 6-digit OTP as a string."""
    return str(random.randint(100000, 999999))


def send_otp_email(email, otp):
    """Send OTP email to admin."""
    msg = EmailMessage()
    msg["Subject"] = "AspireMatch Admin OTP"
    msg["From"] = "aspirematch2@gmail.com"
    msg["To"] = email
    msg.set_content(f"""
Your One-Time Password (OTP) for AspireMatch Admin login is:

{otp}

This OTP will expire in 5 minutes.
""")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login("aspirematch2@gmail.com", "bvti ptud ebch pmee")
        server.send_message(msg)

@admin_bp.route("/test-db")
def test_db():
    conn = get_db_connection()
    return "DB CONNECTED"

@admin_bp.route("/")
def home():
    return redirect(url_for("admin.login"))

MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_MINUTES = 5

@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    username = ""
    password = ""

    session.setdefault("admin_login_attempts", 0)
    session.setdefault("admin_lock_until", None)

    if session["admin_lock_until"]:
        if datetime.now(timezone.utc) < session["admin_lock_until"]:
            remaining = int(
                (session["admin_lock_until"] - datetime.now(timezone.utc)).total_seconds() / 60
            )
            error = f"Account locked. Try again in {remaining} minutes."
            return render_template("admin/adminLogin.html", error=error)
        else:
            session["admin_login_attempts"] = 0
            session["admin_lock_until"] = None

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        user = None
        user_type = None
        campus = None

        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("SELECT * FROM admin WHERE username = %s", (username,))
        user = cur.fetchone()
        if user:
            user_type = "admin"
            campus = user["campus"]
        else:
            cur.execute("SELECT * FROM super_admin WHERE username = %s", (username,))
            user = cur.fetchone()
            if user:
                user_type = "super_admin"
                campus = user.get("campus", "ALL")

        cur.close()
        conn.close()

        if user and check_password_hash(user["password"], password):
            session.clear()
            session["admin_username"] = username
            session["last_activity"] = datetime.now(timezone.utc)
            session.permanent = True
            session["admin_login_attempts"] = 0
            session["admin_lock_until"] = None

            return redirect(url_for("admin.dashboard"))

        ip = request.headers.get("X-Forwarded-For", request.remote_addr)

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO admin_login_attempts (ip_address, username, attempts)
            VALUES (%s, %s, 1)
            ON CONFLICT (ip_address)
            DO UPDATE SET
                attempts = admin_login_attempts.attempts + 1,
                last_attempt = CURRENT_TIMESTAMP
        """, (ip, username))
        conn.commit()
        cur.close()
        conn.close()

        session["admin_login_attempts"] += 1

        if session["admin_login_attempts"] == MAX_LOGIN_ATTEMPTS:
            send_security_alert(ip, username)

        if session["admin_login_attempts"] >= MAX_LOGIN_ATTEMPTS:
            session["admin_lock_until"] = datetime.now(timezone.utc) + timedelta(minutes=LOCKOUT_MINUTES)
            error = "Too many failed attempts. Account locked for 5 minutes."
        else:
            remaining = MAX_LOGIN_ATTEMPTS - session["admin_login_attempts"]
            error = f"Invalid credentials. {remaining} attempts remaining."

    locked = session.get("admin_login_attempts", 0) >= MAX_LOGIN_ATTEMPTS

    return render_template("admin/adminLogin.html", error=error, locked=locked, username=username)

@admin_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    error = success = None

    if request.method == "POST":
        email = request.form["email"]

        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute(
            "SELECT id FROM super_admin WHERE email = %s",
            (email,)
        )
        admin = cur.fetchone()

        
        is_super_admin = False

        if admin:
            is_super_admin = True
        else:
            cur.execute(
                "SELECT id FROM admin WHERE email = %s",
                (email,)
            )
            admin = cur.fetchone()

        cur.close()
        conn.close()

        if not admin:
            error = "No admin account found with this email."
        else:
            otp = generate_otp()

            session["admin_otp"] = otp
            session["admin_otp_email"] = email
            session["admin_otp_time"] = time.time()
            session["is_super_admin"] = is_super_admin

            send_otp_email(email, otp)
            success = "OTP has been sent to your email."

            return redirect(url_for("admin.verify_reset_otp"))

    return render_template(
        "admin/adminForgotPassword.html",
        error=error,
        success=success
    )

@admin_bp.route("/verify-reset-otp", methods=["GET", "POST"])
def verify_reset_otp():
    error = None
    success = None
    remaining = None

    is_super_admin = session.get("is_super_admin", False)
    
    conn = get_db_connection()
    cur = conn.cursor()

    if is_super_admin:
        cur.execute("""
            SELECT fullname, campus
            FROM super_admin
            WHERE email = %s
        """, (session.get("admin_otp_email"),))
    else:
        cur.execute("""
            SELECT fullname, campus
            FROM admin
            WHERE email = %s
        """, (session.get("admin_otp_email"),))

    admin_row = cur.fetchone()

    cur.close()
    conn.close()

    if not admin_row:
        return redirect(url_for("admin.login"))

    fullname, admin_campus = admin_row

    if request.method == "POST":

        action = request.form.get("action")

        if action == "resend":
            if "admin_otp_email" not in session:
                error = "Session expired. Please restart password reset."
            else:
                last_sent = session.get("admin_otp_time", 0)
                elapsed = int(time.time() - last_sent)

                if elapsed < 60:
                    remaining = 60 - elapsed
                    error = "Please wait before resending OTP."
                else:
                    otp = generate_otp()
                    session["admin_otp"] = otp
                    session["admin_otp_time"] = time.time()

                    send_otp_email(session["admin_otp_email"], otp)
                    success = "A new OTP has been sent to your email."

            return render_template(
                "admin/adminVerifyOtp.html",
                error=error,
                success=success,
                remaining=remaining
            )

        if action == "verify":
            user_otp = request.form.get("otp", "").strip()

            if not user_otp:
                error = "Please enter the OTP."

            elif time.time() - session.get("admin_otp_time", 0) > 300:
                error = "OTP expired. Please request a new one."

            elif user_otp != session.get("admin_otp"):
                error = "Invalid OTP."

            else:
                session["admin_reset_email"] = session["admin_otp_email"]
                session.pop("admin_otp", None)
                session.pop("admin_otp_email", None)
                session.pop("admin_otp_time", None)
                return redirect(url_for("admin.reset_password"))

    return render_template(
        "admin/adminVerifyOtp.html",
        error=error,
        success=success,
        remaining=remaining,
        fullname=fullname,
        admin_campus=admin_campus
    )

@admin_bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    error = None

    if "admin_reset_email" not in session:
        return redirect(url_for("admin.login"))
    
    is_super_admin = session.get("is_super_admin", False)

    if request.method == "POST":
        password = request.form["password"]
        confirm = request.form["confirm"]

        if password != confirm:
            error = "Passwords do not match."
        else:
            hashed = generate_password_hash(password)

            conn = get_db_connection()
            cur = conn.cursor()

            if is_super_admin:
                cur.execute("""
                    UPDATE super_admin
                    SET password = %s
                    WHERE email = %s
                """, (hashed, session["admin_reset_email"]))
            else:
                cur.execute("""
                    UPDATE admin
                    SET password = %s
                    WHERE email = %s
                """, (hashed, session["admin_reset_email"]))
                
            conn.commit()
            cur.close()
            conn.close()

            session.pop("admin_reset_email", None)

            return redirect(url_for("admin.login"))

    return render_template("admin/adminResetPassword.html", error=error)

@admin_bp.route("/dashboard", methods=["GET"])
def dashboard():
    if "admin_username" not in session:
        return redirect(url_for("admin.login"))
    
    error = request.args.get("error")
    success = request.args.get("success")
    message = request.args.get("message")

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute(
        "SELECT fullname, campus FROM admin WHERE username = %s;",
        (session["admin_username"],)
    )
    admin_row = cur.fetchone()
    
    if admin_row:
        fullname, admin_campus = admin_row
    else:
        cur.execute(
            "SELECT fullname, campus FROM super_admin WHERE username = %s;",
            (session["admin_username"],)
        )
        admin_row = cur.fetchone()
        if admin_row:
            fullname, admin_campus = admin_row
        else:
            cur.close()
            conn.close()
            return redirect(url_for("admin.login"))

    cur.execute(
        "SELECT 1 FROM super_admin WHERE username = %s;",
        (session["admin_username"],)
    )
    is_super_admin = bool(cur.fetchone())

    selected_year = request.args.get("year", type=int) or datetime.now().year
    search_query = request.args.get("q", "").strip()

    if is_super_admin:
        selected_campus = request.args.get("campus")
    else:
        selected_campus = admin_campus

    cur.execute("""
        SELECT DISTINCT EXTRACT(YEAR FROM created_at)::int
        FROM student
        ORDER BY 1 DESC;
    """)
    available_years = [row[0] for row in cur.fetchall()]

    student_query = """
        SELECT id, exam_id, fullname, gender, email, campus
        FROM student
        WHERE EXTRACT(YEAR FROM created_at) = %s
    """
    params = [selected_year]

    if selected_campus:
        student_query += " AND campus = %s"
        params.append(selected_campus)

    if search_query:
        student_query += " AND (LOWER(fullname) LIKE LOWER(%s) OR exam_id ILIKE %s)"
        params.extend([f"%{search_query}%", f"%{search_query}%"])

    student_query += " ORDER BY fullname ASC;"
    cur.execute(student_query, tuple(params))
    searched_students = cur.fetchall()

    total_query = """
        SELECT COUNT(*)
        FROM student
        WHERE EXTRACT(YEAR FROM created_at) = %s
    """
    params = [selected_year]

    if selected_campus:
        total_query += " AND campus = %s"
        params.append(selected_campus)

    cur.execute(total_query, tuple(params))
    total_students = cur.fetchone()[0]

    pending_query = """
        SELECT COUNT(*)
        FROM student s
        LEFT JOIN student_survey_answer a
            ON a.student_id = s.id OR a.exam_id = s.exam_id
        WHERE EXTRACT(YEAR FROM s.created_at) = %s
        AND (a.preferred_program IS NULL OR a.preferred_program = '')
    """
    params = [selected_year]

    if selected_campus:
        pending_query += " AND s.campus = %s"
        params.append(selected_campus)

    cur.execute(pending_query, tuple(params))
    pending_students = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(DISTINCT admin_username)
        FROM admin_logs
        WHERE created_at >= NOW() - INTERVAL '1 month';
    """)
    active_admins = cur.fetchone()[0]

    admin_query = """
        SELECT a.fullname,
            CASE 
                WHEN l.last_login >= NOW() - INTERVAL '1 month' THEN 'Active'
                ELSE 'Inactive'
            END AS status
        FROM admin a
        LEFT JOIN (
            SELECT admin_username, MAX(created_at) AS last_login
            FROM admin_logs
            GROUP BY admin_username
        ) l ON a.username = l.admin_username
    """
    params = []

    if selected_campus:
        admin_query += " WHERE a.campus = %s"
        params.append(selected_campus)

    admin_query += " ORDER BY a.fullname ASC;"

    cur.execute(admin_query, tuple(params))
    admins = cur.fetchall()

    cur.close()
    conn.close()

    return render_template(
        "admin/dashboard.html",
        admin_username=session["admin_username"],
        fullname=fullname,
        admin_campus=admin_campus,
        is_super_admin=is_super_admin,
        selected_campus=selected_campus,
        total_students=total_students,
        pending_students=pending_students,
        active_admins=active_admins,
        year=selected_year,
        available_years=available_years,
        searched_students=searched_students,
        search_query=search_query,
        admins=admins,
        error=error,
        success=success,
        message=message
    )

@admin_bp.route("/edit-student", methods=["POST"])
def edit_student():
    if "admin_username" not in session:
        return redirect(url_for("admin.login"))

    admin_username = session["admin_username"]

    student_id = request.form["student_id"]
    new_fullname = request.form["fullname"]
    new_gender = request.form["gender"]
    new_email = request.form["email"]

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute(
        "SELECT campus FROM admin WHERE username = %s;",
        (admin_username,)
    )
    admin_campus = cur.fetchone()[0]

    cur.execute("""
        SELECT fullname, gender, email
        FROM student
        WHERE id = %s;
    """, (student_id,))
    old_fullname, old_gender, old_email = cur.fetchone()

    cur.execute("""
        UPDATE student
        SET fullname = %s,
            gender = %s,
            email = %s
        WHERE id = %s;
    """, (new_fullname, new_gender, new_email, student_id))

    if old_fullname != new_fullname:
        cur.execute("""
            INSERT INTO admin_logs (admin_username, campus, action)
            VALUES (%s, %s, %s);
        """, (
            admin_username,
            admin_campus,
            f"Edited student: {old_fullname} into {new_fullname}"
        ))

    if old_gender != new_gender:
        cur.execute("""
            INSERT INTO admin_logs (admin_username, campus, action)
            VALUES (%s, %s, %s);
        """, (
            admin_username,
            admin_campus,
            f"Edited student: {old_gender} into {new_gender}"
        ))

    if old_email != new_email:
        cur.execute("""
            INSERT INTO admin_logs (admin_username, campus, action)
            VALUES (%s, %s, %s);
        """, (
            admin_username,
            admin_campus,
            f"Edited student: {old_email} into {new_email}"
        ))

    conn.commit()
    cur.close()
    conn.close()

    return redirect(url_for("admin.dashboard"))

@admin_bp.route("/delete-student", methods=["POST"])
def delete_student():
    if "admin_username" not in session:
        return redirect(url_for("admin.login"))

    admin_username = session["admin_username"]
    student_id = request.form["student_id"]

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute(
        "SELECT campus FROM admin WHERE username = %s;",
        (admin_username,)
    )
    admin_campus = cur.fetchone()[0]

    cur.execute(
        "SELECT fullname FROM student WHERE id = %s;",
        (student_id,)
    )
    student_row = cur.fetchone()
    if not student_row:
        cur.close()
        conn.close()
        return redirect(url_for("admin.dashboard"))

    student_fullname = student_row[0]

    cur.execute(
        "DELETE FROM student WHERE id = %s;",
        (student_id,)
    )

    cur.execute("""
        INSERT INTO admin_logs (admin_username, campus, action)
        VALUES (%s, %s, %s);
    """, (
        admin_username,
        admin_campus,
        f"Deleted student: {student_fullname}"
    ))

    conn.commit()
    cur.close()
    conn.close()

    return redirect(url_for("admin.dashboard"))

@admin_bp.route("/addAdmin", methods=["GET", "POST"])
def addAdmin():
    if "admin_username" not in session:
        return redirect(url_for("admin.login"))

    message = None
    category = None
    admin_username = session["admin_username"]

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("SELECT campus FROM admin WHERE username = %s", (admin_username,))
    admin_row = cur.fetchone()
    if admin_row:
        admin_campus = admin_row["campus"]
        is_super_admin = False
    else:
        cur.execute("SELECT fullname, campus FROM super_admin WHERE username = %s", (admin_username,))
        admin_row = cur.fetchone()
        if admin_row:
            admin_campus = admin_row.get("campus") or "ALL"
            is_super_admin = True
        else:
            cur.close()
            conn.close()
            return redirect(url_for("admin.login"))

    cur.close()
    conn.close()

    if request.method == "POST":
        fullname = request.form["fullname"]
        username = request.form["user_name"]
        email = request.form["email"]
        campus = request.form["campus"]
        password = request.form["password"]

        if not is_password_strong(password):
            return render_template(
                "admin/addAdmin.html",
                admin_username=session["admin_username"],
                message="Password is too weak! Must include: uppercase, lowercase, number, symbol, and min 8 chars.",
                category="danger",
                admins=[]
            )

        hashed_pw = generate_password_hash(password)

        session["new_admin_data"] = {
            "fullname": fullname,
            "username": username,
            "email": email,
            "campus": campus,
            "password": hashed_pw
        }

        otp = generate_otp()
        session["new_admin_otp"] = otp
        session["new_admin_otp_time"] = time.time()
        session["new_admin_email"] = email

        send_otp_email(email, otp)

        return redirect(url_for("admin.verify_new_admin"))

    admins = []
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    if is_super_admin:
        cur.execute("""
            SELECT id, fullname, username, email, campus
            FROM admin
            ORDER BY campus ASC, fullname ASC
        """)
    else:
        cur.execute("""
            SELECT id, fullname, username, email, campus
            FROM admin
            WHERE campus = %s
            ORDER BY fullname ASC
        """, (admin_campus,))
    admins = cur.fetchall()
    cur.close()
    conn.close()

    deleted_admins = []
    if is_super_admin:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT id, fullname, username, email, campus, deleted_by, deleted_at
            FROM deleted_admin
            ORDER BY deleted_at DESC
        """)
        deleted_admins = cur.fetchall()
        cur.close()
        conn.close()

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT program_name, created_at, is_active
        FROM program
        WHERE campus = %s
        ORDER BY created_at DESC
    """, (admin_campus,))
    programs = cur.fetchall()
    cur.close()
    conn.close()

    return render_template(
        "admin/addAdmin.html",
        admin_username=session["admin_username"],
        is_super_admin=is_super_admin,
        message=message,
        category=category,
        admins=admins,
        programs=programs,
        admin_campus=admin_campus,
        deleted_admins=deleted_admins
    )

@admin_bp.route("/delete-admin", methods=["POST"])
def delete_admin():
    if "admin_username" not in session or session["admin_username"] != "hkml":
        return redirect(url_for("admin.login"))

    deleted_admin_id = request.form["admin_id"]
    new_admin_id = request.form["reassign_admin_id"]
    deleter = session["admin_username"]

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, fullname, username, email, campus
        FROM admin
        WHERE id = %s
    """, (deleted_admin_id,))
    admin_row = cur.fetchone()

    if not admin_row:
        cur.close()
        conn.close()
        return redirect(url_for("admin.addAdmin"))

    admin_id, fullname, username, email, campus = admin_row

    if username == deleter:
        cur.close()
        conn.close()
        return redirect(url_for("admin.addAdmin"))

    cur.execute("""
        SELECT username
        FROM admin
        WHERE id = %s
    """, (new_admin_id,))
    new_admin_username = cur.fetchone()[0]

    cur.execute("""
        UPDATE student
        SET added_by = %s
        WHERE added_by = %s
    """, (new_admin_id, admin_id))

    cur.execute("""
        INSERT INTO deleted_admin
        (id, fullname, username, email, campus, deleted_by)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (
        admin_id, fullname, username, email, campus, deleter
    ))

    cur.execute("DELETE FROM admin WHERE id = %s", (admin_id,))

    cur.execute("""
        INSERT INTO admin_logs (admin_username, campus, action)
        VALUES (%s, %s, %s)
    """, (
        deleter,
        campus,
        f"Deleted admin: {fullname} ({username}) and reassigned students into admin {new_admin_username}"
    ))

    conn.commit()
    cur.close()
    conn.close()

    return redirect(url_for("admin.addAdmin"))

@admin_bp.route("/verify-new-admin", methods=["GET", "POST"])
def verify_new_admin():
    error = None
    success = None
    remaining = None

    if "new_admin_email" not in session:
        return redirect(url_for("admin.addAdmin"))

    if request.method == "POST":
        action = request.form.get("action")

        if action == "resend":
            elapsed = int(time.time() - session.get("new_admin_otp_time", 0))

            if elapsed < 60:
                remaining = 60 - elapsed
                error = "Please wait before resending OTP."
            else:
                otp = generate_otp()
                session["new_admin_otp"] = otp
                session["new_admin_otp_time"] = time.time()
                send_otp_email(session["new_admin_email"], otp)
                success = "A new OTP has been sent."

        if action == "verify":
            user_otp = request.form.get("otp", "").strip()

            if not user_otp:
                error = "Please enter the OTP."
            elif time.time() - session["new_admin_otp_time"] > 300:
                error = "OTP expired."
            elif user_otp != session["new_admin_otp"]:
                error = "Invalid OTP."
            else:
                data = session["new_admin_data"]

                conn = get_db_connection()
                cur = conn.cursor()

                cur.execute("""
                    INSERT INTO admin (fullname, username, email, campus, password)
                    VALUES (%s, %s, %s, %s, %s)
                """, (
                    data["fullname"],
                    data["username"],
                    data["email"],
                    data["campus"],
                    data["password"]
                ))

                cur.execute("""
                    INSERT INTO admin_logs (admin_username, campus, action)
                    VALUES (%s, %s, %s)
                """, (
                    session["admin_username"],
                    data["campus"],
                    f"Added new admin '{data['username']}' (email verified)"
                ))

                conn.commit()
                cur.close()
                conn.close()

                session.pop("new_admin_data", None)
                session.pop("new_admin_otp", None)
                session.pop("new_admin_otp_time", None)
                session.pop("new_admin_email", None)

                return redirect(url_for("admin.addAdmin", success="verified"))

    return render_template(
        "admin/adminVerifyOtp.html",
        error=error,
        success=success,
        remaining=remaining
    )

@admin_bp.route("/admin_logs/<username>")
def get_admin_logs(username):
    if "admin_username" not in session:
        return jsonify([])

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT admin_username, action, created_at
        FROM admin_logs
        WHERE admin_username = %s
        ORDER BY created_at DESC
    """, (username,))

    logs = cur.fetchall()
    cur.close()
    conn.close()

    return jsonify([
        {
            "admin_username": log[0],
            "action": log[1],
            "created_at": log[2].strftime("%Y-%m-%d %H:%M")
        }
        for log in logs
    ])

@admin_bp.route("/editAdmin", methods=["POST"])
def editAdmin():
    if "admin_username" not in session:
        return jsonify(success=False, message="Unauthorized")

    data = request.get_json()
    admin_id = data.get("id")
    fullname = data.get("fullname")
    username = data.get("username")
    email = data.get("email")
    campus = data.get("campus")

    if not admin_id:
        return jsonify(success=False, message="Missing admin ID")

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT fullname, username, email, campus
            FROM admin
            WHERE id = %s
        """, (admin_id,))
        old = cur.fetchone()

        if not old:
            return jsonify(success=False, message="Admin not found")

        old_fullname, old_username, old_email, old_campus = old

        changes = []

        if fullname != old_fullname:
            changes.append(f"fullname '{old_fullname}' → '{fullname}'")

        if username != old_username:
            changes.append(f"username '{old_username}' → '{username}'")

        if email != old_email:
            changes.append(f"email '{old_email}' → '{email}'")

        if campus != old_campus:
            changes.append(f"campus '{old_campus}' → '{campus}'")

        if not changes:
            return jsonify(success=False, message="No changes detected")

        cur.execute("""
            UPDATE admin
            SET fullname=%s, username=%s, email=%s, campus=%s
            WHERE id=%s
        """, (fullname, username, email, campus, admin_id))

        cur.execute("""
            SELECT campus FROM admin WHERE username = %s
        """, (session["admin_username"],))
        admin_campus = cur.fetchone()[0]

        action = f"Edited admin '{old_username}': " + ", ".join(changes)

        cur.execute("""
            INSERT INTO admin_logs (admin_username, campus, action)
            VALUES (%s, %s, %s)
        """, (
            session["admin_username"],
            admin_campus,
            action
        ))

        conn.commit()
        cur.close()
        conn.close()

        return jsonify(success=True)

    except psycopg2.Error as e:
        return jsonify(success=False, message=str(e))

@admin_bp.route("/addProgram", methods=["POST"])
def addProgram():
    if "admin_username" not in session:
        return jsonify(success=False, message="Unauthorized")

    program_name = request.form.get("program_name")
    campus = request.form.get("campus")
    category_letters = request.form.get("category_letters")
    category_descriptions = request.form.get("category_descriptions")

    if not category_letters or not category_descriptions:
        return jsonify(success=False, message="Select at least one category")

    if not program_name or not campus:
        return jsonify(success=False, message="Missing data")

    admin_username = session["admin_username"]

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute(
            "SELECT campus FROM admin WHERE username = %s",
            (admin_username,)
        )
        admin_campus = cur.fetchone()[0]

        cur.execute("""
            INSERT INTO program (program_name, campus, category_letter, category_description)
            VALUES (%s, %s, %s, %s)
        """, (
            program_name,
            campus,
            category_letters,
            category_descriptions
        ))

        cur.execute("""
            INSERT INTO admin_logs (admin_username, campus, action)
            VALUES (%s, %s, %s)
        """, (
            admin_username,
            admin_campus,
            f"Added new program '{program_name}'"
        ))

        conn.commit()
        cur.close()
        conn.close()

        return jsonify(success=True)

    except Exception as e:
        return jsonify(success=False, message=str(e))
    
@admin_bp.route("/addProgramColor", methods=["POST"])
def addProgramColor():
    if "admin_username" not in session:
        return jsonify(success=False, message="Unauthorized")

    data = request.get_json()
    program_name = data.get("program_name")
    color = data.get("color")

    if not program_name or not color:
        return jsonify(success=False, message="Missing data")

    admin_username = session["admin_username"]

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("SELECT campus FROM admin WHERE username = %s", (admin_username,))
        admin_campus = cur.fetchone()[0]

        cur.execute("""
            UPDATE program
            SET color = %s
            WHERE program_name = %s
        """, (color, program_name))

        cur.execute("""
            INSERT INTO admin_logs (admin_username, campus, action)
            VALUES (%s, %s, %s)
        """, (
            admin_username,
            admin_campus,
            f"Set color '{color}' for program '{program_name}'"
        ))

        conn.commit()
        cur.close()
        conn.close()

        return jsonify(success=True)

    except Exception as e:
        return jsonify(success=False, message=str(e))

@admin_bp.route("/program")
def program():
    if "admin_username" not in session:
        return redirect(url_for("admin.login"))

    admin_username = session["admin_username"]
    selected_campus = request.args.get("campus", "")

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("SELECT id, campus FROM admin WHERE username = %s", (admin_username,))
    admin = cur.fetchone()

    cur.execute("SELECT id, campus FROM super_admin WHERE username = %s", (admin_username,))
    is_super_admin = cur.fetchone()

    if not admin and not is_super_admin:
        cur.close()
        conn.close()
        return redirect(url_for("admin.login"))

    if is_super_admin:
        admin_campus = is_super_admin["campus"] or "ALL"
        if selected_campus:
            cur.execute("""
                SELECT id, program_name, created_at, is_active, color
                FROM program
                WHERE campus = %s
                ORDER BY created_at DESC
            """, (selected_campus,))
        else:
            cur.execute("""
                SELECT id, program_name, created_at, is_active, color
                FROM program
                ORDER BY created_at DESC
            """)
    else:
        admin_campus = admin["campus"]
        cur.execute("""
            SELECT id, program_name, created_at, is_active, color
            FROM program
            WHERE campus = %s
            ORDER BY created_at DESC
        """, (admin_campus,))

    programs = cur.fetchall()

    cur.close()
    conn.close()

    if request.args.get("ajax"):
        return render_template("admin/_program_rows.html", programs=programs)

    return render_template(
        "admin/program.html",
        admin_username=admin_username,
        is_super_admin=is_super_admin,
        programs=programs,
        admin_campus=admin_campus,
        selected_campus=selected_campus
    )

@admin_bp.route("/deleteProgram", methods=["POST"])
def deleteProgram():
    if "admin_username" not in session:
        return jsonify(success=False, message="Unauthorized")

    data = request.get_json()
    program_id = data.get("program_id")

    if not program_id:
        return jsonify(success=False, message="Missing program ID")

    admin_username = session["admin_username"]

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute(
            "SELECT campus FROM admin WHERE username = %s",
            (admin_username,)
        )
        admin_campus = cur.fetchone()[0]

        cur.execute(
            "SELECT program_name FROM program WHERE id = %s",
            (program_id,)
        )
        row = cur.fetchone()

        if not row:
            return jsonify(success=False, message="Program not found")

        program_name = row[0]

        cur.execute(
            "DELETE FROM program WHERE id = %s",
            (program_id,)
        )

        cur.execute("""
            INSERT INTO admin_logs (admin_username, campus, action)
            VALUES (%s, %s, %s)
        """, (
            admin_username,
            admin_campus,
            f"Deleted program '{program_name}'"
        ))

        conn.commit()
        cur.close()
        conn.close()

        return jsonify(success=True)

    except Exception as e:
        return jsonify(success=False, message=str(e))

@admin_bp.route("/editProgram", methods=["POST"])
def editProgram():
    if "admin_username" not in session:
        return jsonify(success=False, message="Unauthorized")

    data = request.get_json()
    program_id = data.get("id")
    new_name = data.get("name")
    new_color = data.get("color")

    if not program_id or (not new_name and not new_color):
        return jsonify(success=False, message="Missing data")

    admin_username = session["admin_username"]

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("SELECT campus FROM admin WHERE username = %s", (admin_username,))
        admin_campus = cur.fetchone()[0]

        cur.execute("SELECT program_name, color FROM program WHERE id = %s", (program_id,))
        row = cur.fetchone()
        if not row:
            return jsonify(success=False, message="Program not found")
        old_name, old_color = row

        fields_to_update = []
        params = []

        action_parts = []

        if new_name and new_name != old_name:
            fields_to_update.append("program_name = %s")
            params.append(new_name)
            action_parts.append(f"Edited program '{old_name}' → '{new_name}'")

        if new_color and new_color != old_color:
            fields_to_update.append("color = %s")
            params.append(new_color)
            action_parts.append(f"Edited program color '{old_color}' → '{new_color}'")

        if not fields_to_update:
            return jsonify(success=False, message="No changes detected")

        params.append(program_id)

        sql = f"UPDATE program SET {', '.join(fields_to_update)} WHERE id = %s"
        cur.execute(sql, params)

        action_text = "; ".join(action_parts)
        cur.execute("""
            INSERT INTO admin_logs (admin_username, campus, action)
            VALUES (%s, %s, %s)
        """, (admin_username, admin_campus, action_text))

        conn.commit()
        cur.close()
        conn.close()

        return jsonify(success=True)

    except Exception as e:
        return jsonify(success=False, message=str(e))

@admin_bp.route("/addParticipant", methods=["POST"])
def addParticipant():
    if "admin_username" not in session:
        return redirect(url_for("admin.login"))

    fullname = request.form["fullname"].strip().upper()
    exam_id = request.form["exam_id"].strip()
    gender = request.form["gender"]
    email = request.form["email"].strip()

    admin_username = session["admin_username"]

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute(
            "SELECT id, campus FROM admin WHERE username = %s",
            (admin_username,)
        )
        admin = cur.fetchone()
        if not admin:
            cur.close()
            conn.close()
            return redirect(url_for("admin.dashboard", error="Admin not found"))

        admin_id, admin_campus = admin

        cur.execute(
            "SELECT 1 FROM student WHERE exam_id = %s OR email = %s",
            (exam_id, email)
        )
        if cur.fetchone():
            cur.close()
            conn.close()
            return redirect(url_for("admin.dashboard", error="❌ Examination ID or Email already exists!"))

        cur.execute("""
            INSERT INTO student 
                (fullname, exam_id, gender, email, campus, added_by)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (fullname, exam_id, gender, email, admin_campus, admin_id))

        cur.execute("""
            INSERT INTO admin_logs (admin_username, campus, action)
            VALUES (%s, %s, %s)
        """, (admin_username, admin_campus, f"Added new student '{fullname}'"))

        conn.commit()

        cur.execute(
            "SELECT fullname, campus FROM admin WHERE username = %s", (admin_username,)
        )
        fullname, admin_campus = cur.fetchone()

        cur.execute("""
            SELECT COUNT(*) FROM student
            WHERE campus = %s AND EXTRACT(YEAR FROM created_at) = %s
        """, (admin_campus, datetime.now().year))
        total_students = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) 
            FROM student s
            LEFT JOIN student_survey_answer a
                ON a.student_id = s.id OR a.exam_id = s.exam_id
            WHERE s.campus = %s
            AND EXTRACT(YEAR FROM s.created_at) = %s
            AND (a.preferred_program IS NULL OR a.preferred_program = '');
        """, (admin_campus, datetime.now().year))
        pending_students = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(DISTINCT admin_username)
            FROM admin_logs
            WHERE created_at >= NOW() - INTERVAL '1 month';
        """)
        active_admins = cur.fetchone()[0]

        cur.execute("""
            SELECT a.fullname,
                   CASE 
                       WHEN l.last_login >= NOW() - INTERVAL '1 month' THEN 'Active'
                       ELSE 'Inactive'
                   END AS status
            FROM admin a
            LEFT JOIN (
                SELECT admin_username, MAX(created_at) AS last_login
                FROM admin_logs
                GROUP BY admin_username
            ) l ON a.username = l.admin_username
            ORDER BY a.fullname ASC;
        """)
        admins = cur.fetchall()

        cur.close()
        conn.close()

        return render_template(
            "admin/dashboard.html",
            admin_username=admin_username,
            fullname=fullname,
            admin_campus=admin_campus,
            total_students=total_students,
            pending_students=pending_students,
            active_admins=active_admins,
            year=datetime.now().year,
            available_years=[datetime.now().year],
            searched_students=[],
            search_query="",
            admins=admins,
            success=True,
            message=f"Participant added successfully!"
        )

    except Exception as e:
        return render_template(
            "admin/dashboard.html",
            manual_error=f"⚠️ Error: {str(e)}"
        )

@admin_bp.route("/upload", methods=["POST"])
def upload():
    if "admin_username" not in session:
        return redirect(url_for("admin.login"))

    if "file" not in request.files:
        return redirect(url_for("admin.dashboard", error="No file part"))

    file = request.files["file"]

    if file.filename == "":
        return redirect(url_for("admin.dashboard", error="No selected file"))

    if not allowed_file(file.filename):
        return redirect(url_for(
            "admin.dashboard",
            error="Only Excel files (.xlsx, .xls) are allowed"
        ))

    admin_username = session["admin_username"]

    try:
        df = pd.read_excel(file, dtype=str)
        df.columns = df.columns.str.lower().str.strip()

        required_cols = {"fullname", "exam_id", "gender", "email"}
        if not required_cols.issubset(df.columns):
            return redirect(url_for(
                "admin.dashboard",
                error="Excel must contain columns: fullname, exam_id, gender, email"
            ))

        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute(
            "SELECT id, campus FROM admin WHERE username = %s",
            (admin_username,)
        )
        admin = cur.fetchone()

        if not admin:
            return redirect(url_for("admin.dashboard", error="Admin not found"))

        admin_id, admin_campus = admin

        inserted = 0
        skipped = 0

        for _, row in df.iterrows():
            fullname = (row.get("fullname") or "").strip().upper()
            exam_id = (row.get("exam_id") or "").strip()
            gender = (row.get("gender") or "").strip()
            email = (row.get("email") or "").strip()

            if not fullname or not exam_id or not email:
                skipped += 1
                continue

            cur.execute(
                "SELECT 1 FROM student WHERE exam_id = %s OR email = %s",
                (exam_id, email)
            )
            if cur.fetchone():
                skipped += 1
                continue

            cur.execute("""
                INSERT INTO student
                    (fullname, exam_id, gender, email, campus, added_by)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                fullname,
                exam_id,
                gender,
                email,
                admin_campus,
                admin_id
            ))

            inserted += 1

        if inserted > 0:
            cur.execute("""
                INSERT INTO admin_logs (admin_username, campus, action)
                VALUES (%s, %s, %s)
            """, (
                admin_username,
                admin_campus,
                f"Added {inserted} new student through excel"
            ))

        conn.commit()
        cur.close()
        conn.close()

        return redirect(url_for(
            "admin.dashboard",
            success=1,
            message=f"Upload complete! Inserted: {inserted}, Skipped: {skipped}"
        ))

    except Exception as e:
        return redirect(url_for(
            "admin.dashboard",
            error=f"Error reading Excel file: {str(e)}"
        ))

PER_PAGE = 10

@admin_bp.route("/respondents")
def respondents():
    if "admin_username" not in session:
        return redirect(url_for("admin.login"))

    username = session["admin_username"]

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute(
        "SELECT username, campus FROM super_admin WHERE username = %s",
        (username,)
    )
    super_admin = cur.fetchone()

    is_super_admin = False
    admin_campus = None

    if super_admin:
        is_super_admin = True
        admin_campus = super_admin[1]
    else:
        cur.execute(
            "SELECT username, campus FROM admin WHERE username = %s",
            (username,)
        )
        admin = cur.fetchone()

        if not admin:
            cur.close()
            conn.close()
            return redirect(url_for("admin.login"))

        admin_campus = admin[1]

    cur.execute("""
        SELECT DISTINCT EXTRACT(YEAR FROM created_at)::int
        FROM student
        ORDER BY 1 DESC;
    """)
    available_years = [row[0] for row in cur.fetchall()]

    selected_year = request.args.get("year", type=int) or datetime.now().year
    search_query = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "")
    selected_campus = request.args.get("campus", "")
    selected_program = request.args.get("program", "")
    page = request.args.get("page", 1, type=int)

    if is_super_admin:
        if selected_campus:
            cur.execute("""
                SELECT DISTINCT program_name FROM program WHERE campus = %s
                UNION
                SELECT DISTINCT preferred_program
                FROM student_survey_answer sa
                JOIN student s ON sa.exam_id = s.exam_id
                WHERE s.campus = %s AND preferred_program IS NOT NULL
                ORDER BY 1;
            """, (selected_campus, selected_campus))
        else:
            cur.execute("""
                SELECT DISTINCT program_name FROM program
                UNION
                SELECT DISTINCT preferred_program
                FROM student_survey_answer
                WHERE preferred_program IS NOT NULL
                ORDER BY 1;
            """)
    else:
        cur.execute("""
            SELECT DISTINCT program_name FROM program WHERE campus = %s
            UNION
            SELECT DISTINCT preferred_program
            FROM student_survey_answer sa
            JOIN student s ON sa.exam_id = s.exam_id
            WHERE s.campus = %s AND preferred_program IS NOT NULL
            ORDER BY 1;
        """, (admin_campus, admin_campus))

    programs = [row[0] for row in cur.fetchall()]

    params = [selected_year]
    conditions = []

    if is_super_admin:
        if selected_campus:
            conditions.append("s.campus = %s")
            params.append(selected_campus)
    else:
        conditions.append("s.campus = %s")
        params.append(admin_campus)

    if selected_program:
        conditions.append("TRIM(sa.preferred_program) ILIKE TRIM(%s)")
        params.append(selected_program)

    if search_query:
        conditions.append("(s.fullname ILIKE %s OR s.exam_id ILIKE %s)")
        params.extend([f"%{search_query}%", f"%{search_query}%"])

    where_clause = " AND ".join(conditions)
    if where_clause:
        where_clause = "AND " + where_clause

    sql = f"""
        SELECT s.exam_id, s.fullname, sa.preferred_program,
               sa.pair1, sa.pair2, sa.pair3, sa.pair4, sa.pair5,
               sa.pair6, sa.pair7, sa.pair8, sa.pair9, sa.pair10,
               sa.pair11, sa.pair12, sa.pair13, sa.pair14, sa.pair15,
               sa.pair16, sa.pair17, sa.pair18, sa.pair19, sa.pair20,
               sa.pair21, sa.pair22, sa.pair23, sa.pair24, sa.pair25,
               sa.pair26, sa.pair27, sa.pair28, sa.pair29, sa.pair30,
               sa.pair31, sa.pair32, sa.pair33, sa.pair34, sa.pair35,
               sa.pair36, sa.pair37, sa.pair38, sa.pair39, sa.pair40,
               sa.pair41, sa.pair42, sa.pair43, sa.pair44, sa.pair45,
               sa.pair46, sa.pair47, sa.pair48, sa.pair49, sa.pair50,
               sa.pair51, sa.pair52, sa.pair53, sa.pair54, sa.pair55,
               sa.pair56, sa.pair57, sa.pair58, sa.pair59, sa.pair60,
               sa.pair61, sa.pair62, sa.pair63, sa.pair64, sa.pair65,
               sa.pair66, sa.pair67, sa.pair68, sa.pair69, sa.pair70,
               sa.pair71, sa.pair72, sa.pair73, sa.pair74, sa.pair75,
               sa.pair76, sa.pair77, sa.pair78, sa.pair79, sa.pair80,
               sa.pair81, sa.pair82, sa.pair83, sa.pair84, sa.pair85,
               sa.pair86
        FROM student s
        LEFT JOIN student_survey_answer sa ON s.exam_id = sa.exam_id
        WHERE EXTRACT(YEAR FROM s.created_at) = %s
        {where_clause}
        ORDER BY s.fullname ASC;
    """

    cur.execute(sql, params)
    raw_students = cur.fetchall()

    students = []

    for row in raw_students:
        exam_id, fullname, preferred_program, *pairs = row
        answers_clean = [p for p in pairs if p]

        top_letters = [l for l, _ in Counter(answers_clean).most_common(3)]
        program_letters = []

        if preferred_program:
            cur.execute(
                "SELECT category_letter FROM program WHERE program_name = %s",
                (preferred_program,)
            )
            result = cur.fetchone()
            if result:
                program_letters = result[0].split(",")

        if not preferred_program and not answers_clean:
            match_status = "——"
        elif any(letter in program_letters for letter in top_letters):
            match_status = "Match"
        else:
            match_status = "Not Match"

        students.append((exam_id, fullname, preferred_program, match_status))

    cur.close()
    conn.close()

    if status_filter == "match":
        students = [s for s in students if s[3] == "Match"]
    elif status_filter == "not_match":
        students = [s for s in students if s[3] == "Not Match"]

    total_students = len(students)
    total_pages = ceil(total_students / PER_PAGE)
    start = (page - 1) * PER_PAGE
    end = start + PER_PAGE
    students_paginated = students[start:end]

    return render_template(
        "admin/respondents.html",
        admin_username=username,
        admin_campus=admin_campus,
        is_super_admin=is_super_admin,
        available_years=available_years,
        year=selected_year,
        students=students_paginated,
        search_query=search_query,
        status_filter=status_filter,
        selected_campus=selected_campus,
        selected_program=selected_program,
        programs=programs,
        page=page,
        total_pages=total_pages
    )

@admin_bp.route("/adminSurveyResult")
def adminSurveyResult():
    exam_id = request.args.get("exam_id")
    if not exam_id:
        flash("Invalid request. No Exam ID provided.")
        return redirect(url_for("admin.dashboard"))
    
    admin_username = session["admin_username"]

    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute(
        "SELECT fullname, campus FROM super_admin WHERE username = %s",
        (admin_username,)
    )
    super_admin = cur.fetchone()

    is_super_admin = False
    admin_fullname = None
    admin_campus = None

    if super_admin:
        is_super_admin = True
        admin_fullname = super_admin[0]
        admin_campus = super_admin[1]
    else:
        cur.execute(
            "SELECT fullname, campus FROM admin WHERE username = %s",
            (admin_username,)
        )
        admin = cur.fetchone()

        if not admin:
            cur.close()
            conn.close()
            return redirect(url_for("admin.login"))

        admin_fullname = admin[0]
        admin_campus = admin[1]

    cur.execute("""
        SELECT s.exam_id, s.fullname, s.created_at, s.campus, s.photo, 
               sa.preferred_program, sa.ai_explanation,
               sa.pair1, sa.pair2, sa.pair3, sa.pair4, sa.pair5,
               sa.pair6, sa.pair7, sa.pair8, sa.pair9, sa.pair10,
               sa.pair11, sa.pair12, sa.pair13, sa.pair14, sa.pair15,
               sa.pair16, sa.pair17, sa.pair18, sa.pair19, sa.pair20,
               sa.pair21, sa.pair22, sa.pair23, sa.pair24, sa.pair25,
               sa.pair26, sa.pair27, sa.pair28, sa.pair29, sa.pair30,
               sa.pair31, sa.pair32, sa.pair33, sa.pair34, sa.pair35,
               sa.pair36, sa.pair37, sa.pair38, sa.pair39, sa.pair40,
               sa.pair41, sa.pair42, sa.pair43, sa.pair44, sa.pair45,
               sa.pair46, sa.pair47, sa.pair48, sa.pair49, sa.pair50,
               sa.pair51, sa.pair52, sa.pair53, sa.pair54, sa.pair55,
               sa.pair56, sa.pair57, sa.pair58, sa.pair59, sa.pair60,
               sa.pair61, sa.pair62, sa.pair63, sa.pair64, sa.pair65,
               sa.pair66, sa.pair67, sa.pair68, sa.pair69, sa.pair70,
               sa.pair71, sa.pair72, sa.pair73, sa.pair74, sa.pair75,
               sa.pair76, sa.pair77, sa.pair78, sa.pair79, sa.pair80,
               sa.pair81, sa.pair82, sa.pair83, sa.pair84, sa.pair85,
               sa.pair86
        FROM student s
        LEFT JOIN student_survey_answer sa ON s.exam_id = sa.exam_id
        WHERE s.exam_id = %s;
    """, (exam_id,))
    
    row = cur.fetchone()

    if not row:
        return "No survey results found."

    created_at = row[2]

    start_year = created_at.year
    end_year = start_year + 1
    year = f"{start_year}-{end_year}"

    student_results = {
        "exam_id": row[0],
        "fullname": row[1],
        "created_at": row[2],
        "campus": row[3],
        "photo": row[4],
        "preferred_program": row[5],
        "ai_explanation": format_ai_explanation_for_pdf(row[6]),
        "answers": [row[i] for i in range(7, 93)]
    }

    answers_clean = student_results["answers"]
    preferred = student_results["preferred_program"]

    top_letters = []
    program_letters = []

    if answers_clean:
        letter_counts = Counter(answers_clean)
        top_letters = [letter for letter, _ in letter_counts.most_common(3)]

    if preferred:
        cur.execute("SELECT category_letter FROM program WHERE program_name = %s", (preferred,))
        result = cur.fetchone()
        program_letters = result[0].split(",") if result else []

    if not preferred and not answers_clean:
        match_status = "Not Yet Answer"
    elif any(letter in program_letters for letter in top_letters):
        match_status = "Match"
    else:
        match_status = "Not Match"

    predicted_programs = []

    if top_letters:
        conditions = " OR ".join(["category_letter ILIKE %s"] * len(top_letters))
        values = [f"%{letter}%" for letter in top_letters]

        query = f"""
            SELECT program_name, category_letter
            FROM program
            WHERE {conditions}
            ORDER BY program_name
            LIMIT 5
        """
        cur.execute(query, values)
        predicted_programs = cur.fetchall()

    conn.close()

    return render_template(
        "admin/adminSurveyResult.html",
        admin_username=session["admin_username"],
        admin_fullname=admin_fullname,
        admin_campus=admin_campus,
        is_super_admin=is_super_admin,
        student_results=student_results,
        student_campus=student_results["campus"],
        top_letters=top_letters,
        letter_descriptions=letter_descriptions,
        match_status=match_status,
        predicted_programs=predicted_programs,
        year=year
    )

@admin_bp.route('/download_result/<exam_id>')
def download_result(exam_id):
    if not exam_id:
        flash("Invalid request.")
        return redirect(url_for('admin.dashboard'))

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT s.exam_id, s.fullname, s.created_at, s.campus, s.photo,
               sa.preferred_program, sa.ai_explanation,
               sa.pair1, sa.pair2, sa.pair3, sa.pair4, sa.pair5,
               sa.pair6, sa.pair7, sa.pair8, sa.pair9, sa.pair10,
               sa.pair11, sa.pair12, sa.pair13, sa.pair14, sa.pair15,
               sa.pair16, sa.pair17, sa.pair18, sa.pair19, sa.pair20,
               sa.pair21, sa.pair22, sa.pair23, sa.pair24, sa.pair25,
               sa.pair26, sa.pair27, sa.pair28, sa.pair29, sa.pair30,
               sa.pair31, sa.pair32, sa.pair33, sa.pair34, sa.pair35,
               sa.pair36, sa.pair37, sa.pair38, sa.pair39, sa.pair40,
               sa.pair41, sa.pair42, sa.pair43, sa.pair44, sa.pair45,
               sa.pair46, sa.pair47, sa.pair48, sa.pair49, sa.pair50,
               sa.pair51, sa.pair52, sa.pair53, sa.pair54, sa.pair55,
               sa.pair56, sa.pair57, sa.pair58, sa.pair59, sa.pair60,
               sa.pair61, sa.pair62, sa.pair63, sa.pair64, sa.pair65,
               sa.pair66, sa.pair67, sa.pair68, sa.pair69, sa.pair70,
               sa.pair71, sa.pair72, sa.pair73, sa.pair74, sa.pair75,
               sa.pair76, sa.pair77, sa.pair78, sa.pair79, sa.pair80,
               sa.pair81, sa.pair82, sa.pair83, sa.pair84, sa.pair85,
               sa.pair86
        FROM student s
        LEFT JOIN student_survey_answer sa ON s.exam_id = sa.exam_id
        WHERE s.exam_id = %s;
    """, (exam_id,))

    row = cur.fetchone()

    created_at = row[2]

    start_year = created_at.year
    end_year = start_year + 1
    year = f"{start_year}-{end_year}"

    if not row:
        return "Survey results not found", 404

    student_data = {
        "exam_id": row[0],
        "fullname": row[1],
        "created_at": row[2],
        "campus": row[3],
        "photo": row[4],
        "preferred_program": row[5],
        "ai_explanation": format_ai_explanation_for_pdf(row[6]),
        "answers": [row[i] for i in range(7, 93)]
    }

    answers_clean = [a for a in student_data["answers"] if a]
    letter_counts = Counter(answers_clean)
    top_letters = [l for l, _ in letter_counts.most_common(3)]

    preferred = student_data["preferred_program"]
    if not preferred and not answers_clean:
        match_status = "Not Yet Answer"
    elif preferred in preferred_program_map and any(
        l in preferred_program_map[preferred] for l in top_letters
    ):
        match_status = "Match"
    else:
        match_status = "Not Match"

    predicted_programs = []

    if top_letters:
        conditions = " OR ".join(["category_letter ILIKE %s"] * len(top_letters))
        values = [f"%{letter}%" for letter in top_letters]

        query = f"""
            SELECT program_name, category_letter
            FROM program
            WHERE {conditions}
            ORDER BY program_name
            LIMIT 5
        """
        cur.execute(query, values)
        predicted_programs = cur.fetchall()

    student_photo_base64 = None

    student_photo_base64 = student_photo_to_base64(student_data.get("photo"))

    cpsu_logo = image_to_base64("cpsulogo.png")
    bagong_logo = image_to_base64("bagong-pilipinas-logo.png")
    safe_logo = image_to_base64("logo.png")

    html = render_template(
        "admin/adminSurveyResultPDF.html",
        student_data=student_data,
        top_letters=top_letters,
        match_status=match_status,
        student_campus=student_data["campus"],
        letter_descriptions=letter_descriptions,
        year=year,
        predicted_programs=predicted_programs,
        cpsu_logo_base64=cpsu_logo,
        bagong_logo_base64=bagong_logo,
        safe_logo_base64=safe_logo,
        student_photo_base64=student_photo_base64
    )

    pdf_io = BytesIO()
    HTML(string=html, base_url=current_app.root_path).write_pdf(pdf_io)
    pdf_io.seek(0)

    filename = f"Career_Survey_Result_{student_data['fullname']}.pdf"

    return send_file(
        pdf_io,
        mimetype="application/pdf",
        download_name=filename,
        as_attachment=True
    )

PER_PAGE = 10

@admin_bp.route("/adminInventory")
def adminInventory():
    if "admin_username" not in session:
        return redirect(url_for("admin.login"))

    username = session["admin_username"]

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cur.execute("""
        SELECT id, campus
        FROM super_admin
        WHERE username = %s
    """, (username,))
    admin = cur.fetchone()

    is_super_admin = False
    admin_campus = None

    if admin:
        is_super_admin = True
        admin_id, admin_campus = admin
    else:
        cur.execute("""
            SELECT id, campus
            FROM admin
            WHERE username = %s
        """, (username,))
        admin = cur.fetchone()

        if not admin:
            cur.close()
            conn.close()
            return redirect(url_for("admin.login"))

        admin_id, admin_campus = admin

    selected_campus = request.args.get("campus", "")
    search_query = request.args.get("q", "")
    page = request.args.get("page", 1, type=int)

    cur.execute("""
        SELECT DISTINCT EXTRACT(YEAR FROM created_at)::int 
        FROM student 
        ORDER BY 1 DESC;
    """)
    available_years = [row[0] for row in cur.fetchall()]

    selected_year = request.args.get("year", type=int) or datetime.now().year

    query = """
        SELECT 
            s.id,
            s.exam_id,
            s.fullname,
            COALESCE(f.father_income, 0) + COALESCE(f.mother_income, 0) AS total_income
        FROM student s
        LEFT JOIN family_background f 
            ON f.student_id = s.id
        WHERE EXTRACT(YEAR FROM s.created_at) = %s
          AND (%s = '' OR s.fullname ILIKE %s OR s.exam_id ILIKE %s)
    """

    params = [
        selected_year,
        search_query,
        f"%{search_query}%",
        f"%{search_query}%"
    ]

    if is_super_admin:
        if selected_campus:
            query += " AND s.campus = %s"
            params.append(selected_campus)
    else:
        query += " AND s.campus = %s"
        params.append(admin_campus)

    query += " ORDER BY s.fullname ASC"

    cur.execute(query, params)
    students = cur.fetchall()

    sort = request.args.get("sort", "income_asc")

    classified_students = []
    for row in students:
        id, exam_id, fullname, total_income = row

        if total_income == 0:
            category = "____"
            income_display = None
        else:
            income_display = total_income
            if total_income <= 10000:
                category = "Low Income"
            elif total_income <= 20000:
                category = "Lower-Middle"
            elif total_income <= 40000:
                category = "Middle"
            elif total_income <= 80000:
                category = "Middle-Upper"
            else:
                category = "High Income"

        classified_students.append(
            (id, exam_id, fullname, income_display, category)
        )

    if sort in ["name_asc", "name_desc"]:
        classified_students.sort(
            key=lambda x: x[2].lower(),
            reverse=(sort == "name_desc")
        )
    elif sort == "income_asc":
        classified_students.sort(
            key=lambda x: x[3] if x[3] is not None else float("inf")
        )
    elif sort == "income_desc":
        classified_students.sort(
            key=lambda x: -(x[3] if x[3] is not None else 0)
        )

    cur.close()
    conn.close()

    total_students = len(classified_students)
    total_pages = max(1, ceil(total_students / PER_PAGE))
    start = (page - 1) * PER_PAGE
    end = start + PER_PAGE
    students_paginated = classified_students[start:end]

    return render_template(
        "admin/adminInventory.html",
        admin_username=username,
        admin_campus=admin_campus,
        is_super_admin=is_super_admin,
        available_years=available_years,
        year=selected_year,
        students=students_paginated,
        search_query=search_query,
        page=page,
        total_pages=total_pages,
        sort=sort,
        selected_campus=selected_campus
    )

@admin_bp.route("/adminInventoryResult")
def adminInventoryResult():
    if "admin_username" not in session:
        return redirect(url_for("admin.login"))

    student_id = request.args.get("student_id")
    if not student_id:
        flash("Invalid request. No student ID provided.")
        return redirect(url_for("admin.adminInventory"))

    username = session["admin_username"]

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cur.execute("""
        SELECT id, campus
        FROM super_admin
        WHERE username = %s
    """, (username,))
    admin = cur.fetchone()

    is_super_admin = False
    admin_campus = None

    if admin:
        is_super_admin = True
        admin_id, admin_campus = admin
    else:
        cur.execute("""
            SELECT id, campus
            FROM admin
            WHERE username = %s
        """, (username,))
        admin = cur.fetchone()

        if not admin:
            cur.close()
            conn.close()
            return redirect(url_for("admin.login"))

        admin_id, admin_campus = admin

    cur.execute("""
        SELECT 
            s.id AS id,
            s.fullname, s.gender, s.email, s.campus, s.photo,
            sa.nickname, sa.present_address, sa.provincial_address,
            sa.date_of_birth, sa.place_of_birth, sa.age, sa.birth_order, sa.siblings_count,
            sa.civil_status, sa.religion, sa.nationality,
            sa.home_phone, sa.mobile_no, sa.email AS personal_email,
            sa.weight, sa.height, sa.blood_type, sa.hobbies, sa.talents,
            sa.emergency_name, sa.emergency_relationship, sa.emergency_address, sa.emergency_contact,
            sb.father_name, sb.father_age, sb.father_education, sb.father_occupation,
            sb.father_income, sb.father_contact, sb.mother_name, sb.mother_age, sb.mother_education,
            sb.mother_occupation, sb.mother_income, sb.mother_contact, 
            sc.married_living_together, sc.living_not_married, sc.legally_separated,
            sc.mother_widow, sc.father_widower, sc.separated, sc.father_another_family, sc.mother_another_family,
            sd.elementary_school_name, sd.elementary_year_graduated, sd.elementary_awards,
            sd.junior_high_school_name, sd.junior_high_year_graduated, sd.junior_high_awards,
            sd.senior_high_school_name, sd.senior_high_year_graduated, sd.senior_high_awards,
            sd.senior_high_track, sd.senior_high_strand, sd.subject_interested, sd.org_membership,
            sd.study_finance, sd.course_personal_choice, sd.influenced_by, sd.feeling_about_course, sd.personal_choice,
            se.bullying, se.bullying_when, se.bullying_bother,
            se.suicidal_thoughts, se.suicidal_thoughts_when, se.suicidal_thoughts_bother,
            se.suicidal_attempts, se.suicidal_attempts_when, se.suicidal_attempts_bother,
            se.panic_attacks, se.panic_attacks_when, se.panic_attacks_bother,
            se.anxiety, se.anxiety_when, se.anxiety_bother,
            se.depression, se.depression_when, se.depression_bother,
            se.self_anger_issues, se.self_anger_issues_when, se.self_anger_issues_bother,
            se.recurring_negative_thoughts, se.recurring_negative_thoughts_when, se.recurring_negative_thoughts_bother,
            se.low_self_esteem, se.low_self_esteem_when, se.low_self_esteem_bother,
            se.poor_study_habits, se.poor_study_habits_when, se.poor_study_habits_bother,
            se.poor_in_decision_making, se.poor_in_decision_making_when, se.poor_in_decision_making_bother,
            se.impulsivity, se.impulsivity_when, se.impulsivity_bother,
            se.poor_sleeping_habits, se.poor_sleeping_habits_when, se.poor_sleeping_habits_bother,
            se.loos_of_appetite, se.loos_of_appetite_when, se.loos_of_appetite_bother,
            se.over_eating, se.over_eating_when, se.over_eating_bother,
            se.poor_hygiene, se.poor_hygiene_when, se.poor_hygiene_bother,
            se.withdrawal_isolation, se.withdrawal_isolation_when, se.withdrawal_isolation_bother,
            se.family_problem, se.family_problem_when, se.family_problem_bother,
            se.other_relationship_problem, se.other_relationship_problem_when, se.other_relationship_problem_bother,
            se.alcohol_addiction, se.alcohol_addiction_when, se.alcohol_addiction_bother,
            se.gambling_addiction, se.gambling_addiction_when, se.gambling_addiction_bother,
            se.drug_addiction, se.drug_addiction_when, se.drug_addiction_bother,
            se.computer_addiction, se.computer_addiction_when, se.computer_addiction_bother,
            se.sexual_harassment, se.sexual_harassment_when, se.sexual_harassment_bother,
            se.sexual_abuse, se.sexual_abuse_when, se.sexual_abuse_bother,
            se.physical_abuse, se.physical_abuse_when, se.physical_abuse_bother,
            se.verbal_abuse, se.verbal_abuse_when, se.verbal_abuse_bother,
            se.pre_marital_sex, se.pre_marital_sex_when, se.pre_marital_sex_bother,
            se.teenage_pregnancy, se.teenage_pregnancy_when, se.teenage_pregnancy_bother,
            se.abortion, se.abortion_when, se.abortion_bother,
            se.extra_marital_affairs, se.extra_marital_affairs_when, se.extra_marital_affairs_bother,
            sf.psychiatrist_before, sf.psychiatrist_reason, sf.psychiatrist_when,
            sf.psychologist_before, sf.psychologist_reason, sf.psychologist_when,
            sf.counselor_before, sf.counselor_reason, sf.counselor_when,
            sg.personal_description
        FROM student s
        LEFT JOIN personal_information sa ON sa.student_id = s.id
        LEFT JOIN family_background sb ON sb.student_id = s.id
        LEFT JOIN status_of_parent sc ON sc.student_id = s.id
        LEFT JOIN academic_information sd ON sd.student_id = s.id
        LEFT JOIN behavior_information se ON se.student_id = s.id
        LEFT JOIN psychological_consultations sf ON sf.student_id = s.id
        LEFT JOIN personal_descriptions sg ON sg.student_id = s.id
        WHERE s.id = %s
    """, (student_id,))

    info = cur.fetchone()

    student_photo_base64 = None
    if info and info["photo"]:
        student_photo_base64 = student_photo_to_base64(info["photo"])

    cur.execute("""
        SELECT reasons, other_reason
        FROM cpsu_enrollment_reason
        WHERE student_id = %s
    """, (student_id,))
    enroll_reason = cur.fetchone()

    cur.execute("""
        SELECT school_choices, other_school
        FROM other_schools_considered
        WHERE student_id = %s
    """, (student_id,))
    other_school_data = cur.fetchone()

    cur.close()
    conn.close()

    selected_reasons = []
    other_reason = ""
    if enroll_reason:
        if enroll_reason[0]:
            selected_reasons = [r.strip() for r in enroll_reason[0].split(",")]
        other_reason = enroll_reason[1] or ""

    other_schools_selected = []
    other_school = ""
    if other_school_data:
        if other_school_data[0]:
            other_schools_selected = [r.strip() for r in other_school_data[0].split(",")]
        other_school = other_school_data[1] or ""

    return render_template(
        "admin/adminInventoryResult.html",
        admin_username=username,
        admin_campus=admin_campus,
        is_super_admin=is_super_admin,
        info=info,
        student_photo_base64=student_photo_base64,
        selected_reasons=selected_reasons,
        other_reason=other_reason,
        other_schools_selected=other_schools_selected,
        other_school=other_school
    )

@admin_bp.route('/download_admin_inventory_pdf/<int:student_id>')
def download_admin_inventory_pdf(student_id):
    if "admin_username" not in session:
        return redirect(url_for("admin.login"))

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cur.execute("""
        SELECT 
            s.id AS id,
            s.fullname, s.gender, s.email, s.campus, s.photo,
            sa.nickname, sa.present_address, sa.provincial_address,
            sa.date_of_birth, sa.place_of_birth, sa.age, sa.birth_order, sa.siblings_count,
            sa.civil_status, sa.religion, sa.nationality,
            sa.home_phone, sa.mobile_no, sa.email AS personal_email,
            sa.weight, sa.height, sa.blood_type, sa.hobbies, sa.talents,
            sa.emergency_name, sa.emergency_relationship, sa.emergency_address, sa.emergency_contact,
            sb.father_name, sb.father_age, sb.father_education, sb.father_occupation,
            sb.father_income, sb.father_contact, sb.mother_name, sb.mother_age, sb.mother_education,
            sb.mother_occupation, sb.mother_income, sb.mother_contact,
            sc.married_living_together, sc.living_not_married, sc.legally_separated,
            sc.mother_widow, sc.father_widower, sc.separated, sc.father_another_family, sc.mother_another_family,
            sd.elementary_school_name, sd.elementary_year_graduated, sd.elementary_awards,
            sd.junior_high_school_name, sd.junior_high_year_graduated, sd.junior_high_awards,
            sd.senior_high_school_name, sd.senior_high_year_graduated, sd.senior_high_awards,
            sd.senior_high_track, sd.senior_high_strand, sd.subject_interested, sd.org_membership,
            sd.study_finance, sd.course_personal_choice, sd.influenced_by,
            sd.feeling_about_course, sd.personal_choice,
            se.*, sf.*, sg.personal_description
        FROM student s
        LEFT JOIN personal_information sa ON sa.student_id = s.id
        LEFT JOIN family_background sb ON sb.student_id = s.id
        LEFT JOIN status_of_parent sc ON sc.student_id = s.id
        LEFT JOIN academic_information sd ON sd.student_id = s.id
        LEFT JOIN behavior_information se ON se.student_id = s.id
        LEFT JOIN psychological_consultations sf ON sf.student_id = s.id
        LEFT JOIN personal_descriptions sg ON sg.student_id = s.id
        WHERE s.id = %s
    """, (student_id,))

    info = cur.fetchone()
    if not info:
        return "Student Inventory results not found.", 404

    cur.execute("""
        SELECT reasons, other_reason
        FROM cpsu_enrollment_reason
        WHERE student_id = %s
    """, (student_id,))
    enroll_reason = cur.fetchone()

    cur.execute("""
        SELECT school_choices, other_school
        FROM other_schools_considered
        WHERE student_id = %s
    """, (student_id,))
    other_school_data = cur.fetchone()

    cur.close()
    conn.close()

    selected_reasons = enroll_reason[0].split(",") if enroll_reason and enroll_reason[0] else []
    other_reason = enroll_reason[1] if enroll_reason else ""

    other_schools_selected = other_school_data[0].split(",") if other_school_data and other_school_data[0] else []
    other_school = other_school_data[1] if other_school_data else ""

    cpsu_logo_base64 = image_to_base64("cpsulogo.png")

    html = render_template(
        "admin/adminInventoryResultPDF.html",
        admin_username=session["admin_username"],
        info=info,
        selected_reasons=selected_reasons,
        other_reason=other_reason,
        other_schools_selected=other_schools_selected,
        other_school=other_school,
        cpsu_logo_base64=cpsu_logo_base64
    )

    pdf_io = BytesIO()
    HTML(string=html, base_url=current_app.root_path).write_pdf(pdf_io)
    pdf_io.seek(0)

    filename = f"Inventory_{info['fullname'].replace(' ', '_')}.pdf"

    return send_file(
        pdf_io,
        mimetype="application/pdf",
        download_name=filename,
        as_attachment=True
    )

@admin_bp.route("/generateInterviewAI/<int:student_id>")
def generateInterviewAI(student_id):
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute(
            "SELECT questions FROM interview_questions WHERE student_id = %s",
            (student_id,)
        )
        existing = cur.fetchone()

        if existing:
            import json
            data = json.loads(existing[0])
            return jsonify(data)

        cur.execute("""
            SELECT 
                s.fullname,
                sa.preferred_program,
                sa.pair1, sa.pair2, sa.pair3, sa.pair4, sa.pair5,
                sa.pair6, sa.pair7, sa.pair8, sa.pair9, sa.pair10,
                sa.pair11, sa.pair12, sa.pair13, sa.pair14, sa.pair15,
                sa.pair16, sa.pair17, sa.pair18, sa.pair19, sa.pair20,
                sa.pair21, sa.pair22, sa.pair23, sa.pair24, sa.pair25,
                sa.pair26, sa.pair27, sa.pair28, sa.pair29, sa.pair30,
                sa.pair31, sa.pair32, sa.pair33, sa.pair34, sa.pair35,
                sa.pair36, sa.pair37, sa.pair38, sa.pair39, sa.pair40,
                sa.pair41, sa.pair42, sa.pair43, sa.pair44, sa.pair45,
                sa.pair46, sa.pair47, sa.pair48, sa.pair49, sa.pair50,
                sa.pair51, sa.pair52, sa.pair53, sa.pair54, sa.pair55,
                sa.pair56, sa.pair57, sa.pair58, sa.pair59, sa.pair60,
                sa.pair61, sa.pair62, sa.pair63, sa.pair64, sa.pair65,
                sa.pair66, sa.pair67, sa.pair68, sa.pair69, sa.pair70,
                sa.pair71, sa.pair72, sa.pair73, sa.pair74, sa.pair75,
                sa.pair76, sa.pair77, sa.pair78, sa.pair79, sa.pair80,
                sa.pair81, sa.pair82, sa.pair83, sa.pair84, sa.pair85,
                sa.pair86
            FROM student s
            LEFT JOIN student_survey_answer sa ON s.id = sa.student_id
            WHERE s.id = %s
        """, (student_id,))

        row = cur.fetchone()
        if not row:
            return jsonify({"error": "Student not found"}), 404

        fullname = row[0]
        preferred_program = row[1]
        letters = [l for l in row[2:] if l]

        if not letters:
            return jsonify({"error": "No survey answers"}), 400

        program_letters = []
        if preferred_program:
            cur.execute(
                "SELECT category_letter FROM program WHERE program_name = %s",
                (preferred_program,)
            )
            res = cur.fetchone()
            if res and res[0]:
                program_letters = [x.strip() for x in res[0].split(",")]

        counts = Counter(letters)
        top_three = [l for l, _ in counts.most_common(3)]

        top_three_descriptions = [
            short_letter_descriptions.get(l, "Unknown")
            for l in top_three
        ]

        all_letter_descriptions = [
            short_letter_descriptions.get(l, "Unknown")
            for l in letters
        ]

        program_descriptions = [
            short_letter_descriptions.get(l, "Unknown")
            for l in program_letters
        ]

        prompt = f"""
You are an educational guidance AI.

Student: {fullname}
Preferred Program: {preferred_program}

Program Category Letters: {program_letters}
Program Descriptions: {program_descriptions}

Student Top 3 Letters: {top_three}
Top 3 Descriptions: {top_three_descriptions}

All 86 Answers (Letters): {letters}
All 86 Answers (Descriptions): {all_letter_descriptions}

Use ONLY the descriptions from short_letter_descriptions.
Do NOT use Holland RIASEC.
Do NOT invent traits.

Explain alignment or mismatch by comparing:
- Program category letters + descriptions
- Student top 3 letters + descriptions

Use ONLY provided descriptions.
Return JSON ONLY.

{{
  "questions": ["q1","q2","q3","q4","q5","q6"],
  "mismatch_reason": "Explain clearly",
  "talking_points": ["p1","p2","p3"]
}}
"""

        # ---------- GROQ AI ----------
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": "Return ONLY valid JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=1000
        )

        raw = response.choices[0].message.content.strip()

        import re, json
        match = re.search(r"\{.*\}", raw, re.S)
        if not match:
            raise ValueError("Invalid JSON from AI")

        data = json.loads(match.group())

        # ---------- SAVE ----------
        cur.execute(
            "INSERT INTO interview_questions (student_id, questions) VALUES (%s, %s)",
            (student_id, json.dumps(data))
        )
        conn.commit()

        return jsonify(data)

    except Exception as e:
        conn.rollback()
        print("ERROR:", e)
        return jsonify({"error": "AI generation failed"}), 500

    finally:
        cur.close()
        conn.close()

PER_PAGE = 10

@admin_bp.route("/interviewList")
def interviewList():
    if "admin_username" not in session:
        return redirect(url_for("admin.login"))

    conn = get_db_connection()
    cur = conn.cursor()
    username = session["admin_username"]

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cur.execute("""
        SELECT id, campus
        FROM super_admin
        WHERE username = %s
    """, (username,))
    admin = cur.fetchone()

    is_super_admin = False
    admin_campus = None

    if admin:
        is_super_admin = True
        admin_id, admin_campus = admin
    else:
        cur.execute("""
            SELECT id, campus
            FROM admin
            WHERE username = %s
        """, (username,))
        admin = cur.fetchone()

        if not admin:
            cur.close()
            conn.close()
            return redirect(url_for("admin.login"))

        admin_id, admin_campus = admin

    selected_campus = request.args.get("campus", "")
    search_query = request.args.get("q", "")
    page = request.args.get("page", 1, type=int)

    cur.execute("""
        SELECT DISTINCT EXTRACT(YEAR FROM created_at)::int
        FROM student
        ORDER BY 1 DESC;
    """)
    available_years = [row[0] for row in cur.fetchall()]

    selected_year = request.args.get("year", type=int) or datetime.now().year

    query = """
        SELECT 
            s.id,
            s.exam_id,
            s.fullname,
            sa.preferred_program,
            sa.pair1, sa.pair2, sa.pair3, sa.pair4, sa.pair5,
            sa.pair6, sa.pair7, sa.pair8, sa.pair9, sa.pair10,
            sa.pair11, sa.pair12, sa.pair13, sa.pair14, sa.pair15,
            sa.pair16, sa.pair17, sa.pair18, sa.pair19, sa.pair20,
            sa.pair21, sa.pair22, sa.pair23, sa.pair24, sa.pair25,
            sa.pair26, sa.pair27, sa.pair28, sa.pair29, sa.pair30,
            sa.pair31, sa.pair32, sa.pair33, sa.pair34, sa.pair35,
            sa.pair36, sa.pair37, sa.pair38, sa.pair39, sa.pair40,
            sa.pair41, sa.pair42, sa.pair43, sa.pair44, sa.pair45,
            sa.pair46, sa.pair47, sa.pair48, sa.pair49, sa.pair50,
            sa.pair51, sa.pair52, sa.pair53, sa.pair54, sa.pair55,
            sa.pair56, sa.pair57, sa.pair58, sa.pair59, sa.pair60,
            sa.pair61, sa.pair62, sa.pair63, sa.pair64, sa.pair65,
            sa.pair66, sa.pair67, sa.pair68, sa.pair69, sa.pair70,
            sa.pair71, sa.pair72, sa.pair73, sa.pair74, sa.pair75,
            sa.pair76, sa.pair77, sa.pair78, sa.pair79, sa.pair80,
            sa.pair81, sa.pair82, sa.pair83, sa.pair84, sa.pair85,
            sa.pair86,
            sch.schedule_date,
            sch.start_time,
            sch.end_time,
            CASE 
                WHEN iq.student_id IS NOT NULL THEN TRUE 
                ELSE FALSE 
            END AS has_interview
        FROM student s
        LEFT JOIN student_survey_answer sa ON s.id = sa.student_id
        LEFT JOIN student_schedules ss ON s.id = ss.student_id
        LEFT JOIN schedules sch ON ss.schedule_id = sch.id
        LEFT JOIN interview_questions iq ON s.id = iq.student_id
        WHERE EXTRACT(YEAR FROM s.created_at) = %s
        AND (%s = '' OR s.fullname ILIKE %s OR s.exam_id ILIKE %s)
    """

    params = [
        selected_year,
        search_query,
        f"%{search_query}%",
        f"%{search_query}%"
    ]

    if is_super_admin:
        if selected_campus:
            query += " AND s.campus = %s"
            params.append(selected_campus)
    else:
        query += " AND s.campus = %s"
        params.append(admin_campus)

    query += " ORDER BY s.fullname ASC"

    cur.execute(query, tuple(params))
    raw_students = cur.fetchall()

    students = []

    for row in raw_students:
        student_id, exam_id, fullname, preferred_program, *rest = row
        pairs = rest[:-4]
        schedule_date, start_time, end_time, has_interview = rest[-4:]

        answers_clean = [p for p in pairs if p]
        top_letters = [l for l, _ in Counter(answers_clean).most_common(3)]

        program_letters = []
        if preferred_program:
            cur.execute(
                "SELECT category_letter FROM program WHERE program_name = %s",
                (preferred_program,)
            )
            res = cur.fetchone()
            if res:
                program_letters = res[0].split(",")

        if not preferred_program and not answers_clean:
            match_status = "Not Yet Answer"
        elif any(letter in program_letters for letter in top_letters):
            match_status = "Match"
        else:
            match_status = "Not Match"

        if match_status == "Not Match":
            schedule_str = (
                f"{schedule_date.strftime('%Y-%m-%d')} "
                f"({start_time.strftime('%I:%M %p')} - {end_time.strftime('%I:%M %p')})"
                if schedule_date else None
            )

            students.append((student_id, exam_id, fullname, schedule_str, has_interview))

    cur.close()
    conn.close()

    total_students = len(students)
    total_pages = max(1, ceil(total_students / PER_PAGE))
    start = (page - 1) * PER_PAGE
    end = start + PER_PAGE

    return render_template(
        "admin/interviewList.html",
        admin_username=username,
        admin_campus=admin_campus,
        selected_campus=selected_campus,
        available_years=available_years,
        year=selected_year,
        students=students[start:end],
        search_query=search_query,
        page=page,
        total_pages=total_pages,
        is_super_admin=is_super_admin
    )

@admin_bp.route("/save_schedule", methods=["POST"])
def save_schedule():
    if "admin_username" not in session:
        return redirect(url_for("admin.login"))

    data = request.get_json()
    schedule_date = data.get("date")
    start_time = data.get("start_time")
    end_time = data.get("end_time")
    slot_count = data.get("slot_count")

    admin_username = session["admin_username"]

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute(
            "SELECT campus FROM admin WHERE username = %s",
            (admin_username,)
        )
        admin_campus = cur.fetchone()[0]

        cur.execute(
            "SELECT 1 FROM schedules WHERE schedule_date = %s",
            (schedule_date,)
        )
        if cur.fetchone():
            return jsonify({
                "status": "error",
                "error": "A schedule already exists for this date."
            }), 400

        cur.execute("""
            INSERT INTO schedules
                (schedule_date, start_time, end_time, slot_count, admin_username)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            schedule_date,
            start_time,
            end_time,
            slot_count,
            admin_username
        ))

        cur.execute("""
            INSERT INTO admin_logs (admin_username, campus, action)
            VALUES (%s, %s, %s)
        """, (
            admin_username,
            admin_campus,
            f"Added new interview date '{schedule_date}'"
        ))

        conn.commit()

        return jsonify({
            "status": "success",
            "message": "Schedule saved successfully!"
        }), 200

    except psycopg2.Error as e:
        conn.rollback()
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500

    finally:
        cur.close()
        conn.close()

@admin_bp.route("/visualization")
def visualization():
    if "admin_username" not in session:
        return redirect(url_for("admin.login"))

    username = session["admin_username"]

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, campus
        FROM super_admin
        WHERE username = %s
    """, (username,))
    admin = cur.fetchone()

    is_super_admin = False
    admin_campus = None

    if admin:
        is_super_admin = True
        admin_id, admin_campus = admin
    else:
        cur.execute("""
            SELECT id, campus
            FROM admin
            WHERE username = %s
        """, (username,))
        admin = cur.fetchone()

        if not admin:
            cur.close()
            conn.close()
            return redirect(url_for("admin.login"))

        admin_id, admin_campus = admin

    selected_year = request.args.get("year", str(datetime.now().year))
    selected_gender = request.args.get("gender", "All")
    selected_campus = request.args.get("campus", "")

    available_campuses = []
    if is_super_admin:
        cur.execute("SELECT DISTINCT campus FROM student ORDER BY campus ASC;")
        available_campuses = [r[0] for r in cur.fetchall()]

    year_query = """
        SELECT DISTINCT EXTRACT(YEAR FROM created_at)::int
        FROM student
    """
    params = []

    if not is_super_admin:
        year_query += " WHERE campus = %s"
        params.append(admin_campus)
    elif selected_campus:
        year_query += " WHERE campus = %s"
        params.append(selected_campus)

    year_query += " ORDER BY 1 ASC"

    cur.execute(year_query, tuple(params))
    available_years = [row[0] for row in cur.fetchall()]

    cur.execute("SELECT id, program_name, color FROM program ORDER BY id ASC;")
    all_programs = cur.fetchall()

    def fetch_data_for_year(year=None, gender=None):
        filters = []
        params = []

        if is_super_admin:
            if selected_campus:
                filters.append("s.campus = %s")
                params.append(selected_campus)
        else:
            filters.append("s.campus = %s")
            params.append(admin_campus)

        if year:
            filters.append("EXTRACT(YEAR FROM s.created_at) = %s")
            params.append(year)

        if gender and gender != "All":
            filters.append("LOWER(s.gender) = LOWER(%s)")
            params.append(gender)

        where_clause = "WHERE " + " AND ".join(filters) if filters else ""

        cur.execute(f"""
            SELECT COALESCE(ssa.preferred_program, 'Unknown'), COUNT(*)
            FROM student_survey_answer ssa
            JOIN student s ON ssa.student_id = s.id
            {where_clause}
            GROUP BY COALESCE(ssa.preferred_program, 'Unknown')
            ORDER BY COUNT(*) DESC
        """, tuple(params))

        preferred = cur.fetchall()

        letter_cols = [f"pair{i}" for i in range(1, 87)]
        unions = [
            f"SELECT {c} AS letter FROM student_survey_answer ssa JOIN student s ON ssa.student_id = s.id "
            f"{where_clause} AND {c} BETWEEN 'A' AND 'R'"
            for c in letter_cols
        ]

        cur.execute(f"""
            SELECT letter, COUNT(*) FROM (
                {" UNION ALL ".join(unions)}
            ) t
            GROUP BY letter
            ORDER BY COUNT(*) DESC
            LIMIT 18
        """, tuple(params * len(letter_cols)))

        letters = cur.fetchall()

        return {
            "year": str(year) if year else "All",
            "gender": gender or "All",
            "preferred_labels": [r[0] for r in preferred],
            "preferred_counts": [r[1] for r in preferred],
            "top_labels": [r[0] for r in letters],
            "top_counts": [r[1] for r in letters]
        }

    if selected_year.lower() == "all":
        all_years_data = [fetch_data_for_year(y, selected_gender) for y in available_years]
    else:
        all_years_data = [fetch_data_for_year(int(selected_year), selected_gender)]

    cur.close()
    conn.close()

    return render_template(
        "admin/visualization.html",
        admin_username=username,
        admin_campus=admin_campus,
        is_super_admin=is_super_admin,
        available_campuses=available_campuses,
        selected_campus=selected_campus,
        available_years=available_years,
        year=selected_year,
        gender=selected_gender,
        all_years_data=all_years_data,
        all_programs=all_programs,
        letter_descriptions=letter_descriptions
    )

@admin_bp.route("/adminProfile", methods=["GET", "POST"])
def adminProfile():
    if "admin_username" not in session:
        return redirect(url_for("admin.login"))

    username = session["admin_username"]

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT fullname, username, email, campus
        FROM super_admin
        WHERE username = %s
    """, (username,))
    admin = cur.fetchone()

    is_super_admin = False
    table_name = "admin"

    if admin:
        is_super_admin = True
        table_name = "super_admin"
    else:
        cur.execute("""
            SELECT fullname, username, email, campus
            FROM admin
            WHERE username = %s
        """, (username,))
        admin = cur.fetchone()

        if not admin:
            cur.close()
            conn.close()
            return redirect(url_for("admin.login"))

    admin_fullname, admin_username, admin_email, admin_campus = admin

    if request.method == "POST":
        fullname = request.form.get("fullname")
        new_email = request.form.get("email")

        cur.execute(f"""
            UPDATE {table_name}
            SET fullname = %s
            WHERE username = %s
        """, (fullname, username))
        conn.commit()

        if new_email != admin_email:
            otp = generate_otp()

            session["email_change"] = {
                "otp": otp,
                "new_email": new_email,
                "username": username,
                "table": table_name,
                "time": time.time(),
                "attempts": 0
            }

            send_otp_email(new_email, otp)

            flash("Verification code sent to new email.", "info")
            cur.close()
            conn.close()
            return redirect(url_for("admin.verify_email_change"))

        flash("Profile updated successfully.", "success")

    cur.close()
    conn.close()

    return render_template(
        "admin/adminProfile.html",
        admin=admin,
        admin_username=username,
        admin_campus=admin_campus,
        is_super_admin=is_super_admin
    )

@admin_bp.route("/verify-email-change", methods=["GET", "POST"])
def verify_email_change():
    if "email_change" not in session:
        flash("No email change request found.", "error")
        return redirect(url_for("admin.adminProfile"))

    data = session["email_change"]

    if time.time() - data["time"] > 300:
        session.pop("email_change")
        flash("Verification expired.", "error")
        return redirect(url_for("admin.adminProfile"))

    if request.method == "POST":
        if request.form.get("action") == "back":
            session.pop("email_change")
            flash("Email change cancelled.", "info")
            return redirect(url_for("admin.adminProfile"))

        entered_otp = request.form.get("otp")

        data["attempts"] += 1
        session["email_change"] = data

        if data["attempts"] >= 5:
            session.pop("email_change")
            flash("Too many failed attempts. Email change cancelled.", "error")
            return redirect(url_for("admin.adminProfile"))

        if entered_otp != data["otp"]:
            flash(f"Invalid code. Attempts left: {5 - data['attempts']}", "error")
            return redirect(url_for("admin.verify_email_change"))

        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute(f"""
            UPDATE {data["table"]}
            SET email = %s
            WHERE username = %s
        """, (data["new_email"], data["username"]))

        conn.commit()
        cur.close()
        conn.close()

        session.pop("email_change")
        flash("Email updated successfully.", "success")
        return redirect(url_for("admin.adminProfile"))

    return render_template("admin/verify_email_change.html")

@admin_bp.route("/resend-email-otp")
def resend_email_otp():
    if "email_change" not in session:
        flash("No email change request found.", "error")
        return redirect(url_for("admin.adminProfile"))

    otp = generate_otp()

    session["email_change"]["otp"] = otp
    session["email_change"]["time"] = time.time()
    session["email_change"]["attempts"] = 0

    send_otp_email(session["email_change"]["new_email"], otp)

    flash("A new verification code has been sent.", "info")
    return redirect(url_for("admin.verify_email_change"))

@admin_bp.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("admin.login"))
