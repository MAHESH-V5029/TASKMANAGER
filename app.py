import os
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import (LoginManager, UserMixin, login_user, logout_user,
                         login_required, current_user)
from flask_socketio import SocketIO, join_room
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-change-me")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///tasks.db"

db = SQLAlchemy(app)
socketio = SocketIO(app, async_mode="threading")
login_manager = LoginManager(app)
login_manager.login_view = "login"

STATUSES = {"todo", "doing", "done"}


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(40), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)


class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    notes = db.Column(db.Text, default="")
    status = db.Column(db.String(10), default="todo")
    due = db.Column(db.String(10), default="")  # YYYY-MM-DD
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    def to_dict(self):
        return {"id": self.id, "title": self.title, "notes": self.notes,
                "status": self.status, "due": self.due}


@login_manager.user_loader
def load_user(uid):
    return db.session.get(User, int(uid))


# ---------- auth ----------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("username", "").strip().lower()
        pw = request.form.get("password", "")
        if len(name) < 3 or len(pw) < 6:
            flash("Use a username of 3+ characters and a password of 6+.")
        elif User.query.filter_by(username=name).first():
            flash("That username is taken. Try another.")
        else:
            user = User(username=name, password_hash=generate_password_hash(pw))
            db.session.add(user)
            db.session.commit()
            login_user(user)
            return redirect(url_for("index"))
    return render_template("auth.html", mode="register")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        name = request.form.get("username", "").strip().lower()
        user = User.query.filter_by(username=name).first()
        if user and check_password_hash(user.password_hash, request.form.get("password", "")):
            login_user(user)
            return redirect(url_for("index"))
        flash("Wrong username or password.")
    return render_template("auth.html", mode="login")


@app.route("/logout")
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    return render_template("index.html")


# ---------- task API (every query is scoped to the signed-in user) ----------
def notify():
    socketio.emit("tasks_changed", room=f"user-{current_user.id}")


def clean(data, task):
    title = (data.get("title", task.title) or "").strip()
    status = data.get("status", task.status)
    if not title:
        return "Title is required."
    if status not in STATUSES:
        return "Invalid status."
    task.title, task.status = title[:120], status
    task.notes = (data.get("notes", task.notes) or "").strip()
    task.due = (data.get("due", task.due) or "")[:10]


@app.get("/api/tasks")
@login_required
def list_tasks():
    tasks = Task.query.filter_by(user_id=current_user.id).order_by(Task.id.desc()).all()
    return jsonify([t.to_dict() for t in tasks])


@app.post("/api/tasks")
@login_required
def create_task():
    task = Task(user_id=current_user.id, title="x", status="todo", notes="", due="")
    err = clean(request.get_json() or {}, task)
    if err:
        return jsonify(error=err), 400
    db.session.add(task)
    db.session.commit()
    notify()
    return jsonify(task.to_dict()), 201


@app.put("/api/tasks/<int:tid>")
@login_required
def update_task(tid):
    task = Task.query.filter_by(id=tid, user_id=current_user.id).first_or_404()
    err = clean(request.get_json() or {}, task)
    if err:
        return jsonify(error=err), 400
    db.session.commit()
    notify()
    return jsonify(task.to_dict())


@app.delete("/api/tasks/<int:tid>")
@login_required
def delete_task(tid):
    task = Task.query.filter_by(id=tid, user_id=current_user.id).first_or_404()
    db.session.delete(task)
    db.session.commit()
    notify()
    return "", 204


# ---------- real time: each user joins a private room ----------
@socketio.on("connect")
def on_connect():
    if current_user.is_authenticated:
        join_room(f"user-{current_user.id}")


with app.app_context():
    db.create_all()

if __name__ == "__main__":
    # Port 5001 because macOS uses 5000 for AirPlay.
    socketio.run(app, host="127.0.0.1", port=5001, debug=True, allow_unsafe_werkzeug=True)
