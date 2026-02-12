from flask import Flask, session, redirect, url_for, request, flash
import os
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta

load_dotenv()

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../frontend"))

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static")
)
app.secret_key = os.getenv("SECRET_KEY")
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=10)

UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

@app.before_request
def check_session_timeout():
    if request.blueprint == "admin":

        if request.endpoint == "admin.login":
            return

        if "admin_username" not in session:
            return

        now = datetime.now(timezone.utc)
        last_activity = session.get("last_activity")

        if last_activity:
            idle_time = (now - last_activity).total_seconds()
            timeout = app.permanent_session_lifetime.total_seconds()

            if idle_time > timeout:
                flash("Session expired due to inactivity.", "session_expired")
                session.pop("admin_username", None)
                session.pop("last_activity", None)
                session.pop("admin_login_attempts", None)
                session.pop("admin_lock_until", None)
                return redirect(url_for("admin.login"))

        session["last_activity"] = now
        session.permanent = True

# Import Blueprints
from .admin.routes import admin_bp
from .student.routes import student_bp

# Blueprints
app.register_blueprint(admin_bp, url_prefix='/admin')
app.register_blueprint(student_bp, url_prefix='/student')

if __name__ == "__main__":
    app.run()
    #app.run(host="127.0.0.1", port=5002, debug=True)
    #python -m backend.app  