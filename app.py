from __future__ import annotations

import hmac
import os
import re
import secrets
from datetime import datetime, timezone
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from pymongo import ASCENDING, MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY") or secrets.token_hex(32),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "false").lower() == "true",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 12,
)

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://127.0.0.1:27017/").strip()
MONGODB_DATABASE = os.getenv("MONGODB_DATABASE", "astropulse").strip()
BATCH_NAME = os.getenv("BATCH_NAME", "AstroPulse Cohort").strip()
BATCH_INVITE_CODE = os.getenv("BATCH_INVITE_CODE", "").strip()
_mongo_client: MongoClient | None = None


def get_database():
    global _mongo_client
    if _mongo_client is None:
        _mongo_client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=4000)
    return _mongo_client[MONGODB_DATABASE]


def database_error_response():
    app.logger.exception("MongoDB request failed")
    return jsonify({"error": "AstroPulse could not connect to MongoDB. Check MONGODB_URI and make sure MongoDB is running."}), 503


def csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


app.jinja_env.globals["csrf_token"] = csrf_token


def csrf_valid():
    expected = session.get("_csrf_token", "")
    supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Please sign in to continue."}), 401
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def api_csrf_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not csrf_valid():
            return jsonify({"error": "Your session expired. Refresh the page and try again."}), 400
        return view(*args, **kwargs)

    return wrapped


def safe_user(user):
    return {
        "id": str(user["_id"]),
        "name": user["name"],
        "email": user["email"],
        "batch": user["batch"],
        "mission_phase": user["mission_phase"],
    }


def compute_alerts(logs):
    if not logs:
        return [{
            "title": "No health check recorded",
            "severity": "low",
            "region": "Global",
            "detail": "Add a health check to see personalized trend alerts."
        }]

    latest = logs[-1]
    alerts = []

    if latest["heart_rate"] >= 95 or latest["systolic_bp"] >= 125:
        alerts.append({
            "title": "Cardiovascular strain",
            "severity": "high",
            "region": "Cardiovascular",
            "detail": "A recorded value is above this prototype's review threshold. Follow mission medical procedures."
        })
    if latest["sleep_hours"] < 6:
        alerts.append({
            "title": "Sleep debt",
            "severity": "moderate",
            "region": "Neurological/Behavioral",
            "detail": "Recent sleep is below the demo target. Consider prioritizing the planned recovery routine."
        })
    recent_mood = [entry["mood_score"] for entry in logs[-3:]]
    if len(recent_mood) >= 3 and all(score <= 4 for score in recent_mood):
        alerts.append({
            "title": "Isolation check-in",
            "severity": "moderate",
            "region": "Neurological/Behavioral",
            "detail": "Three consecutive low mood scores suggest a crew check-in may be helpful."
        })
    cumulative_radiation = sum(entry["radiation_mSv"] for entry in logs)
    if cumulative_radiation >= 4.5:
        alerts.append({
            "title": "Radiation exposure",
            "severity": "high",
            "region": "Immune",
            "detail": "Recorded cumulative demo exposure is elevated. Review the mission radiation plan."
        })
    if latest["mood_score"] <= 3:
        alerts.append({
            "title": "Stress check-in",
            "severity": "moderate",
            "region": "Neurological/Behavioral",
            "detail": "The latest mood score is low. Consider a private crew-support check-in."
        })
    if not alerts:
        alerts.append({
            "title": "No demo thresholds triggered",
            "severity": "low",
            "region": "Global",
            "detail": "No configured prototype alerts were triggered by the latest health check."
        })
    return alerts


def calculate_hazards(user, logs):
    if not logs:
        return [
            {"id": key, "name": name, "score": 0, "status": "No data", "detail": detail}
            for key, name, detail in [
                ("radiation", "Radiation", "Add health checks with radiation readings to assess this indicator."),
                ("isolation", "Isolation", "Add mood and sleep readings to assess this indicator."),
                ("gravity", "Altered gravity", "Add health readings to assess this indicator."),
                ("hostile", "Hostile environment", "This prototype has no habitat sensors; enter a health check to assess wellness indicators."),
                ("distance", "Distance from Earth", "Mission phase is used as a simple illustrative context indicator."),
            ]
        ]

    latest = logs[-1]
    cumulative_radiation = sum(entry["radiation_mSv"] for entry in logs)
    recent = logs[-3:]
    average_mood = sum(item["mood_score"] for item in recent) / len(recent)
    average_sleep = sum(item["sleep_hours"] for item in recent) / len(recent)
    mission = user["mission_phase"].lower()
    distance_score = 60 if "mars" in mission or "lunar" in mission else 35

    def status(score):
        return "Elevated" if score >= 60 else "Moderate" if score >= 30 else "Low"

    isolation_score = min(100, max(
        0,
        round((6 - min(6, average_mood)) * 18 + max(0, 6 - average_sleep) * 12),
    ))
    gravity_score = min(100, max(
        0,
        round((max(0, latest["heart_rate"] - 72) * 2 + max(0, latest["systolic_bp"] - 118)) / 2),
    ))
    hostile_score = min(100, max(
        0,
        round(max(0, 6 - average_sleep) * 22 + max(0, 10 - latest["mood_score"]) * 8),
    ))
    entries = [
        ("radiation", "Radiation", min(100, round(cumulative_radiation / 6 * 100)),
         "Based on the sum of radiation values entered in your health logs; not a NASA exposure limit.",
         "Elevated" if cumulative_radiation >= 4.5 else "Moderate" if cumulative_radiation >= 2 else "Low"),
        ("isolation", "Isolation", isolation_score,
         "An illustrative score derived from your recent mood and sleep entries.",
         "Elevated" if len(recent) >= 3 and all(item["mood_score"] <= 4 for item in recent) else status(isolation_score)),
        ("gravity", "Altered gravity", gravity_score,
         "Illustrative cardiovascular trend indicator; not a diagnostic measurement of gravity effects.",
         "Elevated" if latest["heart_rate"] >= 90 or latest["systolic_bp"] >= 125 else "Moderate" if latest["heart_rate"] >= 80 else "Low"),
        ("hostile", "Hostile environment", hostile_score,
         "No habitat sensors are connected; this is a wellness proxy, not an environmental measurement.",
         "Elevated" if average_sleep < 6 or latest["mood_score"] <= 3 else "Moderate" if average_sleep < 7 else "Low"),
        ("distance", "Distance from Earth", distance_score,
         "Mission context indicator only; communication delay is not measured by this prototype.",
         status(distance_score)),
    ]
    return [
        {"id": key, "name": name, "score": score, "status": level, "detail": detail}
        for key, name, score, detail, level in entries
    ]


def get_summary(logs):
    if not logs:
        return {
            "heart_rate": 0, "systolic_bp": 0, "diastolic_bp": 0,
            "sleep_hours": 0, "radiation_mSv": 0, "mood_score": 0,
            "cumulative_radiation": 0,
        }
    latest = logs[-1]
    return {
        "heart_rate": latest["heart_rate"],
        "systolic_bp": latest["systolic_bp"],
        "diastolic_bp": latest["diastolic_bp"],
        "sleep_hours": round(latest["sleep_hours"], 1),
        "radiation_mSv": round(latest["radiation_mSv"], 2),
        "mood_score": latest["mood_score"],
        "cumulative_radiation": round(sum(item["radiation_mSv"] for item in logs), 2),
    }


def current_user():
    from bson import ObjectId

    user_id = session.get("user_id")
    if not user_id or not ObjectId.is_valid(user_id):
        session.clear()
        return None
    return get_database()["users"].find_one({"_id": ObjectId(user_id)})


def validate_health_payload(data):
    try:
        payload = {
            "heart_rate": int(data.get("heart_rate", "")),
            "systolic_bp": int(data.get("systolic_bp", "")),
            "diastolic_bp": int(data.get("diastolic_bp", "")),
            "sleep_hours": float(data.get("sleep_hours", "")),
            "radiation_mSv": float(data.get("radiation_mSv", "")),
            "mood_score": int(data.get("mood_score", "")),
            "notes": str(data.get("notes", "")).strip()[:1000],
        }
    except (TypeError, ValueError):
        return None, "Enter valid numbers for all health measurements."

    bounds = {
        "heart_rate": (40, 180),
        "systolic_bp": (60, 180),
        "diastolic_bp": (40, 120),
        "sleep_hours": (0, 12),
        "radiation_mSv": (0, 20),
        "mood_score": (1, 10),
    }
    for field, (minimum, maximum) in bounds.items():
        if not minimum <= payload[field] <= maximum:
            return None, f"{field.replace('_', ' ')} must be between {minimum} and {maximum}."
    if payload["diastolic_bp"] >= payload["systolic_bp"]:
        return None, "Diastolic blood pressure must be lower than systolic pressure."
    return payload, None


@app.get("/")
def index():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    try:
        user = current_user()
    except PyMongoError:
        return render_template("index.html", db_unavailable=True, user=None), 503
    if not user:
        return redirect(url_for("login"))
    return render_template("index.html", user=safe_user(user), db_unavailable=False)


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id") and request.method == "GET":
        return redirect(url_for("index"))
    if request.method == "POST":
        if not csrf_valid():
            flash("Your form expired. Please try again.", "error")
            return render_template("auth.html", mode="login"), 400
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        try:
            user = get_database()["users"].find_one({"email": email})
        except PyMongoError:
            flash("AstroPulse could not connect to MongoDB. Check your database setup.", "error")
            return render_template("auth.html", mode="login"), 503
        if not user or not check_password_hash(user["password_hash"], password):
            flash("Email or password is incorrect.", "error")
            return render_template("auth.html", mode="login"), 401
        session.clear()
        session["user_id"] = str(user["_id"])
        session.permanent = True
        csrf_token()
        return redirect(url_for("index"))
    return render_template("auth.html", mode="login")


@app.route("/register", methods=["GET", "POST"])
def register():
    if session.get("user_id") and request.method == "GET":
        return redirect(url_for("index"))
    if request.method == "POST":
        if not csrf_valid():
            flash("Your form expired. Please try again.", "error")
            return render_template("auth.html", mode="register"), 400
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        invite_code = request.form.get("invite_code", "").strip()
        mission_phase = request.form.get("mission_phase", "ISS").strip()
        if not BATCH_INVITE_CODE:
            flash("Registration is not configured yet. Ask your project organizer to set the batch invite code.", "error")
            return render_template("auth.html", mode="register"), 503
        if not hmac.compare_digest(invite_code, BATCH_INVITE_CODE):
            flash("That batch invite code is not valid.", "error")
            return render_template("auth.html", mode="register"), 403
        if not name or len(name) > 80 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            flash("Enter your name and a valid email address.", "error")
            return render_template("auth.html", mode="register"), 400
        if len(password) < 10:
            flash("Use a password with at least 10 characters.", "error")
            return render_template("auth.html", mode="register"), 400
        if mission_phase not in {"ISS", "Artemis Lunar Transit", "Lunar Surface", "Mars Transit"}:
            flash("Select a valid mission phase.", "error")
            return render_template("auth.html", mode="register"), 400

        try:
            db = get_database()
            db["users"].create_index([("email", ASCENDING)], unique=True)
            db["health_logs"].create_index([("user_id", ASCENDING), ("timestamp", ASCENDING)])
            db["mission_tasks"].create_index([("user_id", ASCENDING)])
            user = {
                "name": name,
                "email": email,
                "password_hash": generate_password_hash(password),
                "batch": BATCH_NAME or "AstroPulse Cohort",
                "mission_phase": mission_phase,
                "created_at": datetime.now(timezone.utc),
            }
            result = db["users"].insert_one(user)
            user_id = result.inserted_id
            db["mission_tasks"].insert_many([
                {"user_id": user_id, "task_name": "Radiation badge scan", "category": "Safety", "status": "pending", "reward_points": 40},
                {"user_id": user_id, "task_name": "Hydration check", "category": "Wellness", "status": "pending", "reward_points": 25},
                {"user_id": user_id, "task_name": "Resistance exercise", "category": "Fitness", "status": "pending", "reward_points": 60},
                {"user_id": user_id, "task_name": "Sleep recovery protocol", "category": "Behavioral", "status": "pending", "reward_points": 50},
            ])
        except DuplicateKeyError:
            flash("An account with that email already exists. Please sign in instead.", "error")
            return render_template("auth.html", mode="register"), 409
        except PyMongoError:
            app.logger.exception("MongoDB account registration failed")
            flash("We couldn't save your account. Check MongoDB setup and try again.", "error")
            return render_template("auth.html", mode="register"), 503

        session.clear()
        session["user_id"] = str(user_id)
        session.permanent = True
        csrf_token()
        return redirect(url_for("index"))
    return render_template("auth.html", mode="register")


@app.post("/logout")
@login_required
def logout():
    if not csrf_valid():
        return jsonify({"error": "Your session expired. Refresh the page and try again."}), 400
    session.clear()
    return redirect(url_for("login"))


@app.get("/api/dashboard")
@login_required
def dashboard():
    try:
        user = current_user()
        if not user:
            return jsonify({"error": "Please sign in again."}), 401
        user_id = user["_id"]
        db = get_database()
        logs = list(db["health_logs"].find({"user_id": user_id}).sort("timestamp", ASCENDING))
        tasks = list(db["mission_tasks"].find({"user_id": user_id}).sort("_id", ASCENDING))
        for entry in logs:
            entry["id"] = str(entry.pop("_id"))
            entry.pop("user_id", None)
            entry["timestamp"] = entry["timestamp"].isoformat()
        for task in tasks:
            task["id"] = str(task.pop("_id"))
            task.pop("user_id", None)
        return jsonify({
            "user": safe_user(user),
            "summary": get_summary(logs),
            "logs": logs,
            "tasks": tasks,
            "risk_alerts": compute_alerts(logs),
            "hazards": calculate_hazards(user, logs),
        })
    except PyMongoError:
        return database_error_response()


@app.post("/api/health")
@login_required
@api_csrf_required
def add_health_log():
    payload, error = validate_health_payload(request.get_json(silent=True) or {})
    if error:
        return jsonify({"error": error}), 400
    try:
        user = current_user()
        if not user:
            return jsonify({"error": "Please sign in again."}), 401
        payload["user_id"] = user["_id"]
        payload["timestamp"] = datetime.now(timezone.utc)
        get_database()["health_logs"].insert_one(payload)
        return jsonify({"status": "ok"}), 201
    except PyMongoError:
        return database_error_response()


@app.route("/api/tasks", methods=["GET", "POST"])
@login_required
def tasks():
    try:
        user = current_user()
        if not user:
            return jsonify({"error": "Please sign in again."}), 401
        collection = get_database()["mission_tasks"]
        if request.method == "POST":
            if not csrf_valid():
                return jsonify({"error": "Your session expired. Refresh the page and try again."}), 400
            data = request.get_json(silent=True) or {}
            task_name = str(data.get("task_name", "")).strip()
            category = str(data.get("category", "")).strip()
            try:
                reward_points = int(data.get("reward_points", 0))
            except (TypeError, ValueError):
                return jsonify({"error": "Reward points must be a whole number."}), 400
            if not task_name or len(task_name) > 120 or not category or len(category) > 40:
                return jsonify({"error": "Enter a task (up to 120 characters) and category (up to 40 characters)."}), 400
            if not 0 <= reward_points <= 200:
                return jsonify({"error": "Reward points must be between 0 and 200."}), 400
            collection.insert_one({
                "user_id": user["_id"],
                "task_name": task_name,
                "category": category,
                "status": "pending",
                "reward_points": reward_points,
            })
            return jsonify({"status": "ok"}), 201
        results = list(collection.find({"user_id": user["_id"]}).sort("_id", ASCENDING))
        return jsonify([
            {
                "id": str(item["_id"]),
                "task_name": item["task_name"],
                "category": item["category"],
                "status": item["status"],
                "reward_points": item["reward_points"],
            }
            for item in results
        ])
    except PyMongoError:
        return database_error_response()


@app.patch("/api/tasks/<task_id>")
@login_required
@api_csrf_required
def toggle_task(task_id):
    from bson import ObjectId

    data = request.get_json(silent=True) or {}
    status = data.get("status")
    if status not in {"pending", "completed"}:
        return jsonify({"error": "Invalid task status."}), 400
    if not ObjectId.is_valid(task_id):
        return jsonify({"error": "Task not found."}), 404
    try:
        user = current_user()
        if not user:
            return jsonify({"error": "Please sign in again."}), 401
        result = get_database()["mission_tasks"].update_one(
            {"_id": ObjectId(task_id), "user_id": user["_id"]},
            {"$set": {"status": status}},
        )
        if result.matched_count == 0:
            return jsonify({"error": "Task not found."}), 404
        return jsonify({"status": "updated"})
    except PyMongoError:
        return database_error_response()


if __name__ == "__main__":
    app.run(debug=False, host="127.0.0.1", port=int(os.getenv("PORT", "5001")))
