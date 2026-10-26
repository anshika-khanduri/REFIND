import os, sqlite3, re
from datetime import datetime
from difflib import SequenceMatcher
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "refind.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("REFIND_SECRET_KEY", "change-this-secret-key-before-deployment")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB per upload


def connect_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_db():
    with connect_db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            item_type TEXT NOT NULL CHECK(item_type IN ('lost','found')),
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            color TEXT DEFAULT '',
            description TEXT NOT NULL,
            location TEXT NOT NULL,
            event_date TEXT DEFAULT '',
            image_filename TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            item_id INTEGER,
            related_item_id INTEGER,
            message TEXT NOT NULL,
            score REAL NOT NULL DEFAULT 0,
            is_read INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(item_id) REFERENCES items(id),
            FOREIGN KEY(related_item_id) REFERENCES items(id)
        );
        CREATE TABLE IF NOT EXISTS match_pairs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lost_item_id INTEGER NOT NULL,
            found_item_id INTEGER NOT NULL,
            score REAL NOT NULL,
            UNIQUE(lost_item_id, found_item_id),
            FOREIGN KEY(lost_item_id) REFERENCES items(id),
            FOREIGN KEY(found_item_id) REFERENCES items(id)
        );
        """)


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def similarity(a, b):
    a, b = (a or "").strip().lower(), (b or "").strip().lower()
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def token_similarity(a, b):
    """A simple word-overlap score; useful when descriptions use different word order."""
    words_a = set(re.findall(r"[a-z0-9]+", (a or "").lower()))
    words_b = set(re.findall(r"[a-z0-9]+", (b or "").lower()))
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


def date_score(a, b):
    if not a or not b:
        return 0.35
    try:
        d1 = datetime.strptime(a, "%Y-%m-%d").date()
        d2 = datetime.strptime(b, "%Y-%m-%d").date()
        days = abs((d1 - d2).days)
        return max(0.0, 1.0 - days / 31.0)
    except ValueError:
        return 0.0


def image_color_similarity(filename_a, filename_b):
    """Optional lightweight image comparison based on average RGB color (not AI object recognition)."""
    if not filename_a or not filename_b:
        return None
    try:
        from PIL import Image, ImageStat
        paths = [os.path.join(UPLOAD_DIR, filename_a), os.path.join(UPLOAD_DIR, filename_b)]
        means = []
        for path in paths:
            with Image.open(path) as img:
                means.append(ImageStat.Stat(img.convert("RGB").resize((20, 20)).resize((1, 1)).getdata()).mean)
        distance = sum((x-y)**2 for x, y in zip(means[0], means[1])) ** 0.5
        return max(0.0, 1.0 - distance / 441.7)
    except Exception:
        return None


def calculate_match(lost, found):
    # Priority weights: category 25%, title 20%, description 20%, location 15%,
    # colour 10%, date 10%. If both photos exist, image similarity contributes 15%
    # and text/detail weights are scaled to 85%.
    category = similarity(lost["category"], found["category"])
    title = similarity(lost["title"], found["title"])
    description = max(similarity(lost["description"], found["description"]),
                      token_similarity(lost["description"], found["description"]))
    location = similarity(lost["location"], found["location"])
    color = similarity(lost["color"], found["color"]) if lost["color"] and found["color"] else 0.35
    date = date_score(lost["event_date"], found["event_date"])
    details = (category * .25 + title * .20 + description * .20 +
               location * .15 + color * .10 + date * .10)
    image_score = image_color_similarity(lost["image_filename"], found["image_filename"])
    if image_score is not None:
        score = (details * .85 + image_score * .15) * 100
    else:
        score = details * 100
    return round(score, 1)


def check_matches(item_id):
    """Create a match record and notify the lost-item owner and found-item reporter separately."""
    with connect_db() as con:
        current = con.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if not current:
            return
        opposite = "found" if current["item_type"] == "lost" else "lost"
        candidates = con.execute(
            "SELECT * FROM items WHERE item_type=? AND status='open' AND id != ?",
            (opposite, item_id)
        ).fetchall()
        for other in candidates:
            lost = current if current["item_type"] == "lost" else other
            found = current if current["item_type"] == "found" else other
            score = calculate_match(lost, found)
            # Keep all plausible candidates so users can compare ranked results.
            if score < 25:
                continue
            existing = con.execute(
                "SELECT id, score FROM match_pairs WHERE lost_item_id=? AND found_item_id=?",
                (lost["id"], found["id"])
            ).fetchone()
            if existing:
                con.execute("UPDATE match_pairs SET score=? WHERE id=?", (score, existing["id"]))
                continue
            con.execute(
                "INSERT INTO match_pairs(lost_item_id, found_item_id, score) VALUES(?,?,?)",
                (lost["id"], found["id"], score)
            )
            if score >= 99.9:
                msg_lost = f"Perfect match found ({score}%) for your lost item: {lost['title']}. Check your Matches page."
                msg_found = f"Your found item may belong to someone ({score}% match): {found['title']}. Check your Matches page."
            else:
                msg_lost = f"Possible match ({score}%) for your lost item: {lost['title']}. Review it on your Matches page."
                msg_found = f"Possible match ({score}%) for your found item: {found['title']}. Review it on your Matches page."
            now = datetime.now().isoformat(timespec="seconds")
            con.execute("INSERT INTO notifications(user_id,item_id,related_item_id,message,score,created_at) VALUES(?,?,?,?,?,?)",
                        (lost["user_id"], lost["id"], found["id"], msg_lost, score, now))
            con.execute("INSERT INTO notifications(user_id,item_id,related_item_id,message,score,created_at) VALUES(?,?,?,?,?,?)",
                        (found["user_id"], found["id"], lost["id"], msg_found, score, now))


@app.before_request
def setup():
    init_db()


@app.context_processor
def inject_notifications_count():
    if "user_id" not in session:
        return {"unread_count": 0}
    with connect_db() as con:
        count = con.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0",
                            (session["user_id"],)).fetchone()[0]
    return {"unread_count": count}


@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not name or not email or len(password) < 6:
            flash("Enter your name, a valid email, and a password of at least 6 characters.", "error")
            return render_template("register.html")
        try:
            with connect_db() as con:
                con.execute("INSERT INTO users(name,email,password_hash,created_at) VALUES(?,?,?,?)",
                            (name, email, generate_password_hash(password), datetime.now().isoformat(timespec="seconds")))
            flash("Account created. You can now log in.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("An account with this email already exists.", "error")
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        with connect_db() as con:
            user = con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["name"] = user["name"]
            return redirect(url_for("dashboard"))
        flash("Email or password is incorrect.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("index"))


def login_required():
    if "user_id" not in session:
        flash("Please log in first.", "error")
        return False
    return True


@app.route("/dashboard")
def dashboard():
    if not login_required():
        return redirect(url_for("login"))
    with connect_db() as con:
        items = con.execute("SELECT * FROM items WHERE user_id=? ORDER BY created_at DESC",
                            (session["user_id"],)).fetchall()
        notifications = con.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 5",
                                    (session["user_id"],)).fetchall()
        lost_count = con.execute("SELECT COUNT(*) FROM items WHERE user_id=? AND item_type='lost'",
                                 (session["user_id"],)).fetchone()[0]
        found_count = con.execute("SELECT COUNT(*) FROM items WHERE user_id=? AND item_type='found'",
                                  (session["user_id"],)).fetchone()[0]
    return render_template("dashboard.html", items=items, notifications=notifications,
                           lost_count=lost_count, found_count=found_count)


@app.route("/items/new/<item_type>", methods=["GET", "POST"])
def new_item(item_type):
    if not login_required():
        return redirect(url_for("login"))
    if item_type not in ("lost", "found"):
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        category = request.form.get("category", "").strip()
        color = request.form.get("color", "").strip()
        description = request.form.get("description", "").strip()
        location = request.form.get("location", "").strip()
        event_date = request.form.get("event_date", "").strip()
        photo = request.files.get("photo")
        if not all([title, category, description, location]):
            flash("Please complete the title, category, description, and location fields.", "error")
            return render_template("new_item.html", item_type=item_type)
        filename = ""
        if photo and photo.filename:
            if not allowed_file(photo.filename):
                flash("Photo must be PNG, JPG, JPEG, or WEBP.", "error")
                return render_template("new_item.html", item_type=item_type)
            filename = datetime.now().strftime("%Y%m%d%H%M%S%f") + "_" + secure_filename(photo.filename)
            photo.save(os.path.join(UPLOAD_DIR, filename))
        with connect_db() as con:
            cur = con.execute("""INSERT INTO items(user_id,item_type,title,category,color,description,location,event_date,image_filename,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (session["user_id"], item_type, title, category, color, description, location,
                 event_date, filename, datetime.now().isoformat(timespec="seconds")))
            item_id = cur.lastrowid
        check_matches(item_id)
        flash("Item saved. REFIND has checked it against the opposite list.", "success")
        return redirect(url_for("matches"))
    return render_template("new_item.html", item_type=item_type)


@app.route("/matches")
def matches():
    if not login_required():
        return redirect(url_for("login"))
    with connect_db() as con:
        rows = con.execute("""
            SELECT mp.score, mp.lost_item_id, mp.found_item_id,
                   l.title AS lost_title, l.category AS lost_category, l.location AS lost_location,
                   l.image_filename AS lost_image, l.user_id AS lost_user_id, lu.name AS lost_owner,
                   f.title AS found_title, f.category AS found_category, f.location AS found_location,
                   f.image_filename AS found_image, f.user_id AS found_user_id, fu.name AS finder
            FROM match_pairs mp
            JOIN items l ON l.id=mp.lost_item_id
            JOIN items f ON f.id=mp.found_item_id
            JOIN users lu ON lu.id=l.user_id
            JOIN users fu ON fu.id=f.user_id
            WHERE l.user_id=? OR f.user_id=?
            ORDER BY mp.score DESC
        """, (session["user_id"], session["user_id"])).fetchall()
    return render_template("matches.html", matches=rows)


@app.route("/notifications")
def notifications():
    if not login_required():
        return redirect(url_for("login"))
    with connect_db() as con:
        con.execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (session["user_id"],))
        rows = con.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC",
                           (session["user_id"],)).fetchall()
    return render_template("notifications.html", notifications=rows)


@app.route("/items/<int:item_id>/close", methods=["POST"])
def close_item(item_id):
    if not login_required():
        return redirect(url_for("login"))
    with connect_db() as con:
        con.execute("UPDATE items SET status='resolved' WHERE id=? AND user_id=?",
                    (item_id, session["user_id"]))
    flash("Item marked as resolved.", "success")
    return redirect(url_for("dashboard"))


@app.errorhandler(413)
def too_large(_error):
    flash("Photo is too large. Please use an image under 5 MB.", "error")
    return redirect(request.referrer or url_for("dashboard"))


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
