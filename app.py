"""
School Fee and Results Tracker - Kenya CBE (Grades 1-9)

Admin features: logins with roles, user management, audit log, automatic and
manual backups, restore, learner editing/deleting, class promotion, CSV import
and school settings.

Run:
    python -m streamlit run app.py

First login:  username  admin   password  admin123
(you are forced to change it straight away)
"""
import base64
import hashlib
import hmac
import html
import io
import os
import re
import shutil
import sqlite3
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

# ---------------------------------------------------------------
# Settings
# ---------------------------------------------------------------
# Data lives in your user folder, e.g. C:\Users\Admin\SchoolTracker
DATA_DIR = Path(os.environ.get("SCHOOL_TRACKER_DATA", Path.home() / "SchoolTracker"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
BACKUP_DIR = DATA_DIR / "backups"
DB_PATH = DATA_DIR / "school_tracker_cbe.db"
LOGO_PATH = DATA_DIR / "school_logo.png"
BG_PATH = DATA_DIR / "dashboard_bg.jpg"

# Carry over a database from the old project folder, if there is one
_OLD_DB = Path("school_tracker_cbe.db")
if _OLD_DB.exists() and not DB_PATH.exists():
    shutil.copy(_OLD_DB, DB_PATH)

DEFAULT_ADMIN_PASSWORD = "admin123"
# Change this whenever a PDF layout changes, so PDFs made by an older version are not offered again
PDF_VERSION = "2026-10-13-black-and-white"
ALL_GRADES = [f"Grade {n}" for n in range(1, 10)]
TERMS = ["Term 1", "Term 2", "Term 3"]
# Starting learning areas, following the Kenyan CBE levels. The admin can edit them in Admin > Learning areas.
# (name, short name, grades that take it, optional)
DEFAULT_AREAS = [
    ("English", "ENG", range(4, 10), 0),
    ("Kiswahili", "KISW", range(4, 10), 0),
    ("Mathematics", "MATH", range(4, 10), 0),
    ("Integrated Science", "INT SCI", range(7, 10), 0),
    ("Science and Technology", "SCI & TECH", range(4, 7), 0),
    ("Pre-Technical Studies", "PRE-TECH", range(7, 10), 0),
    ("Agriculture and Nutrition", "AGRI", range(4, 10), 0),
    ("Social Studies", "SOC STUD", range(4, 10), 0),
    ("Creative Arts and Sports", "CAS", range(7, 10), 0),
    ("Creative Arts", "CREATIVE ARTS", range(4, 7), 0),
    ("Physical and Health Education", "PHE", range(4, 7), 0),
    ("Religious Education", "CRE", range(4, 10), 0),
    ("Literacy Activities", "LIT", range(1, 4), 0),
    ("Kiswahili Language Activities", "KLA", range(1, 4), 0),
    ("Indigenous Language Activities", "ILA", range(1, 4), 1),
    ("Mathematical Activities", "MATH ACT", range(1, 4), 0),
    ("Environmental Activities", "ENV", range(1, 4), 0),
    ("Hygiene and Nutrition Activities", "H&N", range(1, 4), 0),
    ("Religious Education Activities", "RE ACT", range(1, 4), 0),
    ("Movement and Creative Activities", "MCA", range(1, 4), 0),
]
PAY_METHODS = ["M-Pesa", "Cash", "Bank", "Cheque"]
# Starting list of exam types (the admin can edit these in Admin > Exams)
DEFAULT_EXAMS = ["Back to School", "Midterm", "End of Term"]
ROLES = ["admin", "teacher", "bursar"]
ROLE_PAGES = {
    "admin": ["Dashboard", "Learners", "Results", "Analysis", "Fees", "Reports", "Admin"],
    "teacher": ["Dashboard", "Learners", "Results", "Analysis", "Reports"],
    "bursar": ["Dashboard", "Fees", "Reports"],
}

# Kenya CBE 8-point performance scale: (min, max, code, points, description)
CBE_SCALE = [
    (90, 100, "EE1", 8, "Exceptionally Exceeds Expectations"),
    (75, 89, "EE2", 7, "Very Good"),
    (58, 74, "ME1", 6, "Good"),
    (41, 57, "ME2", 5, "Fair"),
    (31, 40, "AE1", 4, "Needs Some Improvement"),
    (21, 30, "AE2", 3, "Needs Improvement"),
    (11, 20, "BE1", 2, "Below Expectations"),
    (0, 10, "BE2", 1, "Well Below Expectations"),
]


# Cell colours for the performance level on printed reports
# Printed PDFs are black and white (saves ink): no level colours
LEVEL_COLORS = {"EE": "#FFFFFF", "ME": "#FFFFFF", "AE": "#FFFFFF", "BE": "#FFFFFF"}


def get_level(marks):
    """Return (code, points, description) for a mark out of 100."""
    marks = round(marks)
    for low, high, code, points, desc in CBE_SCALE:
        if low <= marks <= high:
            return code, points, desc
    return "BE2", 1, "Well Below Expectations"


# ---------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------
def hash_password(password):
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return f"{salt.hex()}${digest.hex()}"


def check_password(password, stored):
    try:
        salt_hex, digest_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), 200_000
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except ValueError:
        return False


def password_problem(p1, p2):
    if len(p1) < 8:
        return "Password must be at least 8 characters."
    if p1 != p2:
        return "The two passwords do not match."
    if p1 == DEFAULT_ADMIN_PASSWORD:
        return "Choose a password different from the default one."
    return None


# ---------------------------------------------------------------
# Database
# ---------------------------------------------------------------
def get_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admission_no TEXT UNIQUE NOT NULL,
            full_name TEXT NOT NULL,
            class_name TEXT NOT NULL,
            parent_phone TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    # Learner names cannot repeat (case-insensitive)
    c.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_student_name "
        "ON students(full_name COLLATE NOCASE)"
    )
    # Exam types (editable by the admin)
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS exam_types (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            sort_order INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    if c.execute("SELECT COUNT(*) FROM exam_types").fetchone()[0] == 0:
        for order, name in enumerate(DEFAULT_EXAMS, start=1):
            c.execute("INSERT INTO exam_types (name, sort_order) VALUES (?, ?)", (name, order))

    # Older databases have no exam_type column, so the table is rebuilt once
    old_cols = [r["name"] for r in c.execute("PRAGMA table_info(results)")]
    if old_cols and "exam_type" not in old_cols:
        c.execute("ALTER TABLE results RENAME TO results_old")
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            subject TEXT NOT NULL,
            term TEXT NOT NULL,
            year INTEGER NOT NULL,
            exam_type TEXT NOT NULL,
            marks INTEGER NOT NULL CHECK (marks BETWEEN 0 AND 100),
            grade TEXT NOT NULL,
            FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
            UNIQUE (student_id, subject, term, year, exam_type)
        )
        """
    )
    if c.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'results_old'").fetchone():
        legacy = c.execute(
            "SELECT name FROM exam_types WHERE name = 'End of Term' COLLATE NOCASE"
        ).fetchone() or c.execute("SELECT name FROM exam_types ORDER BY sort_order, id").fetchone()
        c.execute(
            "INSERT OR IGNORE INTO results (id, student_id, subject, term, year, exam_type, marks, grade) "
            "SELECT id, student_id, subject, term, year, ?, marks, grade FROM results_old",
            (legacy["name"],),
        )
        c.execute("DROP TABLE results_old")
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS fee_structure (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_name TEXT NOT NULL,
            term TEXT NOT NULL,
            year INTEGER NOT NULL,
            amount REAL NOT NULL,
            UNIQUE (class_name, term, year)
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            term TEXT NOT NULL,
            year INTEGER NOT NULL,
            amount REAL NOT NULL CHECK (amount > 0),
            method TEXT,
            reference TEXT,
            paid_on TEXT NOT NULL,
            FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            full_name TEXT NOT NULL,
            role TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            must_change INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
        """
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)"
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS learning_areas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            short_name TEXT NOT NULL DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 0,
            optional INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS grade_areas (
            class_name TEXT NOT NULL,
            area_id INTEGER NOT NULL REFERENCES learning_areas(id) ON DELETE CASCADE,
            PRIMARY KEY (class_name, area_id)
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS subject_teachers (
            class_name TEXT NOT NULL,
            subject TEXT NOT NULL,
            teacher_name TEXT NOT NULL,
            PRIMARY KEY (class_name, subject)
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            action TEXT NOT NULL,
            details TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
        """
    )
    if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        c.execute(
            "INSERT INTO users (username, full_name, role, password_hash, must_change) "
            "VALUES (?, ?, ?, ?, 1)",
            ("admin", "Administrator", "admin", hash_password(DEFAULT_ADMIN_PASSWORD)),
        )
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('school_name', 'My School')")
    if c.execute("SELECT COUNT(*) FROM learning_areas").fetchone()[0] == 0:
        for order, (name, short, taught, optional) in enumerate(DEFAULT_AREAS, start=1):
            cur = c.execute(
                "INSERT INTO learning_areas (name, short_name, sort_order, optional) VALUES (?, ?, ?, ?)",
                (name, short, order, optional),
            )
            area_id = cur.lastrowid  # read it now: the next execute() on this cursor replaces it
            for n in taught:
                c.execute("INSERT INTO grade_areas (class_name, area_id) VALUES (?, ?)",
                          (f"Grade {n}", area_id))
    # Databases made by the Grade 1-12 version: drop grades above 9 and learning areas nobody uses
    marks = ",".join("?" * len(ALL_GRADES))
    c.execute(f"DELETE FROM grade_areas WHERE class_name NOT IN ({marks})", ALL_GRADES)
    c.execute("DELETE FROM learning_areas WHERE id NOT IN (SELECT area_id FROM grade_areas) "
              "AND name NOT IN (SELECT DISTINCT subject FROM results)")
    c.execute("DELETE FROM subject_teachers WHERE subject NOT IN (SELECT name FROM learning_areas)")
    # Marks recorded under a name that is not in the list (e.g. after restoring an old backup)
    for r in c.execute("SELECT DISTINCT subject FROM results "
                       "WHERE subject NOT IN (SELECT name FROM learning_areas)").fetchall():
        cur = c.execute(
            "INSERT INTO learning_areas (name, short_name, sort_order, optional) VALUES "
            "(?, ?, (SELECT COALESCE(MAX(sort_order), 0) + 1 FROM learning_areas), 0)",
            (r["subject"], r["subject"][:6].upper()),
        )
        area_id = cur.lastrowid
        for cl in c.execute(
            "SELECT DISTINCT s.class_name FROM students s JOIN results x ON x.student_id = s.id "
            "WHERE x.subject = ?", (r["subject"],)
        ).fetchall():
            if cl["class_name"] in ALL_GRADES:
                c.execute("INSERT OR IGNORE INTO grade_areas (class_name, area_id) VALUES (?, ?)",
                          (cl["class_name"], area_id))
    conn.commit()
    conn.close()


def query_df(sql, params=()):
    conn = get_connection()
    df = pd.read_sql_query(sql, conn, params=params)
    conn.close()
    return df


def run_sql(sql, params=()):
    """Run one write statement. Returns lastrowid."""
    conn = get_connection()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def run_many(statements):
    """Run several writes as one all-or-nothing transaction."""
    conn = get_connection()
    try:
        for sql, params in statements:
            conn.execute(sql, params)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_setting(key, default=""):
    df = query_df("SELECT value FROM settings WHERE key = ?", (key,))
    return df["value"][0] if not df.empty else default


def set_setting(key, value):
    run_sql(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def make_backup(label="manual"):
    BACKUP_DIR.mkdir(exist_ok=True)
    dest = BACKUP_DIR / f"{label}_{datetime.now():%Y%m%d_%H%M%S}.db"
    src = get_connection()
    dst = sqlite3.connect(dest)
    src.backup(dst)
    dst.close()
    src.close()
    return dest


def auto_backup():
    """One automatic backup per day; keeps the latest 14."""
    BACKUP_DIR.mkdir(exist_ok=True)
    if not any(BACKUP_DIR.glob(f"auto_{date.today():%Y%m%d}_*.db")):
        make_backup("auto")
    for old in sorted(BACKUP_DIR.glob("auto_*.db"))[:-14]:
        old.unlink()


# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------
def current_user():
    return st.session_state.get("user")


def is_admin():
    user = current_user()
    return bool(user and user["role"] == "admin")


def log_action(action, details=""):
    user = current_user()
    run_sql(
        "INSERT INTO audit_log (username, action, details) VALUES (?, ?, ?)",
        (user["username"] if user else "-", action, details),
    )


def clean_name(name):
    """Trim, collapse spaces, Title Case."""
    return " ".join(name.split()).title()


def valid_phone(phone):
    """Kenyan mobile: 07xx, 01xx, +2547xx, +2541xx."""
    return bool(re.fullmatch(r"(\+254|0)[17]\d{8}", phone.replace(" ", "")))


def flash(message, kind="success"):
    st.session_state["flash"] = (kind, message)


def show_flash():
    if "flash" in st.session_state:
        kind, message = st.session_state.pop("flash")
        getattr(st, kind)(message)


def show_df(df):
    st.dataframe(df, hide_index=True)


def default_year():
    try:
        year = int(get_setting("academic_year", date.today().year))
    except ValueError:
        year = date.today().year
    return max(2020, min(2100, year))


def school_contact_line():
    """'P.O. Box 91, Kaloleni  ·  Tel: 0712 345 678  ·  Email: info@school.ac.ke' (only what is set)."""
    box = get_setting("school_pobox", "").strip()
    if box and not re.match(r"(?i)p\.?\s?o\b", box):
        box = f"P.O. Box {box}"
    phone = get_setting("school_phone", "").strip()
    email = get_setting("school_email", "").strip()
    parts = [box, f"Tel: {phone}" if phone else "", f"Email: {email}" if email else ""]
    return "  ·  ".join(p for p in parts if p)


def contact_flowables():
    """The contact line as a centred PDF paragraph (empty list if nothing is set)."""
    line = school_contact_line()
    if not line:
        return []
    from xml.sax.saxutils import escape

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph

    style = ParagraphStyle("contact", fontName="Helvetica", fontSize=7.5, leading=10,
                           alignment=TA_CENTER, textColor=colors.black)
    return [Paragraph(escape(line), style)]


def grades():
    """Grades this school offers (Admin > Settings). All of Grade 1-9 until the admin narrows it."""
    saved = {g.strip() for g in get_setting("grades_offered", "").split(",")}
    return [g for g in ALL_GRADES if g in saved] or list(ALL_GRADES)


def class_sort_sql(column):
    """SQL expression that sorts classes Grade 1, 2 ... 12, then Completed."""
    return f"CASE WHEN {column} = 'Completed' THEN 99 ELSE CAST(substr({column}, 7) AS INTEGER) END"


def area_rows(class_name=None):
    """Learning areas in order, as dicts (name, short_name, optional).
    For a class: only the areas that grade takes (all areas if none are set)."""
    if class_name in ALL_GRADES:
        df = query_df(
            "SELECT a.name, a.short_name, a.optional FROM learning_areas a "
            "JOIN grade_areas g ON g.area_id = a.id WHERE g.class_name = ? "
            "ORDER BY a.sort_order, a.id",
            (class_name,),
        )
        if not df.empty:
            return df.to_dict("records")
    df = query_df("SELECT name, short_name, optional FROM learning_areas ORDER BY sort_order, id")
    return df.to_dict("records")


def all_areas():
    return [r["name"] for r in area_rows()]


def areas_for(class_name):
    return [r["name"] for r in area_rows(class_name)]


def exam_names():
    """Exam types in the order the admin set."""
    df = query_df("SELECT name FROM exam_types ORDER BY sort_order, id")
    return df["name"].tolist() or ["End of Term"]


def remember_pdf(key, signature, name, data):
    """Keep a generated PDF, tied to the options and app version that produced it."""
    st.session_state[key] = {"sig": (PDF_VERSION,) + tuple(signature), "name": name, "data": data}


def current_pdf(key, signature):
    """The stored PDF if it still matches the chosen options, otherwise None."""
    saved = st.session_state.get(key)
    if isinstance(saved, dict) and saved["sig"] == (PDF_VERSION,) + tuple(signature):
        return saved
    if saved:
        st.caption("The options changed since the last PDF was made. Click Generate again.")
    return None


def file_slug(*parts):
    """Safe file name pieces, e.g. file_slug('report', 'Grade 9', 'Midterm') -> report_Grade9_Midterm."""
    return re.sub(r"[^A-Za-z0-9_-]+", "", "_".join(str(p).replace(" ", "") for p in parts))


def learner_labels():
    df = query_df(
        "SELECT id, admission_no, full_name, class_name FROM students ORDER BY full_name"
    )
    labels = {
        int(r.id): f"{r.full_name} (Adm {r.admission_no} · {r.class_name})"
        for r in df.itertuples()
    }
    return df, labels


def kes(value):
    return f"KES {value:,.0f}"


# ---------------------------------------------------------------
# Printable report cards (PDF)
# ---------------------------------------------------------------
def report_thresholds():
    """Minimum marks for EE, ME and AE on printed reports (below AE = BE)."""

    def num(key, default):
        try:
            return int(get_setting(key, default))
        except ValueError:
            return default

    return num("level_ee", 75), num("level_me", 50), num("level_ae", 25)


def report_level(marks, thresholds):
    ee, me, ae = thresholds
    if marks >= ee:
        return "EE"
    if marks >= me:
        return "ME"
    if marks >= ae:
        return "AE"
    return "BE"


def report_subject_label(subject):
    if subject == "Religious Education":
        return get_setting("religion_label", "CRE").upper()
    return subject.replace(" and ", " & ").upper()


def class_totals(class_name, term, year, exam):
    """Total marks and class position for every learner in a class."""
    df = query_df(
        """
        SELECT s.id, SUM(r.marks) AS total
        FROM students s JOIN results r ON r.student_id = s.id
        WHERE s.class_name = ? AND r.term = ? AND r.year = ? AND r.exam_type = ?
        GROUP BY s.id
        """,
        (class_name, term, int(year), exam),
    )
    if not df.empty:
        df["position"] = df["total"].rank(method="min", ascending=False).astype(int)
    return df


def build_report_pdf(student_ids, term, year, exam, closes="", reopens=""):
    """One A4 page per learner, in the style of the school's end-of-term report."""
    from reportlab.graphics.shapes import Circle, Drawing, String
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import (
        HRFlowable,
        Image,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    NAVY = colors.black
    BLUE = colors.black
    LABEL_BG = colors.white
    TOTAL_BG = colors.white
    GRID = colors.black
    LINE = colors.black

    school = get_setting("school_name", "School")
    motto = get_setting("school_motto", "")
    thresholds = report_thresholds()
    term_no = term.split()[-1]

    def style(name, **kw):
        base = {"fontName": "Helvetica", "fontSize": 8, "leading": 10}
        base.update(kw)
        return ParagraphStyle(name, **base)

    s_school = style("school", fontName="Helvetica-Bold", fontSize=15, leading=18,
                     alignment=TA_CENTER, textColor=NAVY)
    s_motto = style("motto", fontName="Helvetica-Oblique", fontSize=7.5,
                    alignment=TA_CENTER, textColor=BLUE)
    s_title = style("title", fontName="Helvetica-Bold", fontSize=11, leading=14,
                    alignment=TA_CENTER, textColor=NAVY)
    s_sub = style("sub", fontName="Helvetica-Bold", fontSize=9, leading=12,
                  alignment=TA_CENTER, textColor=BLUE)
    s_key = style("key", fontSize=6.5, textColor=BLUE)
    s_small = style("small", fontSize=8)
    s_note = style("note", fontSize=6.5, alignment=TA_CENTER, textColor=colors.black)

    def logo():
        if LOGO_PATH.exists():
            try:
                # print the logo in greyscale, whatever colours the uploaded file has
                from PIL import Image as PILImage

                with PILImage.open(LOGO_PATH) as im:
                    gray = im.convert("LA") if "A" in im.getbands() else im.convert("L")
                    if gray.mode == "LA":  # put transparent areas on white
                        flat = PILImage.new("L", gray.size, 255)
                        flat.paste(gray.getchannel("L"), mask=gray.getchannel("A"))
                        gray = flat
                    buf = io.BytesIO()
                    gray.save(buf, format="PNG")
                    buf.seek(0)
                    w, h = gray.size
                img = Image(buf, width=62 * w / h, height=62)
                img.hAlign = "CENTER"
                return img
            except Exception:
                pass
        d = Drawing(62, 62)
        d.add(Circle(31, 31, 29, strokeColor=NAVY, strokeWidth=2,
                     fillColor=colors.white))
        d.add(Circle(31, 31, 24, strokeColor=BLUE, strokeWidth=0.8, fillColor=None))
        initials = "".join(w[0] for w in school.split()[:2]).upper() or "S"
        d.add(String(31, 27, initials, textAnchor="middle",
                     fontName="Helvetica-Bold", fontSize=16, fillColor=NAVY))
        d.hAlign = "CENTER"
        return d

    def ruled(n):
        t = Table([[""]] * n, colWidths=[515], rowHeights=[15] * n)
        t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.6, LINE)]))
        return t

    def sign_row(left, right):
        t = Table([[Paragraph(left, s_small), Paragraph(right, s_small)]],
                  colWidths=[257, 258])
        t.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, -1), 2),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
        return t

    blank = "_" * 22

    def chrome(canvas, doc):
        width, height = A4
        canvas.saveState()
        canvas.setStrokeColor(colors.black)
        canvas.setLineWidth(0.8)
        canvas.line(40, height - 24, width - 40, height - 24)
        canvas.line(40, 24, width - 40, 24)
        canvas.setFillColor(colors.black)
        canvas.setFont("Helvetica-Bold", 7)
        canvas.drawString(40, height - 16, school.upper())
        canvas.drawRightString(width - 40, height - 16, motto.upper())
        canvas.drawCentredString(width / 2, 9, f"Page {doc.page}")
        canvas.restoreState()

    story = []
    for n, sid in enumerate(student_ids):
        stu = query_df("SELECT * FROM students WHERE id = ?", (int(sid),)).iloc[0]
        res = query_df(
            "SELECT subject, marks FROM results "
            "WHERE student_id = ? AND term = ? AND year = ? AND exam_type = ?",
            (int(sid), term, int(year), exam),
        )
        marks_by = dict(zip(res["subject"], res["marks"]))

        totals = class_totals(stu["class_name"], term, year, exam)
        mine = totals[totals["id"] == int(sid)]
        position = int(mine["position"].iloc[0]) if not mine.empty else "-"
        size = len(totals) if not mine.empty else "-"

        # Header
        story += [logo(), Spacer(1, 6), Paragraph(school.upper(), s_school)]
        if motto:
            story.append(Paragraph(f"MOTTO: {motto.upper()}", s_motto))
        story += contact_flowables()
        story += [
            Spacer(1, 4),
            HRFlowable(width="100%", thickness=1, color=NAVY),
            Spacer(1, 4),
            Paragraph(f"{exam.upper()} ASSESSMENT REPORT", s_title),
            Paragraph(f"{stu['class_name'].upper()} — {term.upper()}, {int(year)}", s_sub),
            Spacer(1, 6),
        ]

        # Learner details
        info = Table(
            [
                ["LEARNER'S NAME:", stu["full_name"].upper(), "ASSESSMENT NO:", stu["admission_no"]],
                ["TERM:", term_no, "YEAR:", str(int(year))],
                ["CLASS POSITION:", str(position), "CLASS SIZE:", str(size)],
            ],
            colWidths=[105, 150, 100, 160],
            rowHeights=[16] * 3,
        )
        info.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
            ("BACKGROUND", (0, 0), (-1, -1), LABEL_BG),
            ("GRID", (0, 0), (-1, -1), 0.5, GRID),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story += [info, Spacer(1, 8)]

        # Marks table: the grade's learning areas (optional ones only if the learner has marks)
        assigned = area_rows(stu["class_name"])
        card_subjects = [a["name"] for a in assigned if not a["optional"] or a["name"] in marks_by]
        card_subjects += [x for x in all_areas() if x in marks_by and x not in card_subjects]
        rows = [["LEARNING AREA", "PERFORMANCE\nLEVEL", "MARKS", "TEACHER'S\nSIGNATURE"]]
        cell_styles = []
        recorded = []
        for i, subject in enumerate(card_subjects, start=1):
            m = marks_by.get(subject)
            level = ""
            if m is not None:
                m = int(m)
                recorded.append(m)
                level = report_level(m, thresholds)
                cell_styles.append(("BACKGROUND", (1, i), (1, i),
                                    colors.HexColor(LEVEL_COLORS[level])))
            rows.append([report_subject_label(subject), level, "" if m is None else str(m), ""])
        total = sum(recorded)
        average = f"{total / len(recorded):.2f}" if recorded else ""
        rows.append(["TOTAL MARKS", "", str(total) if recorded else "", ""])
        rows.append(["AVERAGE", "", average, ""])

        table = Table(rows, colWidths=[200, 105, 85, 125],
                      rowHeights=[28] + [18] * (len(rows) - 1))
        table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
            ("LINEBELOW", (0, 0), (-1, 0), 1.5, colors.black),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (0, 0), (-1, 0), "CENTER"),
            ("ALIGN", (1, 1), (2, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.5, GRID),
            ("LINEABOVE", (0, -2), (-1, -2), 1.2, colors.black),
            ("FONTNAME", (0, -2), (-1, -1), "Helvetica-Bold"),
        ] + cell_styles))
        story += [
            table,
            Spacer(1, 4),
            Paragraph(
                "<b>Performance Levels Key:</b> EE = Exceeding Expectations &nbsp;&nbsp; "
                "ME = Meeting Expectations &nbsp;&nbsp; AE = Approaching Expectations "
                "&nbsp;&nbsp; BE = Below Expectations",
                s_key,
            ),
            Spacer(1, 10),
        ]

        # Remarks and signatures
        story += [
            Paragraph("<b>Class Teacher's Remarks:</b>", s_small), ruled(2), Spacer(1, 8),
            Paragraph("<b>Head Teacher's Remarks:</b>", s_small), ruled(2), Spacer(1, 6),
            sign_row(f"<b>Sign:</b> {blank}", f"<b>Date:</b> {blank}"),
            sign_row(
                f"<b>School Closes on:</b> {closes or blank}",
                f"<b>Re-Opens on:</b> {reopens or blank}",
            ),
            Spacer(1, 4),
            Paragraph("<b>Parents/Guardian's Remarks:</b>", s_small), ruled(2), Spacer(1, 6),
            sign_row(f"<b>Sign:</b> {blank}", f"<b>Date:</b> {blank}"),
            HRFlowable(width="100%", thickness=0.8, color=NAVY),
            Spacer(1, 3),
            Paragraph(
                "This is a computer-generated assessment report. "
                "Please verify details with the school administration.",
                s_note,
            ),
        ]
        if n < len(student_ids) - 1:
            story.append(PageBreak())

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=40, rightMargin=40,
                            topMargin=40, bottomMargin=36,
                            title=f"{school} - {exam} {term} {int(year)} reports")
    doc.build(story, onFirstPage=chrome, onLaterPages=chrome)
    return buf.getvalue()


# ---------------------------------------------------------------
# Login
# ---------------------------------------------------------------
def login_screen():
    st.title(f"🏫 {get_setting('school_name', 'School Tracker')}")
    st.caption("Please sign in")

    first = query_df("SELECT must_change FROM users WHERE username = 'admin'")
    if not first.empty and int(first["must_change"][0]) == 1:
        st.info("First time? Sign in with username **admin** and password **admin123**. "
                "You will be asked to choose a new password.")

    if st.session_state.get("fails", 0) >= 5:
        st.error("Too many failed attempts. Close this page and open it again.")
        return

    with st.form("login"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        ok = st.form_submit_button("Sign in", type="primary")

    if ok:
        df = query_df(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username.strip(),)
        )
        if (
            not df.empty
            and int(df.iloc[0]["active"]) == 1
            and check_password(password, df.iloc[0]["password_hash"])
        ):
            row = df.iloc[0]
            st.session_state["user"] = {
                "id": int(row["id"]),
                "username": row["username"],
                "full_name": row["full_name"],
                "role": row["role"],
                "must_change": int(row["must_change"]),
            }
            st.session_state["fails"] = 0
            log_action("Signed in")
            st.rerun()
        else:
            st.session_state["fails"] = st.session_state.get("fails", 0) + 1
            st.error("Wrong username or password, or the account is disabled.")


def force_password_change_screen():
    user = current_user()
    st.title("🔐 Choose a new password")
    st.info("For security, set your own password before continuing.")
    with st.form("force_pw"):
        p1 = st.text_input("New password (at least 8 characters)", type="password")
        p2 = st.text_input("Confirm new password", type="password")
        ok = st.form_submit_button("Save password", type="primary")
    if ok:
        problem = password_problem(p1, p2)
        if problem:
            st.error(problem)
        else:
            run_sql(
                "UPDATE users SET password_hash = ?, must_change = 0 WHERE id = ?",
                (hash_password(p1), user["id"]),
            )
            user["must_change"] = 0
            log_action("Changed own password")
            st.rerun()


def sidebar_account():
    user = current_user()
    st.sidebar.markdown(f"👤 **{user['full_name']}**  \n`{user['role']}`")
    with st.sidebar.expander("Change my password"):
        with st.form("my_pw", clear_on_submit=True):
            old = st.text_input("Current password", type="password")
            p1 = st.text_input("New password", type="password")
            p2 = st.text_input("Confirm new password", type="password")
            ok = st.form_submit_button("Change")
        if ok:
            row = query_df("SELECT password_hash FROM users WHERE id = ?", (user["id"],))
            problem = password_problem(p1, p2)
            if not check_password(old, row["password_hash"][0]):
                st.error("Current password is wrong.")
            elif problem:
                st.error(problem)
            else:
                run_sql(
                    "UPDATE users SET password_hash = ? WHERE id = ?",
                    (hash_password(p1), user["id"]),
                )
                log_action("Changed own password")
                st.success("Password changed.")
    if st.sidebar.button("Sign out"):
        log_action("Signed out")
        st.session_state.clear()
        st.rerun()


# ---------------------------------------------------------------
# Class lists
# ---------------------------------------------------------------
LIST_LAYOUTS = {
    "subjects": "One page per class, a column for each learning area (with subject teachers)",
    "per_subject": "One sheet per learning area (headed by its subject teacher)",
    "simple": "Simple list with blank columns (class teacher only)",
}


def class_teacher(class_name):
    return get_setting(f"class_teacher_{class_name}", "").strip()


def subject_teachers(class_name):
    """{learning area: teacher name} for one class."""
    df = query_df(
        "SELECT subject, teacher_name FROM subject_teachers WHERE class_name = ?",
        (class_name,),
    )
    return dict(zip(df["subject"], df["teacher_name"]))


def class_list_frame(class_names, sort_by="Name"):
    """Learners of the chosen classes, numbered, with each class's teacher."""
    if not class_names:
        return pd.DataFrame()
    marks = ",".join("?" * len(class_names))
    df = query_df(
        "SELECT admission_no, full_name, class_name, parent_phone "
        f"FROM students WHERE class_name IN ({marks})",
        tuple(class_names),
    )
    if df.empty:
        return df
    df["class_order"] = df["class_name"].map({c: i for i, c in enumerate(ALL_GRADES)})
    df["adm_sort"] = pd.to_numeric(df["admission_no"], errors="coerce")
    keys = ["class_order", "full_name"] if sort_by == "Name" else ["class_order", "adm_sort", "admission_no"]
    df = df.sort_values(keys).reset_index(drop=True)
    df["No"] = df.groupby("class_name").cumcount() + 1
    df["Class Teacher"] = df["class_name"].map(class_teacher)
    df["parent_phone"] = df["parent_phone"].fillna("")
    return df


def teacher_columns(df, subjects):
    """Add a '<LEARNING AREA> Teacher' column for each chosen learning area."""
    df = df.copy()
    by_class = {c: subject_teachers(c) for c in df["class_name"].unique()}
    for s in subjects:
        df[f"{report_subject_label(s)} Teacher"] = df["class_name"].map(
            lambda c, s=s: by_class[c].get(s, "")
        )
    return df


def build_class_list_pdf(class_names, year, sort_by="Name", layout="subjects",
                         subjects=None, include_phone=True, blank_cols=4, teacher_names=None,
                         marks_column=True):
    """Class lists as a PDF. See LIST_LAYOUTS for the three layouts."""
    from xml.sax.saxutils import escape

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import (
        HRFlowable,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    df = class_list_frame(class_names, sort_by)
    if df.empty:
        raise ValueError("No learners in that selection.")
    chosen = subjects or all_areas()
    subjects = [s for s in all_areas() if s in chosen]

    NAVY = colors.black
    BLUE = colors.black
    LABEL_BG = colors.white
    GRID = colors.black
    ZEBRA = colors.white
    school = get_setting("school_name", "School")
    motto = get_setting("school_motto", "")
    page = landscape(A4) if layout == "subjects" else A4
    W = int(page[0] - 80)

    def style(name, **kw):
        base = {"fontName": "Helvetica", "fontSize": 8, "leading": 10}
        base.update(kw)
        return ParagraphStyle(name, **base)

    s_school = style("school", fontName="Helvetica-Bold", fontSize=15, leading=18,
                     alignment=TA_CENTER, textColor=NAVY)
    s_title = style("title", fontName="Helvetica-Bold", fontSize=11, leading=14,
                    alignment=TA_CENTER, textColor=NAVY)
    s_sub = style("sub", fontName="Helvetica-Bold", fontSize=9, leading=12,
                  alignment=TA_CENTER, textColor=BLUE)
    s_head = style("head", fontSize=6.5, leading=8, alignment=TA_CENTER, textColor=colors.black)
    s_note = style("note", fontSize=6.5, alignment=TA_CENTER, textColor=colors.black)
    s_cell = style("cell", fontSize=9, leading=10.5)

    def chrome(canvas, doc):
        width, height = page
        canvas.saveState()
        canvas.setStrokeColor(colors.black)
        canvas.setLineWidth(0.8)
        canvas.line(40, height - 24, width - 40, height - 24)
        canvas.line(40, 24, width - 40, 24)
        canvas.setFillColor(colors.black)
        canvas.setFont("Helvetica-Bold", 7)
        canvas.drawString(40, height - 16, school.upper())
        canvas.drawRightString(width - 40, height - 16, motto.upper())
        canvas.drawCentredString(width / 2, 9, f"Page {doc.page}")
        canvas.restoreState()

    def heading(sub):
        return [
            Paragraph(school.upper(), s_school),
            *contact_flowables(),
            Paragraph("CLASS LIST", s_title),
            Paragraph(sub, s_sub),
            Spacer(1, 4),
            HRFlowable(width="100%", thickness=1, color=NAVY),
            Spacer(1, 6),
        ]

    def info(pairs, widths):
        row = []
        for label, value in pairs:
            row += [label, Paragraph(escape(str(value)), s_cell)]
        t = Table([row], colWidths=widths, rowHeights=[26])
        bold = [("FONTNAME", (i, 0), (i, 0), "Helvetica-Bold") for i in range(0, len(row), 2)]
        t.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BACKGROUND", (0, 0), (-1, -1), LABEL_BG),
            ("GRID", (0, 0), (-1, -1), 0.5, GRID),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ] + bold))
        return t

    def grid(rows, widths, header_h):
        t = Table(rows, colWidths=widths, rowHeights=[header_h] + [17] * (len(rows) - 1),
                  repeatRows=1)
        t.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
            ("LINEBELOW", (0, 0), (-1, 0), 1.5, colors.black),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("ALIGN", (2, 1), (2, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.5, GRID),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ZEBRA]),
        ]))
        return t

    def list_header():
        return (["NO", "ADM NO", "NAME"] + (["MARKS"] if marks_column else [])
                + (["PARENT PHONE"] if include_phone else []) + [""] * blank_cols)

    def list_row(r):
        return ([str(r.No), r.admission_no, r.full_name] + ([""] if marks_column else [])
                + ([r.parent_phone] if include_phone else []) + [""] * blank_cols)

    def list_widths():
        widths = ([28, 55, 190] + ([60] if marks_column else [])
                  + ([90] if include_phone else []))
        if blank_cols:
            blank_w = max((W - sum(widths)) / blank_cols, 18)
            over = sum(widths) + blank_w * blank_cols - W
            if over > 0:
                widths[2] = max(120, widths[2] - over)
            widths += [blank_w] * blank_cols
        else:
            widths[2] += W - sum(widths)
        return widths

    note = Paragraph(f"Generated {datetime.now():%d %b %Y %H:%M}. Computer-generated class list.", s_note)
    classes = [c for c in class_names if c in set(df["class_name"])]
    pages = []

    for cls in classes:
        part = df[df["class_name"] == cls]
        teacher = class_teacher(cls)
        by_subject = {**subject_teachers(cls), **(teacher_names or {})}
        count = str(len(part))
        taught = set(areas_for(cls))
        cls_subjects = [x for x in subjects if x in taught]
        if layout != "simple" and not cls_subjects:
            continue

        if layout == "subjects":
            sw = min(90, (W - 72 - 130) / max(len(cls_subjects), 1))
            name_w = min(220, W - 72 - sw * len(cls_subjects))
            widths = [26, 46, name_w] + [sw] * len(cls_subjects)
            header = ["NO", "ADM NO", "NAME"] + [
                Paragraph(
                    f"<b>{escape(subject_short(s))}</b><br/>{escape(by_subject.get(s, '')) or '&nbsp;'}",
                    s_head,
                )
                for s in cls_subjects
            ]
            rows = [header] + [
                [str(r.No), r.admission_no, r.full_name] + [""] * len(cls_subjects)
                for r in part.itertuples()
            ]
            pages.append(
                heading(f"{cls.upper()} · {int(year)}")
                + [info([("CLASS TEACHER:", teacher), ("LEARNERS:", count)],
                        [100, W - 100 - 80 - 80, 80, 80]),
                   Spacer(1, 8), grid(rows, widths, 40), Spacer(1, 6), note]
            )

        elif layout == "per_subject":
            rows = [list_header()] + [list_row(r) for r in part.itertuples()]
            for s in cls_subjects:
                rem = W - 95 - 110 - 70 - 40
                pages.append(
                    heading(f"{cls.upper()} · {report_subject_label(s)} · {int(year)}")
                    + [info(
                        [("LEARNING AREA:", report_subject_label(s)),
                         ("SUBJECT TEACHER:", by_subject.get(s, "")),
                         ("LEARNERS:", count)],
                        [95, rem * 0.45, 110, rem * 0.55, 70, 40]),
                       Spacer(1, 8), grid(rows, list_widths(), 20), Spacer(1, 6), note]
                )

        else:  # simple
            rows = [list_header()] + [list_row(r) for r in part.itertuples()]
            pages.append(
                heading(f"{cls.upper()} · {int(year)}")
                + [info([("CLASS TEACHER:", teacher), ("LEARNERS:", count)],
                        [100, W - 100 - 80 - 90, 80, 90]),
                   Spacer(1, 8), grid(rows, list_widths(), 20), Spacer(1, 6), note]
            )

    if not pages:
        raise ValueError("None of the chosen classes takes that learning area.")

    story = []
    for i, flowables in enumerate(pages):
        if i:
            story.append(PageBreak())
        story += flowables

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=page, leftMargin=40, rightMargin=40,
                            topMargin=40, bottomMargin=36, title=f"{school} - class lists")
    doc.build(story, onFirstPage=chrome, onLaterPages=chrome)
    return buf.getvalue()


# ---------------------------------------------------------------
# Pages
# ---------------------------------------------------------------
def save_dashboard_background(data):
    """Shrink and store the uploaded photo (phone photos are huge)."""
    from PIL import Image as PILImage
    from PIL import ImageOps

    with PILImage.open(io.BytesIO(data)) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((1920, 1080))
        im.save(BG_PATH, format="JPEG", quality=78, optimize=True)


@st.cache_data(show_spinner=False)
def _background_uri(stamp):
    return "data:image/jpeg;base64," + base64.b64encode(BG_PATH.read_bytes()).decode("ascii")


def apply_dashboard_background():
    """Show the chosen photo behind the dashboard. Returns the text colour, or None if no photo is set."""
    if not BG_PATH.exists():
        return None
    info = BG_PATH.stat()
    uri = _background_uri((info.st_mtime_ns, info.st_size))
    light = get_setting("dashboard_tone", "dark") == "light"
    try:
        strength = max(0, min(90, int(get_setting("dashboard_overlay", "60")))) / 100
    except ValueError:
        strength = 0.6
    rgb, ink = ("255,255,255", "#111111") if light else ("0,0,0", "#FFFFFF")
    st.markdown(
        f"""<style>
        .stApp {{
            background-image: linear-gradient(rgba({rgb},{strength}), rgba({rgb},{strength})),
                              url("{uri}");
            background-size: cover; background-position: center; background-attachment: fixed;
        }}
        [data-testid="stHeader"] {{ background: transparent; }}
        .stApp :is(h1, h2, h3, h4, p, label, li, span, small,
                   [data-testid="stMetricValue"], [data-testid="stMetricLabel"]):not([data-testid="stSidebar"] *) {{
            color: {ink} !important;
        }}
        </style>""",
        unsafe_allow_html=True,
    )
    return ink


def html_class_bars(frame, ink):
    """Simple bars that stay readable on top of a photo."""
    top = max(int(frame["Learners"].max()), 1)
    rows = "".join(
        f'<div style="display:flex;align-items:center;margin:6px 0;color:{ink}">'
        f'<div style="width:95px">{html.escape(str(r.Class))}</div>'
        f'<div style="flex:1"><div style="height:16px;width:{max(2, int(100 * r.Learners / top))}%;'
        f'background:{ink};opacity:.85;border-radius:3px"></div></div>'
        f'<div style="margin-left:8px;min-width:28px">{int(r.Learners)}</div></div>'
        for r in frame.itertuples()
    )
    st.markdown(f"<div>{rows}</div>", unsafe_allow_html=True)


def page_dashboard():
    ink = apply_dashboard_background()
    st.header("📊 Dashboard")
    learners = query_df("SELECT COUNT(*) AS n FROM students")["n"][0]
    results = query_df("SELECT COUNT(*) AS n FROM results")["n"][0]
    paid = query_df("SELECT COALESCE(SUM(amount), 0) AS t FROM payments")["t"][0]

    c1, c2, c3 = st.columns(3)
    c1.metric("Learners", int(learners))
    c2.metric("Results recorded", int(results))
    c3.metric("Fees collected", kes(paid))

    st.subheader("Learners per class")
    per_class = query_df(
        "SELECT class_name AS Class, COUNT(*) AS Learners FROM students GROUP BY class_name"
    )
    if per_class.empty:
        st.info("No learners yet. Add learners first.")
    else:
        order = [c for c in ALL_GRADES + ["Completed"] if c in set(per_class["Class"])]
        chart = per_class.set_index("Class").reindex(order)
        if ink:
            html_class_bars(chart.reset_index(), ink)
        else:
            st.bar_chart(chart)


def page_learners():
    st.header("👩🏽‍🎓 Learners")
    show_flash()
    can_add = current_user()["role"] in ("admin", "teacher")
    tab_list, tab_add, tab_import, tab_lists = st.tabs(
        ["All learners", "Add learner", "Import from Excel", "Class lists"]
    )

    with tab_add:
        if not can_add:
            st.info("You do not have permission to add learners.")
        else:
            with st.form("add_learner", clear_on_submit=True):
                adm = st.text_input("Admission number")
                name = st.text_input("Full name")
                class_name = st.selectbox("Class", grades())
                phone = st.text_input("Parent phone (optional, e.g. 0712345678)")
                submitted = st.form_submit_button("Save learner", type="primary")

            if submitted:
                adm, name, phone = adm.strip(), clean_name(name), phone.strip()
                if not adm or not name:
                    st.error("Admission number and full name are required.")
                elif phone and not valid_phone(phone):
                    st.error("Enter a valid Kenyan phone number, e.g. 0712345678.")
                else:
                    try:
                        run_sql(
                            "INSERT INTO students (admission_no, full_name, class_name, parent_phone) "
                            "VALUES (?, ?, ?, ?)",
                            (adm, name, class_name, phone or None),
                        )
                        log_action("Added learner", f"{name} (Adm {adm}, {class_name})")
                        flash(f"{name} added to {class_name}.")
                        st.rerun()
                    except sqlite3.IntegrityError:
                        exists = query_df(
                            "SELECT 1 FROM students WHERE full_name = ? COLLATE NOCASE", (name,)
                        )
                        if not exists.empty:
                            st.error(f"A learner named '{name}' already exists. Names must be unique.")
                        else:
                            st.error(f"Admission number {adm} is already in use.")

    with tab_list:
        df = query_df(
            "SELECT admission_no AS 'Adm No', full_name AS Name, class_name AS Class, "
            "parent_phone AS 'Parent Phone' FROM students ORDER BY "
            + class_sort_sql("class_name") + ", full_name"
        )
        if df.empty:
            st.info("No learners yet.")
        else:
            chosen = st.selectbox("Filter by class", ["All"] + ALL_GRADES + ["Completed"])
            if chosen != "All":
                df = df[df["Class"] == chosen]
            show_df(df)

    with tab_import:
        if can_add:
            import_learners_panel("lrn")
        else:
            st.info("You do not have permission to add learners.")

    with tab_lists:
        st.write("Make a class list with the subject teacher's name on it. "
                 "Choose the class and the learning area.")
        c1, c2 = st.columns(2)
        cls_choice = c1.selectbox("Class", ["All classes"] + grades(), index=1, key="cl_class")
        pool = areas_for(cls_choice) if cls_choice != "All classes" else all_areas()
        subj_choice = c2.selectbox("Learning area", ["All learning areas"] + pool,
                                   index=1, key="cl_subject")
        classes = grades() if cls_choice == "All classes" else [cls_choice]
        single = subj_choice != "All learning areas"
        subjects = [subj_choice] if single else pool

        layout = "per_subject"
        if not single:
            layout = st.radio("Layout", list(LIST_LAYOUTS), format_func=LIST_LAYOUTS.get, key="cl_layout")

        # With one class and one learning area the teacher's name can be typed right here
        teacher_names, teacher_name, stored, remember = None, "", "", False
        if single and cls_choice != "All classes":
            stored = subject_teachers(cls_choice).get(subj_choice, "")
            teacher_name = " ".join(st.text_input(
                f"{subj_choice} teacher for {cls_choice}", stored,
                key=f"cl_teacher_{cls_choice}_{subj_choice}").split())
            teacher_names = {subj_choice: teacher_name}
            if is_admin() and teacher_name != stored:
                remember = st.checkbox("Remember this name for next time",
                                       key=f"cl_remember_{cls_choice}_{subj_choice}")

        c3, c4 = st.columns(2)
        sort_by = c3.selectbox("Sort learners by", ["Name", "Admission number"], key="cl_sort")
        yr = int(c4.number_input("Year", 2020, 2100, default_year(), step=1, key="cl_year"))
        marks_col, phone, blanks = False, False, 0
        if layout != "subjects":
            marks_col = st.checkbox("Marks column (right after the name)", value=True,
                                    key="cl_marks")
            with st.expander("More columns (optional)"):
                phone = st.checkbox("Parent phone", value=False, key="cl_phone")
                blanks = int(st.number_input("Extra blank columns", 0, 10, 0, step=1,
                                             key="cl_blank"))

        frame = class_list_frame(classes, sort_by)
        if frame.empty:
            st.info("No learners in that selection yet.")
        else:
            present = [c for c in classes if c in set(frame["class_name"])]
            missing = []
            for c in present:
                if layout in ("subjects", "simple") and not class_teacher(c):
                    missing.append(f"{c}: class teacher")
                if layout != "simple":
                    have = subject_teachers(c)
                    if teacher_names is not None:
                        have = {**have, **teacher_names}
                    names = [report_subject_label(s) for s in subjects if not have.get(s)]
                    if names:
                        missing.append(f"{c}: {', '.join(names)}")
            if missing:
                st.caption("No teacher set yet for – " + " · ".join(missing)
                           + ". Type the name above, or an admin can add them in Admin → Teachers.")

            out = frame if layout == "simple" else teacher_columns(frame, subjects)
            if teacher_names is not None:
                out = out.copy()
                out[f"{report_subject_label(subj_choice)} Teacher"] = teacher_name
            cols = {"No": "No", "admission_no": "Adm No", "full_name": "Name",
                    "class_name": "Class", "Class Teacher": "Class Teacher"}
            if layout != "subjects" and phone:
                cols["parent_phone"] = "Parent Phone"
            teacher_cols = [c for c in out.columns if c.endswith(" Teacher") and c != "Class Teacher"]
            out = out[list(cols) + teacher_cols].rename(columns=cols)
            if marks_col:
                out.insert(list(out.columns).index("Name") + 1, "Marks", "")
            show_df(out)

            label = file_slug(cls_choice if cls_choice != "All classes" else "AllClasses",
                              subj_choice if single else "AllSubjects")
            csv_button("Download class list (CSV)", out, file_slug("class_list", label, yr) + ".csv")

            cl_sig = (tuple(classes), yr, sort_by, layout, tuple(subjects), phone, blanks,
                      marks_col, tuple(sorted((teacher_names or {}).items())))
            if st.button("Generate PDF", type="primary", key="cl_go"):
                if remember:
                    if teacher_name:
                        run_sql(
                            "INSERT INTO subject_teachers (class_name, subject, teacher_name) "
                            "VALUES (?, ?, ?) ON CONFLICT(class_name, subject) "
                            "DO UPDATE SET teacher_name = excluded.teacher_name",
                            (cls_choice, subj_choice, teacher_name),
                        )
                    else:
                        run_sql("DELETE FROM subject_teachers WHERE class_name = ? AND subject = ?",
                                (cls_choice, subj_choice))
                    log_action("Set teacher", f"{cls_choice} | {subj_choice} | {teacher_name or '(none)'}")
                try:
                    pdf = build_class_list_pdf(classes, yr, sort_by, layout, subjects,
                                               phone, blanks, teacher_names, marks_col)
                except ImportError:
                    st.error("The PDF library is missing. In the terminal run: "
                             "python -m pip install reportlab")
                else:
                    remember_pdf("class_list_pdf", cl_sig, file_slug("class_list", label, yr) + ".pdf", pdf)
                    log_action("Generated class list", f"{label} {yr} ({layout})")
            saved = current_pdf("class_list_pdf", cl_sig)
            if saved:
                st.success("Class list PDF ready.")
                st.download_button("Download class list (PDF)", saved["data"], file_name=saved["name"],
                                   mime="application/pdf")


def page_results():
    st.header("📝 Results (CBE)")
    show_flash()
    students, labels = learner_labels()
    exams = exam_names()
    tab_add, tab_view, tab_scale = st.tabs(["Add result", "View results", "CBE scale"])

    with tab_add:
        if students.empty:
            st.info("Add learners first.")
        else:
            mode = st.radio(
                "Enter results for",
                ["A whole class (list of learners)", "One learner"],
                horizontal=True,
                key="res_mode",
            )

            if mode.startswith("A whole class"):
                c1, c2 = st.columns(2)
                cls = c1.selectbox("Class", grades(), key="ent_class")
                subject = c2.selectbox("Learning area", areas_for(cls), key="ent_subject")
                c3, c4, c5 = st.columns(3)
                exam = c3.selectbox("Exam", exams, key="ent_exam")
                term = c4.selectbox("Term", TERMS, key="ent_term")
                year = int(c5.number_input("Year", 2020, 2100, default_year(), step=1, key="ent_year"))

                roster = query_df(
                    """
                    SELECT s.id, s.admission_no, s.full_name, r.marks AS saved
                    FROM students s
                    LEFT JOIN results r
                           ON r.student_id = s.id AND r.subject = ? AND r.term = ?
                          AND r.year = ? AND r.exam_type = ?
                    WHERE s.class_name = ?
                    ORDER BY s.full_name
                    """,
                    (subject, term, year, exam, cls),
                )
                if roster.empty:
                    st.info(f"There are no learners in {cls} yet.")
                else:
                    pending = roster[roster["saved"].isna()].reset_index(drop=True)
                    done = roster[roster["saved"].notna()].reset_index(drop=True)
                    st.caption(
                        f"{len(roster)} learners in {cls} · {len(done)} already have "
                        f"{subject} marks for {exam}, {term} {year}."
                    )

                    if pending.empty:
                        st.success("Every learner in this class already has a mark for this selection.")
                    else:
                        grid = pd.DataFrame(
                            {
                                "No": range(1, len(pending) + 1),
                                "Adm No": pending["admission_no"],
                                "Name": pending["full_name"],
                                "Marks": pd.Series([None] * len(pending), dtype="float"),
                            }
                        )
                        version = st.session_state.get("ent_ver", 0)
                        edited = st.data_editor(
                            grid,
                            hide_index=True,
                            disabled=["No", "Adm No", "Name"],
                            column_config={
                                "Marks": st.column_config.NumberColumn(
                                    "Marks (0-100)", min_value=0, max_value=100, step=1
                                )
                            },
                            key=f"entry_{cls}_{subject}_{exam}_{term}_{year}_{len(pending)}_{version}",
                        )
                        entered = edited[edited["Marks"].notna()]
                        st.caption(
                            f"{len(entered)} of {len(pending)} marks entered. "
                            "Leave a mark blank to skip that learner (for example if absent)."
                        )

                        if st.button("Save marks", type="primary", key="ent_save"):
                            bad = [
                                r["Name"]
                                for _, r in entered.iterrows()
                                if not (0 <= r["Marks"] <= 100) or r["Marks"] != int(r["Marks"])
                            ]
                            if entered.empty:
                                st.warning("Enter at least one mark first.")
                            elif bad:
                                st.error("Marks must be whole numbers from 0 to 100. Check: " + ", ".join(bad))
                            else:
                                statements = []
                                for idx, r in entered.iterrows():
                                    m = int(r["Marks"])
                                    statements.append(
                                        (
                                            "INSERT INTO results (student_id, subject, term, year, exam_type, marks, grade) "
                                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                                            (int(pending.loc[idx, "id"]), subject, term, year, exam, m, get_level(m)[0]),
                                        )
                                    )
                                try:
                                    run_many(statements)
                                except sqlite3.IntegrityError:
                                    st.error("Some of these marks were saved by someone else meanwhile. "
                                             "Reload the list and try again.")
                                else:
                                    log_action(
                                        "Added results (class list)",
                                        f"{cls} | {subject} | {exam} {term} {year}: {len(statements)} mark(s)",
                                    )
                                    st.session_state["ent_ver"] = version + 1
                                    flash(f"{len(statements)} {subject} mark(s) saved for {cls}.")
                                    st.rerun()

                    if not done.empty:
                        with st.expander(f"Already recorded ({len(done)})"):
                            shown = pd.DataFrame(
                                {
                                    "Adm No": done["admission_no"],
                                    "Name": done["full_name"],
                                    "Marks": done["saved"].astype(int),
                                    "Level": done["saved"].apply(lambda m: get_level(m)[0]),
                                }
                            )
                            show_df(shown)
                            st.caption("To correct a saved mark, ask an admin to delete it under View results, then enter it again.")

            else:
                sid = st.selectbox("Learner", list(labels), format_func=labels.get)
                c1, c2 = st.columns(2)
                subject = c1.selectbox(
                    "Learning area", areas_for(students.set_index("id").loc[sid, "class_name"]))
                exam = c2.selectbox("Exam", exams)
                c3, c4 = st.columns(2)
                term = c3.selectbox("Term", TERMS)
                year = c4.number_input("Year", 2020, 2100, default_year(), step=1)
                marks = st.number_input("Marks (0-100)", 0, 100, 0, step=1)

                code, points, desc = get_level(marks)
                st.info(f"Performance level: **{code}** – {desc} ({points} points)")

                if st.button("Save result", type="primary"):
                    try:
                        run_sql(
                            "INSERT INTO results (student_id, subject, term, year, exam_type, marks, grade) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (sid, subject, term, int(year), exam, int(marks), code),
                        )
                        log_action(
                            "Added result",
                            f"{labels[sid].split(' (')[0]} | {subject} | {exam} {term} {int(year)} = {int(marks)} ({code})",
                        )
                        flash(f"Result saved! Performance Level: {code}")
                        st.rerun()
                    except sqlite3.IntegrityError:
                        st.error(
                            f"{subject} for {exam}, {term} {int(year)} is already recorded for this learner. "
                            "Ask an admin to delete the old entry if it needs correcting."
                        )

    with tab_view:
        df = query_df(
            """
            SELECT r.id, s.admission_no, s.full_name, s.class_name,
                   r.subject, r.exam_type, r.term, r.year, r.marks, r.grade
            FROM results r JOIN students s ON s.id = r.student_id
            ORDER BY s.full_name, r.year, r.term, r.exam_type, r.subject
            """
        )
        if df.empty:
            st.info("No results saved yet.")
        else:
            f1, f2 = st.columns(2)
            cls = f1.selectbox("Class", ["All"] + grades(), key="rf_class")
            exm = f2.selectbox("Exam", ["All"] + exams, key="rf_exam")
            f3, f4 = st.columns(2)
            trm = f3.selectbox("Term", ["All"] + TERMS, key="rf_term")
            yrs = sorted(df["year"].unique().tolist(), reverse=True)
            yr = f4.selectbox("Year", ["All"] + yrs, key="rf_year")
            if cls != "All":
                df = df[df["class_name"] == cls]
            if exm != "All":
                df = df[df["exam_type"] == exm]
            if trm != "All":
                df = df[df["term"] == trm]
            if yr != "All":
                df = df[df["year"] == yr]

            if df.empty:
                st.warning("No results match those filters.")
            else:
                # Each learner's name is shown once
                for (adm, name, cl), group in df.groupby(
                    ["admission_no", "full_name", "class_name"], sort=False
                ):
                    st.markdown(f"**{name}** · Adm {adm} · {cl}")
                    show_df(group[["id", "subject", "exam_type", "term", "year", "marks", "grade"]])

                if is_admin():
                    st.divider()
                    st.subheader("🗑️ Delete result (admin)")
                    options = {
                        f"{row.full_name} | ID {row.id} – {row.subject}, {row.exam_type}, "
                        f"{row.term} {row.year} ({row.marks}, {row.grade})": int(row.id)
                        for row in df.itertuples()
                    }
                    pick = st.selectbox("Select result to delete", list(options))
                    confirm = st.checkbox("I confirm I want to delete this result")
                    if st.button("Delete result", type="primary"):
                        if not confirm:
                            st.warning("Tick the confirmation box first.")
                        else:
                            run_sql("DELETE FROM results WHERE id = ?", (options[pick],))
                            log_action("Deleted result", pick)
                            flash("Result deleted.")
                            st.rerun()

    with tab_scale:
        scale = pd.DataFrame(
            [
                {"Level": c, "Marks": f"{lo}-{hi}", "Points": p, "Meaning": d}
                for lo, hi, c, p, d in CBE_SCALE
            ]
        )
        show_df(scale)


def page_fees():
    st.header("💰 Fees")
    show_flash()
    students, labels = learner_labels()
    tab_struct, tab_pay, tab_bal, tab_hist = st.tabs(
        ["Fee structure", "Record payment", "Balances", "Payment history"]
    )

    with tab_struct:
        with st.form("fee_struct"):
            c1, c2, c3 = st.columns(3)
            cls = c1.selectbox("Class", grades())
            trm = c2.selectbox("Term", TERMS)
            yr = c3.number_input("Year", 2020, 2100, default_year(), step=1)
            amount = st.number_input("Fee per learner (KES)", 0, 1_000_000, 0, step=500)
            ok = st.form_submit_button("Save fee amount", type="primary")
        if ok:
            if amount <= 0:
                st.error("Enter an amount greater than 0.")
            else:
                run_sql(
                    "INSERT INTO fee_structure (class_name, term, year, amount) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(class_name, term, year) DO UPDATE SET amount = excluded.amount",
                    (cls, trm, int(yr), float(amount)),
                )
                log_action("Set fee", f"{cls} {trm} {int(yr)} = {kes(amount)}")
                flash(f"{cls} {trm} {int(yr)} fee set to {kes(amount)}.")
                st.rerun()

        fs = query_df(
            "SELECT class_name AS Class, term AS Term, year AS Year, amount AS 'Amount (KES)' "
            "FROM fee_structure ORDER BY year DESC, term, " + class_sort_sql("class_name")
        )
        if not fs.empty:
            show_df(fs)

    with tab_pay:
        if students.empty:
            st.info("Add learners first.")
        else:
            with st.form("payment", clear_on_submit=True):
                sid = st.selectbox("Learner", list(labels), format_func=labels.get)
                c1, c2 = st.columns(2)
                trm = c1.selectbox("Term", TERMS)
                yr = c2.number_input("Year", 2020, 2100, default_year(), step=1)
                amount = st.number_input("Amount paid (KES)", 0, 1_000_000, 0, step=100)
                c3, c4 = st.columns(2)
                method = c3.selectbox("Method", PAY_METHODS)
                ref = c4.text_input("Reference (e.g. M-Pesa code)")
                paid_on = st.date_input("Date paid", date.today())
                ok = st.form_submit_button("Save payment", type="primary")
            if ok:
                if amount <= 0:
                    st.error("Enter an amount greater than 0.")
                else:
                    pid = run_sql(
                        "INSERT INTO payments (student_id, term, year, amount, method, reference, paid_on) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (sid, trm, int(yr), float(amount), method, ref.strip() or None, str(paid_on)),
                    )
                    log_action(
                        "Recorded payment",
                        f"RCT-{pid:05d} | {labels[sid].split(' (')[0]} | {trm} {int(yr)} | {kes(amount)}",
                    )
                    flash(f"Payment saved. Receipt No: RCT-{pid:05d}")
                    st.rerun()

    with tab_bal:
        c1, c2 = st.columns(2)
        trm = c1.selectbox("Term", TERMS, key="bal_term")
        yr = c2.number_input("Year", 2020, 2100, default_year(), step=1, key="bal_year")
        df = query_df(
            f"""
            SELECT s.admission_no AS 'Adm No', s.full_name AS Name, s.class_name AS Class,
                   COALESCE(f.amount, 0) AS Expected,
                   COALESCE(SUM(p.amount), 0) AS Paid
            FROM students s
            LEFT JOIN fee_structure f
                   ON f.class_name = s.class_name AND f.term = ? AND f.year = ?
            LEFT JOIN payments p
                   ON p.student_id = s.id AND p.term = ? AND p.year = ?
            GROUP BY s.id
            ORDER BY {class_sort_sql("s.class_name")}, s.full_name
            """,
            (trm, int(yr), trm, int(yr)),
        )
        if df.empty:
            st.info("No learners yet.")
        else:
            df["Balance"] = df["Expected"] - df["Paid"]
            m1, m2, m3 = st.columns(3)
            m1.metric("Expected", kes(df["Expected"].sum()))
            m2.metric("Collected", kes(df["Paid"].sum()))
            m3.metric("Outstanding", kes(df["Balance"].clip(lower=0).sum()))
            show_df(df)
            st.download_button(
                "Download balances (CSV)",
                df.to_csv(index=False).encode("utf-8"),
                file_name=f"fee_balances_{trm}_{int(yr)}.csv",
                mime="text/csv",
            )

    with tab_hist:
        hist = query_df(
            """
            SELECT p.id, s.full_name AS Name, p.term AS Term, p.year AS Year,
                   p.amount AS Amount, p.method AS Method, p.reference AS Reference,
                   p.paid_on AS 'Date Paid'
            FROM payments p JOIN students s ON s.id = p.student_id
            ORDER BY p.id DESC LIMIT 300
            """
        )
        if hist.empty:
            st.info("No payments recorded yet.")
        else:
            hist.insert(0, "Receipt", hist["id"].apply(lambda i: f"RCT-{i:05d}"))
            show_df(hist.drop(columns=["id"]))

            if is_admin():
                st.subheader("🗑️ Void a payment (admin)")
                options = {
                    f"{row.Receipt} | {row.Name} | {row.Term} {row.Year} | {kes(row.Amount)}": int(row.id)
                    for row in hist.itertuples()
                }
                pick = st.selectbox("Select payment", list(options))
                confirm = st.checkbox("I confirm I want to delete this payment")
                if st.button("Delete payment", type="primary"):
                    if not confirm:
                        st.warning("Tick the confirmation box first.")
                    else:
                        run_sql("DELETE FROM payments WHERE id = ?", (options[pick],))
                        log_action("Deleted payment", pick)
                        flash("Payment deleted.")
                        st.rerun()


def page_reports():
    st.header("📄 Reports")
    students, labels = learner_labels()
    if students.empty:
        st.info("Add learners and results first.")
        return

    exams = exam_names()
    school = get_setting("school_name", "")
    tab_card, tab_rank, tab_pdf = st.tabs(["Report card", "Class ranking", "Printable reports (PDF)"])

    with tab_card:
        sid = st.selectbox("Learner", list(labels), format_func=labels.get, key="rc_learner")
        c1, c2, c3 = st.columns(3)
        exam = c1.selectbox("Exam", exams, key="rc_exam")
        trm = c2.selectbox("Term", TERMS, key="rc_term")
        yr = c3.number_input("Year", 2020, 2100, default_year(), step=1, key="rc_year")

        df = query_df(
            "SELECT subject AS 'Learning Area', marks AS Marks, grade AS Level "
            "FROM results WHERE student_id = ? AND term = ? AND year = ? AND exam_type = ? "
            "ORDER BY subject",
            (sid, trm, int(yr), exam),
        )
        if df.empty:
            st.warning("No results for this learner for that exam.")
        else:
            df["Points"] = df["Marks"].apply(lambda m: get_level(m)[1])
            df["Meaning"] = df["Marks"].apply(lambda m: get_level(m)[2])
            mean = df["Marks"].mean()
            code, _, desc = get_level(mean)

            st.subheader(school)
            if school_contact_line():
                st.caption(school_contact_line())
            st.caption(f"{labels[sid]} · {exam} · {trm} {int(yr)}")
            show_df(df)
            m1, m2, m3 = st.columns(3)
            m1.metric("Total marks", int(df["Marks"].sum()))
            m2.metric("Mean marks", f"{mean:.1f}")
            m3.metric("Overall level", code)
            st.caption(f"{desc} · Total points: {int(df['Points'].sum())}")
            st.download_button(
                "Download report card (CSV)",
                df.to_csv(index=False).encode("utf-8"),
                file_name=file_slug("report_card", labels[sid].split(" (")[0], exam, trm, int(yr)) + ".csv",
                mime="text/csv",
            )

    with tab_rank:
        c1, c2 = st.columns(2)
        cls = c1.selectbox("Class", grades(), key="rk_class")
        exam = c2.selectbox("Exam", exams, key="rk_exam")
        c3, c4 = st.columns(2)
        trm = c3.selectbox("Term", TERMS, key="rk_term")
        yr = c4.number_input("Year", 2020, 2100, default_year(), step=1, key="rk_year")
        df = query_df(
            """
            SELECT s.full_name AS Name, s.admission_no AS 'Adm No',
                   COUNT(r.id) AS Subjects, SUM(r.marks) AS Total, AVG(r.marks) AS Mean
            FROM students s JOIN results r ON r.student_id = s.id
            WHERE s.class_name = ? AND r.term = ? AND r.year = ? AND r.exam_type = ?
            GROUP BY s.id ORDER BY Total DESC
            """,
            (cls, trm, int(yr), exam),
        )
        if df.empty:
            st.info("No results for that class and exam yet.")
        else:
            df["Mean"] = df["Mean"].round(1)
            df["Level"] = df["Mean"].apply(lambda m: get_level(m)[0])
            df.insert(0, "Position", range(1, len(df) + 1))
            show_df(df)

    with tab_pdf:
        st.write("Create assessment reports as a PDF, one page per learner. "
                 "The heading uses the exam name, e.g. MIDTERM ASSESSMENT REPORT.")
        mode = st.radio("Reports for", ["One learner", "Whole class"], horizontal=True)
        c1, c2, c3 = st.columns(3)
        exam = c1.selectbox("Exam", exams, key="pdf_exam")
        trm = c2.selectbox("Term", TERMS, key="pdf_term")
        yr = c3.number_input("Year", 2020, 2100, default_year(), step=1, key="pdf_year")

        if mode == "One learner":
            sid = st.selectbox("Learner", list(labels), format_func=labels.get, key="pdf_learner")
            ids = [sid]
            file_name = file_slug("report", labels[sid].split(" (")[0], exam, trm, int(yr)) + ".pdf"
        else:
            cls = st.selectbox("Class", grades(), key="pdf_class")
            ids = query_df(
                """
                SELECT DISTINCT s.id, s.full_name
                FROM students s JOIN results r ON r.student_id = s.id
                WHERE s.class_name = ? AND r.term = ? AND r.year = ? AND r.exam_type = ?
                ORDER BY s.full_name
                """,
                (cls, trm, int(yr), exam),
            )["id"].tolist()
            file_name = file_slug("reports", cls, exam, trm, int(yr)) + ".pdf"
            st.caption(f"{len(ids)} learner(s) in {cls} have {exam} results for {trm} {int(yr)}.")

        d1, d2 = st.columns(2)
        closes = d1.text_input("School closes on (optional)", placeholder="e.g. 4th Dec 2026")
        reopens = d2.text_input("Re-opens on (optional)", placeholder="e.g. 5th Jan 2027")

        rp_sig = (tuple(ids), trm, int(yr), exam, closes.strip(), reopens.strip())
        if st.button("Generate PDF", type="primary"):
            if not ids:
                st.warning("No results found for that selection.")
            else:
                try:
                    pdf = build_report_pdf(ids, trm, yr, exam, closes.strip(), reopens.strip())
                except ImportError:
                    st.error("The PDF library is missing. In the terminal run: "
                             "python -m pip install reportlab")
                else:
                    remember_pdf("pdf", rp_sig, file_name, pdf)
                    log_action("Generated reports", f"{len(ids)} learner(s), {exam} {trm} {int(yr)}")

        saved = current_pdf("pdf", rp_sig)
        if saved:
            st.success(f"{len(ids)} report(s) ready.")
            st.download_button("Download PDF", saved["data"], file_name=saved["name"],
                               mime="application/pdf")


# ---------------------------------------------------------------
# Exam analysis
# ---------------------------------------------------------------
def load_class_marks(class_name, term, year, exam):
    return query_df(
        """
        SELECT s.id, s.full_name, s.admission_no, r.subject, r.marks
        FROM students s JOIN results r ON r.student_id = s.id
        WHERE s.class_name = ? AND r.term = ? AND r.year = ? AND r.exam_type = ?
        """,
        (class_name, term, int(year), exam),
    )


def learner_summary(df):
    """One row per learner: subjects, total, mean, level and class position."""
    th = report_thresholds()
    g = df.groupby(["id", "admission_no", "full_name"], as_index=False).agg(
        Subjects=("marks", "count"), Total=("marks", "sum"), Mean=("marks", "mean")
    )
    g["Mean"] = g["Mean"].round(2)
    g["Level"] = g["Mean"].apply(lambda m: report_level(m, th))
    g["Position"] = g["Total"].rank(method="min", ascending=False).astype(int)
    return g.sort_values(["Position", "full_name"])


def subject_analysis(df):
    """Mean, range and level distribution for each learning area."""
    th = report_thresholds()
    present = set(df["subject"])
    rows = []
    for subject in [s for s in all_areas() if s in present]:
        m = df.loc[df["subject"] == subject, "marks"]
        counts = m.apply(lambda x: report_level(x, th)).value_counts()
        n = len(m)
        good = int(counts.get("EE", 0) + counts.get("ME", 0))
        rows.append(
            {
                "Learning area": report_subject_label(subject),
                "Entries": n,
                "Mean": round(float(m.mean()), 1),
                "Highest": int(m.max()),
                "Lowest": int(m.min()),
                "EE": int(counts.get("EE", 0)),
                "ME": int(counts.get("ME", 0)),
                "AE": int(counts.get("AE", 0)),
                "BE": int(counts.get("BE", 0)),
                "% at ME or above": round(100 * good / n, 1),
            }
        )
    return pd.DataFrame(rows).sort_values("Mean", ascending=False)


def broadsheet_frame(df, lrn):
    """Merit list: one row per learner, a column per learning area."""
    wide = df.pivot_table(index=["admission_no", "full_name"], columns="subject", values="marks")
    subjects = [s for s in all_areas() if s in wide.columns]
    wide = wide[subjects].astype("Int64")
    wide.columns = [report_subject_label(c) for c in wide.columns]
    wide = wide.reset_index().merge(
        lrn[["admission_no", "Subjects", "Total", "Mean", "Level", "Position"]],
        on="admission_no",
    )
    wide = wide.sort_values(["Position", "full_name"]).rename(
        columns={"admission_no": "Adm No", "full_name": "Name"}
    )
    return wide, subjects


def subject_short(subject):
    if subject == "Religious Education":
        return get_setting("religion_label", "CRE").upper()
    df = query_df("SELECT short_name FROM learning_areas WHERE name = ?", (subject,))
    short = df["short_name"][0].strip() if not df.empty else ""
    return (short or subject[:6]).upper()


def build_analysis_pdf(class_name, term, year, exam):
    """Landscape A4 exam analysis: summary, subject analysis, charts, merit list."""
    from reportlab.graphics.shapes import Drawing, Line, Rect, String
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import (
        HRFlowable,
        KeepTogether,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    df = load_class_marks(class_name, term, year, exam)
    if df.empty:
        raise ValueError("No results for that selection.")

    NAVY = colors.black
    BLUE = colors.black
    LABEL_BG = colors.white
    GRID = colors.black
    ZEBRA = colors.white

    th = report_thresholds()
    lrn = learner_summary(df)
    subj = subject_analysis(df)
    wide, subjects = broadsheet_frame(df, lrn)
    school = get_setting("school_name", "School")
    motto = get_setting("school_motto", "")
    page = landscape(A4)

    def style(name, **kw):
        base = {"fontName": "Helvetica", "fontSize": 8, "leading": 10}
        base.update(kw)
        return ParagraphStyle(name, **base)

    s_school = style("school", fontName="Helvetica-Bold", fontSize=14, leading=17,
                     alignment=TA_CENTER, textColor=NAVY)
    s_title = style("title", fontName="Helvetica-Bold", fontSize=11, leading=14,
                    alignment=TA_CENTER, textColor=NAVY)
    s_sub = style("sub", fontName="Helvetica-Bold", fontSize=9, leading=12,
                  alignment=TA_CENTER, textColor=BLUE)
    s_h = style("h", fontName="Helvetica-Bold", fontSize=9, leading=12, textColor=NAVY)
    s_note = style("note", fontSize=6.5, textColor=colors.black)

    def chrome(canvas, doc):
        width, height = page
        canvas.saveState()
        canvas.setStrokeColor(colors.black)
        canvas.setLineWidth(0.8)
        canvas.line(36, height - 23, width - 36, height - 23)
        canvas.line(36, 22, width - 36, 22)
        canvas.setFillColor(colors.black)
        canvas.setFont("Helvetica-Bold", 7)
        canvas.drawString(36, height - 15, school.upper())
        canvas.drawRightString(width - 36, height - 15, motto.upper())
        canvas.drawCentredString(width / 2, 8, f"Page {doc.page}")
        canvas.restoreState()

    def table(rows, widths, extra=None, font=8, row_h=15):
        t = Table(rows, colWidths=widths, rowHeights=[row_h] * len(rows), repeatRows=1)
        t.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), font),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
            ("LINEBELOW", (0, 0), (-1, 0), 1.5, colors.black),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.5, GRID),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ZEBRA]),
        ] + (extra or [])))
        return t

    def bar_chart(items, width, height, max_value, color_for):
        """items: list of (label, value). Simple bars drawn by hand."""
        d = Drawing(width, height)
        base, top = 22, height - 14
        slot = (width - 20) / max(len(items), 1)
        d.add(Line(10, base, width - 10, base, strokeColor=GRID, strokeWidth=0.8))
        for i, (label, value) in enumerate(items):
            h = (top - base) * (value / max_value if max_value else 0)
            x = 10 + i * slot + slot * 0.2
            d.add(Rect(x, base, slot * 0.6, h, fillColor=color_for(label, value),
                       strokeColor=GRID, strokeWidth=0.5))
            text = f"{value:.1f}" if isinstance(value, float) else str(value)
            d.add(String(x + slot * 0.3, base + h + 3, text, textAnchor="middle",
                         fontName="Helvetica-Bold", fontSize=7, fillColor=NAVY))
            d.add(String(x + slot * 0.3, 10, label, textAnchor="middle",
                         fontName="Helvetica", fontSize=7, fillColor=colors.black))
        return d

    # ---- Summary figures
    at_me = 100 * (lrn["Mean"] >= th[1]).mean()
    best, weakest = subj.iloc[0], subj.iloc[-1]
    summary = table(
        [
            ["LEARNERS", "CLASS MEAN", "HIGHEST MEAN", "LOWEST MEAN", "AT ME OR ABOVE",
             "BEST LEARNING AREA", "WEAKEST LEARNING AREA"],
            [str(len(lrn)), f"{lrn['Mean'].mean():.1f}", f"{lrn['Mean'].max():.1f}",
             f"{lrn['Mean'].min():.1f}", f"{at_me:.0f}%",
             f"{best['Learning area']} ({best['Mean']})",
             f"{weakest['Learning area']} ({weakest['Mean']})"],
        ],
        [80, 80, 90, 90, 100, 160, 165],
        row_h=18,
    )

    # ---- Subject analysis table
    rows = [["#", "LEARNING AREA", "ENTRIES", "MEAN", "HIGHEST", "LOWEST",
             "EE", "ME", "AE", "BE", "% AT ME OR ABOVE"]]
    for rank, r in enumerate(subj.itertuples(index=False), start=1):
        rows.append([str(rank), r[0], str(r[1]), f"{r[2]:.1f}", str(r[3]), str(r[4]),
                     str(r[5]), str(r[6]), str(r[7]), str(r[8]), f"{r[9]:.1f}%"])
    level_hdr = [("BACKGROUND", (6 + i, 0), (6 + i, 0), colors.HexColor(LEVEL_COLORS[lv]))
                 for i, lv in enumerate(["EE", "ME", "AE", "BE"])]
    level_hdr += [("TEXTCOLOR", (6, 0), (9, 0), colors.black)]
    subj_table = table(rows, [30, 185, 60, 60, 60, 60, 50, 50, 50, 50, 110],
                       extra=level_hdr + [("ALIGN", (1, 1), (1, -1), "LEFT")])

    # ---- Charts
    order = [s for s in all_areas() if report_subject_label(s) in set(subj["Learning area"])]
    means = dict(zip(subj["Learning area"], subj["Mean"]))
    subject_items = [(subject_short(s), float(means[report_subject_label(s)])) for s in order]
    subject_chart = bar_chart(
        subject_items, 765, 130, 100,
        lambda label, value: colors.HexColor(LEVEL_COLORS[report_level(value, th)]),
    )
    counts = lrn["Level"].value_counts().reindex(["EE", "ME", "AE", "BE"], fill_value=0)
    level_chart = bar_chart(
        [(lv, int(counts[lv])) for lv in ["EE", "ME", "AE", "BE"]],
        380, 110, max(int(counts.max()), 1),
        lambda label, value: colors.HexColor(LEVEL_COLORS[label]),
    )

    # ---- Top and bottom learners
    def learners_table(frame):
        data = [["POS", "NAME", "TOTAL", "MEAN", "LEVEL"]]
        for r in frame.itertuples(index=False):
            data.append([str(r.Position), r.full_name, str(int(r.Total)), f"{r.Mean:.1f}", r.Level])
        return table(data, [30, 170, 50, 50, 40], extra=[("ALIGN", (1, 1), (1, -1), "LEFT")])

    top5 = learners_table(lrn.head(5))
    bottom5 = learners_table(lrn.tail(5).sort_values("Position", ascending=False))
    side = Table(
        [[Paragraph("Top 5 learners", s_h), Paragraph("Bottom 5 learners (may need support)", s_h)],
         [top5, bottom5]],
        colWidths=[382, 383],
    )
    side.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))

    # ---- Broadsheet
    labels = [report_subject_label(s) for s in subjects]
    header = ["POS", "ADM NO", "NAME"] + [subject_short(s) for s in subjects] + ["TOTAL", "MEAN", "LEVEL"]
    data = [header]
    level_cells = []
    for i, r in enumerate(wide.to_dict("records"), start=1):
        marks = ["-" if pd.isna(r[lab]) else str(int(r[lab])) for lab in labels]
        data.append([str(r["Position"]), r["Adm No"], r["Name"]] + marks
                    + [str(int(r["Total"])), f"{r['Mean']:.1f}", r["Level"]])
        level_cells.append(("BACKGROUND", (len(header) - 1, i), (len(header) - 1, i),
                            colors.HexColor(LEVEL_COLORS[r["Level"]])))
    mark_w = 48
    widths = [30, 50, 150] + [mark_w] * len(subjects) + [42, 42, 36]
    broad = table(data, widths, extra=level_cells + [("ALIGN", (2, 1), (2, -1), "LEFT")],
                  font=7, row_h=14)

    key = " · ".join(f"{subject_short(s)} = {s}" for s in subjects)

    story = [
        Paragraph(school.upper(), s_school),
        *contact_flowables(),
        Paragraph(f"EXAM ANALYSIS — {exam.upper()}", s_title),
        Paragraph(f"{class_name.upper()} · {term.upper()}, {int(year)}", s_sub),
        Spacer(1, 3),
        HRFlowable(width="100%", thickness=1, color=NAVY),
        Spacer(1, 8),
        summary,
        Spacer(1, 10),
        Paragraph("Subject analysis (ranked by mean)", s_h),
        Spacer(1, 3),
        subj_table,
        Spacer(1, 8),
        KeepTogether([Paragraph("Mean mark by learning area", s_h), subject_chart]),
        Spacer(1, 6),
        KeepTogether([Paragraph("Learners by performance level", s_h), level_chart]),
        Spacer(1, 6),
        side,
        Spacer(1, 12),
        Paragraph("Broadsheet (merit list)", s_h),
        Spacer(1, 3),
        broad,
        Spacer(1, 4),
        Paragraph(key, s_note),
        Spacer(1, 6),
        Paragraph(
            f"Generated {datetime.now():%d %b %Y %H:%M}. Computer-generated analysis; "
            "please verify details with the school administration.",
            s_note,
        ),
    ]

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=page, leftMargin=36, rightMargin=36,
                            topMargin=38, bottomMargin=32,
                            title=f"{school} - {exam} analysis {class_name} {term} {int(year)}")
    doc.build(story, onFirstPage=chrome, onLaterPages=chrome)
    return buf.getvalue()


def merit_data(class_name, term, year, exam, top_n=0):
    """Ranked learners for one class and exam: (table, subjects, summary) or None."""
    df = load_class_marks(class_name, term, year, exam)
    if df.empty:
        return None
    lrn = learner_summary(df)
    wide, subjects = broadsheet_frame(df, lrn)
    wide = wide[["Position"] + [c for c in wide.columns if c != "Position"]]
    if top_n:
        wide = wide[wide["Position"] <= top_n]
    return wide.reset_index(drop=True), subjects, lrn


def build_merit_list_pdf(class_names, term, year, exam, show_subjects=True, top_n=0):
    """Merit list PDF: one section per class, ranked by total marks."""
    from xml.sax.saxutils import escape

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import (
        HRFlowable,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    sections = []
    for cls in class_names:
        data = merit_data(cls, term, year, exam, top_n)
        if data:
            sections.append((cls,) + data)
    if not sections:
        raise ValueError("No results for that selection.")

    NAVY = colors.black
    BLUE = colors.black
    LABEL_BG = colors.white
    GRID = colors.black
    ZEBRA = colors.white
    GOLD = colors.white
    school = get_setting("school_name", "School")
    motto = get_setting("school_motto", "")
    page = landscape(A4) if show_subjects else A4
    W = int(page[0] - 80)

    def style(name, **kw):
        base = {"fontName": "Helvetica", "fontSize": 8, "leading": 10}
        base.update(kw)
        return ParagraphStyle(name, **base)

    s_school = style("school", fontName="Helvetica-Bold", fontSize=15, leading=18,
                     alignment=TA_CENTER, textColor=NAVY)
    s_title = style("title", fontName="Helvetica-Bold", fontSize=11, leading=14,
                    alignment=TA_CENTER, textColor=NAVY)
    s_sub = style("sub", fontName="Helvetica-Bold", fontSize=9, leading=12,
                  alignment=TA_CENTER, textColor=BLUE)
    s_cell = style("cell", fontSize=9, leading=10.5)
    s_note = style("note", fontSize=6.5, textColor=colors.black)

    def chrome(canvas, doc):
        width, height = page
        canvas.saveState()
        canvas.setStrokeColor(colors.black)
        canvas.setLineWidth(0.8)
        canvas.line(40, height - 24, width - 40, height - 24)
        canvas.line(40, 24, width - 40, 24)
        canvas.setFillColor(colors.black)
        canvas.setFont("Helvetica-Bold", 7)
        canvas.drawString(40, height - 16, school.upper())
        canvas.drawRightString(width - 40, height - 16, motto.upper())
        canvas.drawCentredString(width / 2, 9, f"Page {doc.page}")
        canvas.restoreState()

    pages = []
    for cls, wide, subjects, lrn in sections:
        labels = [report_subject_label(s) for s in subjects]
        if show_subjects:
            sw = min(60, (W - 196 - 130) / max(len(subjects), 1))
            name_w = min(230, W - 196 - sw * len(subjects))
            widths = [30, 46, name_w] + [sw] * len(subjects) + [42, 42, 36]
            header = ["POS", "ADM NO", "NAME"] + [subject_short(s) for s in subjects] + ["TOTAL", "MEAN", "LEVEL"]
        else:
            widths = [36, 60, 210, 55, 55, 55, 44]
            header = ["POS", "ADM NO", "NAME", "SUBJECTS", "TOTAL", "MEAN", "LEVEL"]

        rows = [header]
        extra = []
        for i, r in enumerate(wide.to_dict("records"), start=1):
            row = [str(r["Position"]), r["Adm No"], r["Name"]]
            if show_subjects:
                row += ["-" if pd.isna(r[lab]) else str(int(r[lab])) for lab in labels]
            else:
                row.append(str(int(r["Subjects"])))
            row += [str(int(r["Total"])), f"{r['Mean']:.1f}", r["Level"]]
            rows.append(row)
            last = len(header) - 1
            extra.append(("BACKGROUND", (last, i), (last, i), colors.HexColor(LEVEL_COLORS[r["Level"]])))
            if r["Position"] <= 3:
                extra.append(("BACKGROUND", (0, i), (0, i), GOLD))
                extra.append(("FONTNAME", (0, i), (0, i), "Helvetica-Bold"))

        table = Table(rows, colWidths=widths, rowHeights=[20] + [15] * (len(rows) - 1), repeatRows=1)
        table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 7.5),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
            ("LINEBELOW", (0, 0), (-1, 0), 1.5, colors.black),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("ALIGN", (2, 1), (2, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.5, GRID),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ZEBRA]),
        ] + extra))

        flex = W - 100 - 80 - 50 - 90 - 50
        info_row = [
            "CLASS TEACHER:", Paragraph(escape(class_teacher(cls)), s_cell),
            "LEARNERS:", Paragraph(str(len(lrn)), s_cell),
            "CLASS MEAN:", Paragraph(f"{lrn['Mean'].mean():.1f}", s_cell),
        ]
        info = Table([info_row], colWidths=[100, flex, 80, 50, 90, 50], rowHeights=[26])
        info.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("FONTNAME", (0, 0), (0, 0), "Helvetica-Bold"),
            ("FONTNAME", (2, 0), (2, 0), "Helvetica-Bold"),
            ("FONTNAME", (4, 0), (4, 0), "Helvetica-Bold"),
            ("BACKGROUND", (0, 0), (-1, -1), LABEL_BG),
            ("GRID", (0, 0), (-1, -1), 0.5, GRID),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))

        sub = f"{cls.upper()} · {exam.upper()} · {term.upper()}, {int(year)}"
        if top_n:
            sub += f" · TOP {top_n}"
        notes = ["Ranked by total marks. Learners with equal totals share a position."]
        if show_subjects:
            notes.append(" · ".join(f"{subject_short(s)} = {s}" for s in subjects))
        pages.append([
            Paragraph(school.upper(), s_school),
            *contact_flowables(),
            Paragraph("MERIT LIST", s_title),
            Paragraph(sub, s_sub),
            Spacer(1, 4),
            HRFlowable(width="100%", thickness=1, color=NAVY),
            Spacer(1, 6),
            info,
            Spacer(1, 8),
            table,
            Spacer(1, 5),
            Paragraph(" ".join(notes), s_note),
            Paragraph(f"Generated {datetime.now():%d %b %Y %H:%M}. Computer-generated merit list.", s_note),
        ])

    story = []
    for i, flowables in enumerate(pages):
        if i:
            story.append(PageBreak())
        story += flowables

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=page, leftMargin=40, rightMargin=40,
                            topMargin=40, bottomMargin=36,
                            title=f"{school} - merit list {exam} {term} {int(year)}")
    doc.build(story, onFirstPage=chrome, onLaterPages=chrome)
    return buf.getvalue()


def csv_button(label, df, file_name):
    st.download_button(label, df.to_csv(index=False).encode("utf-8"),
                       file_name=file_name, mime="text/csv")


def page_analysis():
    st.header("📈 Exam analysis")
    th = report_thresholds()
    exams = exam_names()
    tabs = st.tabs(
        ["Class summary", "Subject analysis", "Merit list", "Compare",
         "Learner progress", "Print (PDF)"]
    )

    def pick_class(key):
        c1, c2 = st.columns(2)
        cls = c1.selectbox("Class", grades(), key=f"{key}_cls")
        exam = c2.selectbox("Exam", exams, key=f"{key}_exam")
        c3, c4 = st.columns(2)
        trm = c3.selectbox("Term", TERMS, key=f"{key}_trm")
        yr = c4.number_input("Year", 2020, 2100, default_year(), step=1, key=f"{key}_yr")
        return cls, trm, int(yr), exam

    # ---- Class summary
    with tabs[0]:
        cls, trm, yr, exam = pick_class("cs")
        df = load_class_marks(cls, trm, yr, exam)
        if df.empty:
            st.info("No results for that class and exam yet.")
        else:
            lrn = learner_summary(df)
            subj = subject_analysis(df)
            at_me = 100 * (lrn["Mean"] >= th[1]).mean()
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Learners", len(lrn))
            m2.metric("Class mean", f"{lrn['Mean'].mean():.1f}")
            m3.metric("Highest mean", f"{lrn['Mean'].max():.1f}")
            m4.metric("At ME or above", f"{at_me:.0f}%")
            st.caption(
                f"Best learning area: {subj.iloc[0]['Learning area']} ({subj.iloc[0]['Mean']}) · "
                f"Weakest: {subj.iloc[-1]['Learning area']} ({subj.iloc[-1]['Mean']})"
            )

            st.subheader("Learners by performance level")
            dist = lrn["Level"].value_counts().reindex(["EE", "ME", "AE", "BE"], fill_value=0)
            st.bar_chart(dist)

            cols = ["Position", "full_name", "Total", "Mean", "Level"]
            names = {"full_name": "Name"}
            st.subheader("Top 5 learners")
            show_df(lrn.head(5)[cols].rename(columns=names))
            st.subheader("Bottom 5 learners (may need support)")
            show_df(lrn.tail(5).sort_values("Position", ascending=False)[cols].rename(columns=names))

    # ---- Subject analysis
    with tabs[1]:
        cls, trm, yr, exam = pick_class("sa")
        df = load_class_marks(cls, trm, yr, exam)
        if df.empty:
            st.info("No results for that class and exam yet.")
        else:
            subj = subject_analysis(df)
            show_df(subj)
            st.subheader("Mean mark by learning area")
            st.bar_chart(subj.set_index("Learning area")["Mean"])
            csv_button("Download subject analysis (CSV)", subj,
                       file_slug("subject_analysis", cls, exam, trm, yr) + ".csv")

    # ---- Merit list
    with tabs[2]:
        st.write("Learners ranked by total marks. Learners with equal totals share a position.")
        scope = st.radio("Classes", ["One class", "All classes"], horizontal=True, key="ml_scope")
        classes = [st.selectbox("Class", grades(), key="ml_cls")] if scope == "One class" else grades()
        c1, c2 = st.columns(2)
        exam = c1.selectbox("Exam", exams, key="ml_exam")
        trm = c2.selectbox("Term", TERMS, key="ml_trm")
        c3, c4 = st.columns(2)
        yr = int(c3.number_input("Year", 2020, 2100, default_year(), step=1, key="ml_yr"))
        top_n = int(c4.number_input("Show only the top … (0 = everyone)", 0, 500, 0, step=1, key="ml_top"))
        show_subjects = st.checkbox("Show marks for each learning area", value=True, key="ml_subj")

        sections = {}
        for cls in classes:
            data = merit_data(cls, trm, yr, exam, top_n)
            if data:
                sections[cls] = data
        if not sections:
            st.info("No results for that selection yet.")
        else:
            parts = []
            for cls, (wide, subjects, lrn) in sections.items():
                st.subheader(f"{cls} · {len(lrn)} learners")
                shown = wide if show_subjects else wide[
                    ["Position", "Adm No", "Name", "Subjects", "Total", "Mean", "Level"]]
                show_df(shown)
                parts.append(shown.assign(Class=cls))
            combined = pd.concat(parts, ignore_index=True)
            combined = combined[["Class"] + [c for c in combined.columns if c != "Class"]]
            label = "AllClasses" if scope == "All classes" else classes[0]
            csv_button("Download merit list (CSV)", combined,
                       file_slug("merit_list", label, exam, trm, yr) + ".csv")

            ml_sig = (tuple(classes), trm, yr, exam, show_subjects, top_n)
            if st.button("Generate merit list PDF", type="primary", key="ml_go"):
                try:
                    pdf = build_merit_list_pdf(classes, trm, yr, exam, show_subjects, top_n)
                except ImportError:
                    st.error("The PDF library is missing. In the terminal run: "
                             "python -m pip install reportlab")
                else:
                    remember_pdf("merit_pdf", ml_sig,
                                 file_slug("merit_list", label, exam, trm, yr) + ".pdf", pdf)
                    log_action("Generated merit list", f"{label} {exam} {trm} {yr}")
            saved = current_pdf("merit_pdf", ml_sig)
            if saved:
                st.success("Merit list PDF ready.")
                st.download_button("Download merit list (PDF)", saved["data"], file_name=saved["name"],
                                   mime="application/pdf")

    # ---- Compare
    with tabs[3]:
        c1, c2, c3 = st.columns(3)
        cls = c1.selectbox("Class", grades(), key="ct_cls")
        exam = c2.selectbox("Exam", exams, key="ct_exam")
        yr = int(c3.number_input("Year", 2020, 2100, default_year(), step=1, key="ct_yr"))

        st.subheader(f"{exam} across terms")
        sm = query_df(
            """
            SELECT r.term, r.subject, AVG(r.marks) AS mean
            FROM students s JOIN results r ON r.student_id = s.id
            WHERE s.class_name = ? AND r.year = ? AND r.exam_type = ?
            GROUP BY r.term, r.subject
            """,
            (cls, yr, exam),
        )
        if sm.empty:
            st.info("No results for that class, exam and year yet.")
        else:
            piv = sm.pivot(index="subject", columns="term", values="mean")
            piv = piv.reindex(columns=[t for t in TERMS if t in piv.columns])
            piv = piv.reindex([s for s in all_areas() if s in piv.index])
            piv.loc["ALL AREAS"] = piv.mean()
            piv.index = [report_subject_label(i) for i in piv.index]
            piv.index.name = "Learning area"
            show_df(piv.round(1).reset_index())
            st.line_chart(piv.iloc[:-1].T)

            st.write("**Most improved and most declined learners**")
            t1, t2 = st.columns(2)
            ta = t1.selectbox("From", TERMS, index=0, key="ct_a")
            tb = t2.selectbox("To", TERMS, index=1, key="ct_b")
            if ta == tb:
                st.info("Pick two different terms.")
            else:
                lm = query_df(
                    """
                    SELECT s.id, s.full_name AS Name, r.term, AVG(r.marks) AS mean
                    FROM students s JOIN results r ON r.student_id = s.id
                    WHERE s.class_name = ? AND r.year = ? AND r.exam_type = ?
                          AND r.term IN (?, ?)
                    GROUP BY s.id, s.full_name, r.term
                    """,
                    (cls, yr, exam, ta, tb),
                )
                if lm.empty or lm["term"].nunique() < 2:
                    st.info("Both terms need results for this class and exam.")
                else:
                    pv = lm.pivot(index=["id", "Name"], columns="term", values="mean").dropna()
                    if pv.empty:
                        st.info("No learner has results in both terms.")
                    else:
                        pv["Change"] = pv[tb] - pv[ta]
                        pv = pv.reset_index().drop(columns=["id"]).round(1)
                        pv = pv[["Name", ta, tb, "Change"]]
                        st.write("Most improved")
                        show_df(pv.sort_values("Change", ascending=False).head(5))
                        declined = pv[pv["Change"] < 0].sort_values("Change").head(5)
                        st.write("Most declined")
                        if declined.empty:
                            st.caption("No learner's mean dropped.")
                        else:
                            show_df(declined)

        st.divider()
        st.subheader("Exams within a term")
        tm = st.selectbox("Term", TERMS, key="ce_term")
        em = query_df(
            """
            SELECT r.exam_type, r.subject, AVG(r.marks) AS mean
            FROM students s JOIN results r ON r.student_id = s.id
            WHERE s.class_name = ? AND r.year = ? AND r.term = ?
            GROUP BY r.exam_type, r.subject
            """,
            (cls, yr, tm),
        )
        if em.empty:
            st.info("No results for that class and term yet.")
        else:
            pe = em.pivot(index="subject", columns="exam_type", values="mean")
            pe = pe.reindex(columns=[e for e in exams if e in pe.columns])
            pe = pe.reindex([s for s in all_areas() if s in pe.index])
            pe.loc["ALL AREAS"] = pe.mean()
            pe.index = [report_subject_label(i) for i in pe.index]
            pe.index.name = "Learning area"
            show_df(pe.round(1).reset_index())
            st.bar_chart(pe.loc["ALL AREAS"])

    # ---- Learner progress
    with tabs[4]:
        students, labels = learner_labels()
        if students.empty:
            st.info("Add learners first.")
        else:
            sid = st.selectbox("Learner", list(labels), format_func=labels.get, key="lp_l")
            c1, c2 = st.columns(2)
            exam = c1.selectbox("Exam", exams, key="lp_exam")
            yr = int(c2.number_input("Year", 2020, 2100, default_year(), step=1, key="lp_yr"))
            df = query_df(
                "SELECT term, subject, marks FROM results "
                "WHERE student_id = ? AND year = ? AND exam_type = ?",
                (sid, yr, exam),
            )
            if df.empty:
                st.info("No results for this learner for that exam and year.")
            else:
                piv = df.pivot(index="subject", columns="term", values="marks")
                piv = piv.reindex(columns=[t for t in TERMS if t in piv.columns])
                piv = piv.reindex([s for s in all_areas() if s in piv.index])
                piv.loc["MEAN"] = piv.mean()
                piv.index = [report_subject_label(i) for i in piv.index]
                piv.index.name = "Learning area"
                show_df(piv.round(1).reset_index())
                st.subheader("Mean mark by term")
                st.line_chart(piv.loc["MEAN"])

    # ---- Print (PDF)
    with tabs[5]:
        st.write("Create a printable analysis for one class and exam: summary, subject "
                 "analysis, charts, top and bottom learners and the full broadsheet.")
        cls, trm, yr, exam = pick_class("ap")
        df = load_class_marks(cls, trm, yr, exam)
        if df.empty:
            st.info("No results for that class and exam yet.")
        else:
            st.caption(f"{df['id'].nunique()} learner(s) · {df['subject'].nunique()} learning area(s)")
            ap_sig = (cls, trm, yr, exam)
            if st.button("Generate analysis PDF", type="primary", key="ap_go"):
                try:
                    pdf = build_analysis_pdf(cls, trm, yr, exam)
                except ImportError:
                    st.error("The PDF library is missing. In the terminal run: "
                             "python -m pip install reportlab")
                else:
                    remember_pdf("analysis_pdf", ap_sig,
                                 file_slug("analysis", cls, exam, trm, yr) + ".pdf", pdf)
                    log_action("Generated analysis PDF", f"{cls} {exam} {trm} {yr}")
            saved = current_pdf("analysis_pdf", ap_sig)
            if saved:
                st.success("Analysis PDF ready.")
                st.download_button("Download analysis PDF", saved["data"], file_name=saved["name"],
                                   mime="application/pdf")


# ---------------------------------------------------------------
# Admin
# ---------------------------------------------------------------
def admin_users():
    users = query_df(
        "SELECT id, username, full_name, role, active FROM users ORDER BY username"
    )
    shown = users.drop(columns=["id"]).rename(
        columns={"username": "Username", "full_name": "Full name", "role": "Role", "active": "Active"}
    )
    shown["Active"] = shown["Active"].map({1: "Yes", 0: "No"})
    show_df(shown)

    st.subheader("Add user")
    with st.form("add_user", clear_on_submit=True):
        c1, c2 = st.columns(2)
        username = c1.text_input("Username")
        full = c2.text_input("Full name")
        role = c1.selectbox("Role", ROLES)
        pw = c2.text_input("Temporary password (min 8)", type="password")
        ok = st.form_submit_button("Create user", type="primary")
    if ok:
        username = username.strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,30}", username):
            st.error("Username: 3-30 letters, numbers, dot, dash or underscore.")
        elif not full.strip():
            st.error("Enter the person's full name.")
        elif len(pw) < 8:
            st.error("Temporary password must be at least 8 characters.")
        else:
            try:
                run_sql(
                    "INSERT INTO users (username, full_name, role, password_hash, must_change) "
                    "VALUES (?, ?, ?, ?, 1)",
                    (username, clean_name(full), role, hash_password(pw)),
                )
                log_action("Created user", f"{username} ({role})")
                flash(f"User '{username}' created. They must change the password at first login.")
                st.rerun()
            except sqlite3.IntegrityError:
                st.error("That username already exists.")

    st.subheader("Manage user")
    names = {int(r.id): f"{r.username} ({r.role})" for r in users.itertuples()}
    uid = st.selectbox("User", list(names), format_func=names.get)
    target = users[users["id"] == uid].iloc[0]
    me = current_user()["id"]
    active_admins = users[(users["role"] == "admin") & (users["active"] == 1)]
    last_admin = (
        target["role"] == "admin"
        and int(target["active"]) == 1
        and len(active_admins) <= 1
    )

    with st.form(f"reset_{uid}", clear_on_submit=True):
        new_pw = st.text_input("New temporary password (min 8)", type="password")
        ok = st.form_submit_button("Reset password")
    if ok:
        if len(new_pw) < 8:
            st.error("Password must be at least 8 characters.")
        else:
            run_sql(
                "UPDATE users SET password_hash = ?, must_change = 1 WHERE id = ?",
                (hash_password(new_pw), uid),
            )
            log_action("Reset password", target["username"])
            st.success("Password reset. They must change it at next login.")

    c1, c2 = st.columns(2)
    new_role = c1.selectbox("Role", ROLES, index=ROLES.index(target["role"]), key=f"role_{uid}")
    if c2.button("Update role"):
        if last_admin and new_role != "admin":
            st.error("You cannot remove the last active admin.")
        else:
            run_sql("UPDATE users SET role = ? WHERE id = ?", (new_role, uid))
            log_action("Changed role", f"{target['username']} -> {new_role}")
            flash("Role updated.")
            st.rerun()

    if int(target["active"]) == 1:
        if st.button("Deactivate user"):
            if uid == me or last_admin:
                st.error("You cannot deactivate yourself or the last active admin.")
            else:
                run_sql("UPDATE users SET active = 0 WHERE id = ?", (uid,))
                log_action("Deactivated user", target["username"])
                flash("User deactivated.")
                st.rerun()
    else:
        if st.button("Activate user"):
            run_sql("UPDATE users SET active = 1 WHERE id = ?", (uid,))
            log_action("Activated user", target["username"])
            flash("User activated.")
            st.rerun()

    confirm = st.checkbox("I confirm I want to delete this user", key=f"delu_{uid}")
    if st.button("Delete user", type="primary"):
        if not confirm:
            st.warning("Tick the confirmation box first.")
        elif uid == me or last_admin:
            st.error("You cannot delete yourself or the last active admin.")
        else:
            run_sql("DELETE FROM users WHERE id = ?", (uid,))
            log_action("Deleted user", target["username"])
            flash("User deleted.")
            st.rerun()


def admin_learners():
    students, labels = learner_labels()
    if students.empty:
        st.info("No learners yet.")
        return
    sid = st.selectbox("Learner", list(labels), format_func=labels.get, key="adm_learner")
    row = query_df("SELECT * FROM students WHERE id = ?", (sid,)).iloc[0]

    st.subheader("Edit learner")
    classes = ALL_GRADES + ["Completed"]
    if row["class_name"] not in classes:
        classes.append(row["class_name"])
    with st.form(f"edit_{sid}"):
        adm = st.text_input("Admission number", row["admission_no"])
        name = st.text_input("Full name", row["full_name"])
        idx = classes.index(row["class_name"]) if row["class_name"] in classes else 0
        cls = st.selectbox("Class", classes, index=idx)
        phone = st.text_input("Parent phone", row["parent_phone"] or "")
        ok = st.form_submit_button("Save changes", type="primary")
    if ok:
        adm, name, phone = adm.strip(), clean_name(name), phone.strip()
        if not adm or not name:
            st.error("Admission number and full name are required.")
        elif phone and not valid_phone(phone):
            st.error("Enter a valid Kenyan phone number.")
        else:
            try:
                run_sql(
                    "UPDATE students SET admission_no = ?, full_name = ?, class_name = ?, "
                    "parent_phone = ? WHERE id = ?",
                    (adm, name, cls, phone or None, sid),
                )
                log_action("Edited learner", f"{row['full_name']} -> {name}, {cls}")
                flash("Learner updated.")
                st.rerun()
            except sqlite3.IntegrityError:
                st.error("Another learner already has that name or admission number.")

    st.subheader("Delete learner")
    st.warning("This also deletes all of this learner's results and payments.")
    confirm = st.checkbox("I understand and want to delete this learner", key=f"dell_{sid}")
    if st.button("Delete learner", type="primary"):
        if not confirm:
            st.warning("Tick the confirmation box first.")
        else:
            run_sql("DELETE FROM students WHERE id = ?", (sid,))
            log_action("Deleted learner", labels[sid])
            flash("Learner deleted.")
            st.rerun()


def admin_promote():
    offered = grades()
    moves = []
    for g in offered:
        nxt = f"Grade {int(g.split()[1]) + 1}"
        moves.append((g, nxt if nxt in offered else "Completed"))
    st.write(
        "Moves every learner up one grade. Learners in the highest grade the school offers "
        "become **Completed**. Do this **after** the last term's results and fees are recorded, "
        "and make a backup first."
    )
    show_df(pd.DataFrame(moves, columns=["From", "To"]))
    counts = query_df("SELECT class_name AS Class, COUNT(*) AS Learners FROM students "
                      "GROUP BY class_name ORDER BY " + class_sort_sql("class_name"))
    if not counts.empty:
        st.write("Learners now:")
        show_df(counts)
    confirm = st.checkbox("I have made a backup and want to promote all classes")
    if st.button("Promote all classes", type="primary"):
        if not confirm:
            st.warning("Tick the confirmation box first.")
        else:
            make_backup("pre_promotion")
            # highest grade first, so no learner is moved twice
            run_many([("UPDATE students SET class_name = ? WHERE class_name = ?", (to, frm))
                      for frm, to in reversed(moves)])
            log_action("Promoted all classes")
            flash("All classes promoted.")
            st.rerun()


# ---------------------------------------------------------------
# Importing learners from Excel or CSV
# ---------------------------------------------------------------
IMPORT_ALIASES = {
    "admission_no": {"admission no", "admission number", "admission", "adm no", "adm number",
                     "adm", "admno"},
    "full_name": {"full name", "name", "learner name", "learner s name", "student name",
                  "names", "learner", "pupil name", "pupil"},
    "class_name": {"class name", "class", "grade", "level", "form"},
    "parent_phone": {"parent phone", "phone", "telephone", "tel", "parent contact", "contact",
                     "mobile", "phone number", "guardian phone", "parent telephone",
                     "parent s phone"},
    "first_name": {"first name", "firstname", "given name"},
    "middle_name": {"middle name", "other names", "other name", "middle names"},
    "last_name": {"last name", "lastname", "surname", "second name"},
}


def normalize_class(value):
    """'7', 'grade 7', 'Grade9', 'G7', '7A' -> 'Grade 7' (Grades 1-9); otherwise None."""
    text = str(value)
    if re.search(r"(?i)form|\bpp|pre", text):  # Form 1-4 and pre-primary are not Grades 1-9
        return None
    m = re.search(r"(?<!\d)([1-9])(?!\d)", text)
    return f"Grade {m.group(1)}" if m else None


def normalize_phone(raw):
    """Fix numbers Excel mangled: 712345678 -> 0712345678, 254712345678 -> +254712345678."""
    p = re.sub(r"\.0+$", "", str(raw).strip())
    p = re.sub(r"[ \-().]", "", p)
    if re.fullmatch(r"[71]\d{8}", p):
        return "0" + p
    if re.fullmatch(r"254[71]\d{8}", p):
        return "+" + p
    return p


def parse_learner_sheet(df, header_row=1):
    """Check every row of an uploaded sheet. Returns (rows, error_message)."""
    found = {}
    for col in df.columns:
        key = re.sub(r"[^a-z0-9]+", " ", str(col).lower()).strip()
        for field, aliases in IMPORT_ALIASES.items():
            if key in aliases and field not in found:
                found[field] = col

    parts = [found[f] for f in ("first_name", "middle_name", "last_name") if f in found]
    has_name = "full_name" in found or bool(parts)
    missing = []
    if "admission_no" not in found:
        missing.append("admission number")
    if not has_name:
        missing.append("name")
    if "class_name" not in found:
        missing.append("class")
    if missing:
        return [], (f"Could not find a column for: {', '.join(missing)}. "
                    f"Columns found: {', '.join(str(c) for c in df.columns) or 'none'}. "
                    "Check the 'row that holds the column headings' above.")

    existing = query_df("SELECT admission_no, full_name FROM students")
    taken_adm = set(existing["admission_no"])
    taken_names = {" ".join(n.split()).lower() for n in existing["full_name"]}
    seen_adm, seen_names, rows = set(), set(), []

    for i, r in df.iterrows():
        if all(str(v).strip() == "" for v in r.values):
            continue
        adm = re.sub(r"\.0+$", "", str(r[found["admission_no"]]).strip())
        raw_name = (str(r[found["full_name"]]) if "full_name" in found
                    else " ".join(str(r[c]).strip() for c in parts))
        name = clean_name(raw_name)
        raw_class = str(r[found["class_name"]]).strip()
        cls = normalize_class(raw_class)
        phone = normalize_phone(r[found["parent_phone"]]) if "parent_phone" in found else ""

        problem = ""
        if not adm or not name:
            problem = "Missing admission number or name"
        elif cls is None:
            problem = f"Class '{raw_class}' is not a Grade 1 to 9 class"
        elif cls not in grades():
            problem = f"{cls} is not offered at this school (see Admin → Settings)"
        elif phone and not valid_phone(phone):
            problem = f"Invalid phone number '{phone}'"
        elif adm in taken_adm:
            problem = "Admission number already in the system"
        elif name.lower() in taken_names:
            problem = "A learner with this name already exists"
        elif adm in seen_adm:
            problem = "Admission number repeated earlier in the file"
        elif name.lower() in seen_names:
            problem = "Name repeated earlier in the file"

        if not problem:
            seen_adm.add(adm)
            seen_names.add(name.lower())
        rows.append({
            "Row": int(i) + header_row + 1,
            "Adm No": adm,
            "Name": name,
            "Class": cls or raw_class,
            "Parent Phone": phone,
            "Status": "Skipped" if problem else "Ready",
            "Reason": problem,
        })
    return rows, ""


def excel_template_bytes():
    """A ready-to-fill .xlsx file with the right columns and a notes sheet."""
    sample = pd.DataFrame({
        "admission_no": ["001", "002"],
        "full_name": ["Jane Wanjiru", "Peter Otieno"],
        "class_name": ["Grade 7", "Grade 8"],
        "parent_phone": ["0712345678", ""],
    })
    notes = pd.DataFrame({"Notes": [
        "Keep the column headings in row 1 of the Learners sheet.",
        "class_name must be a grade from Grade 1 to Grade 9 that your school offers.",
        "Every learner's name and admission number must be different from the others.",
        "parent_phone is optional, e.g. 0712345678.",
        "Delete the two sample rows before adding your learners.",
    ]})
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        sample.to_excel(writer, sheet_name="Learners", index=False)
        notes.to_excel(writer, sheet_name="Notes", index=False)
        ws = writer.sheets["Learners"]
        for col, width in zip("ABCD", (16, 30, 14, 18)):
            ws.column_dimensions[col].width = width
        for col in "ABD":  # text format keeps leading zeros (001, 0712...)
            for row in range(2, 1001):
                ws[f"{col}{row}"].number_format = "@"
        writer.sheets["Notes"].column_dimensions["A"].width = 80
    return buf.getvalue()


def import_learners_panel(key):
    st.write("Add many learners at once from an Excel (.xlsx) or CSV file. "
             "Columns can be called things like Adm No, Name, Class or Grade, and Phone. "
             "You will see a preview before anything is saved.")
    try:
        st.download_button(
            "Download Excel template", excel_template_bytes(), "learners_template.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"{key}_tpl",
        )
    except ImportError:
        st.caption("To get the Excel template, run: python -m pip install openpyxl")

    version = st.session_state.get(f"{key}_v", 0)
    file = st.file_uploader("Excel or CSV file", type=["xlsx", "csv"], key=f"{key}_file_{version}")
    if not file:
        return

    header_row = int(st.number_input("Row that holds the column headings", 1, 30, 1, step=1,
                                     key=f"{key}_hdr_{version}"))
    try:
        if file.name.lower().endswith(".csv"):
            df = pd.read_csv(file, dtype=str, header=header_row - 1)
        else:
            book = pd.ExcelFile(file)
            sheet = book.sheet_names[0]
            if len(book.sheet_names) > 1:
                sheet = st.selectbox("Sheet", book.sheet_names, key=f"{key}_sheet_{version}")
            df = pd.read_excel(book, sheet_name=sheet, header=header_row - 1, dtype=str)
    except ImportError:
        st.error("Reading Excel files needs one more library. In the terminal run: "
                 "python -m pip install openpyxl")
        return
    except Exception as exc:
        st.error(f"Could not read that file: {exc}. Save it as .xlsx and try again.")
        return
    df = df.fillna("")

    rows, error = parse_learner_sheet(df, header_row)
    if error:
        st.error(error)
        return
    if not rows:
        st.info("No learners found in that sheet.")
        return

    report = pd.DataFrame(rows)
    ready = report[report["Status"] == "Ready"]
    skipped = report[report["Status"] == "Skipped"]
    m1, m2 = st.columns(2)
    m1.metric("Ready to import", len(ready))
    m2.metric("Will be skipped", len(skipped))
    if len(skipped):
        st.warning("Rows that will be skipped (fix them in the file and upload again if needed):")
        show_df(skipped[["Row", "Adm No", "Name", "Class", "Reason"]])
    if len(ready):
        st.write("Learners that will be added:")
        show_df(ready[["Row", "Adm No", "Name", "Class", "Parent Phone"]])
        if st.button(f"Import {len(ready)} learner(s)", type="primary", key=f"{key}_go_{version}"):
            statements = [
                ("INSERT INTO students (admission_no, full_name, class_name, parent_phone) "
                 "VALUES (?, ?, ?, ?)",
                 (r["Adm No"], r["Name"], r["Class"], r["Parent Phone"] or None))
                for r in ready.to_dict("records")
            ]
            try:
                run_many(statements)
            except sqlite3.IntegrityError:
                st.error("Someone added a learner with the same name or admission number "
                         "while you were importing. Upload the file again.")
            else:
                log_action("Imported learners",
                           f"{len(ready)} added, {len(skipped)} skipped ({file.name})")
                st.session_state[f"{key}_v"] = version + 1
                flash(f"{len(ready)} learner(s) imported"
                      + (f", {len(skipped)} skipped." if len(skipped) else "."))
                st.rerun()


def admin_import():
    import_learners_panel("adm")


def admin_settings():
    ee0, me0, ae0 = report_thresholds()
    with st.form("settings"):
        school = st.text_input("School name", get_setting("school_name", "My School"))
        motto = st.text_input("School motto (printed on reports)", get_setting("school_motto", ""))
        pobox = st.text_input("P.O. Box (e.g. P.O. Box 91, Kaloleni)", get_setting("school_pobox", ""))
        phone = st.text_input("Telephone number(s)", get_setting("school_phone", ""),
                              placeholder="e.g. 0712 345 678 / 0733 123 456")
        email = st.text_input("Email address", get_setting("school_email", ""))
        religion = st.text_input(
            "Religious Education label on reports (CRE, IRE or HRE)",
            get_setting("religion_label", "CRE"),
        )
        year = st.number_input("Current academic year", 2020, 2100, default_year(), step=1)
        offered = st.multiselect("Grades this school offers", ALL_GRADES, default=grades())
        st.markdown("**Printed report levels – minimum marks**")
        c1, c2, c3 = st.columns(3)
        ee = c1.number_input("EE from", 1, 100, ee0)
        me = c2.number_input("ME from", 1, 100, me0)
        ae = c3.number_input("AE from", 1, 100, ae0)
        st.caption("Marks below the AE minimum are BE.")
        ok = st.form_submit_button("Save settings", type="primary")
    if ok:
        if not school.strip():
            st.error("School name cannot be empty.")
        elif not offered:
            st.error("Choose at least one grade.")
        elif not (ee > me > ae):
            st.error("Minimum marks must go down from EE to ME to AE.")
        elif phone.strip() and not re.fullmatch(r"[0-9+()\-/,. ]{7,40}", phone.strip()):
            st.error("Enter a valid telephone number, e.g. 0712 345 678 "
                     "(put / or , between two numbers).")
        elif email.strip() and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email.strip()):
            st.error("Enter a valid email address, e.g. info@school.ac.ke.")
        else:
            set_setting("school_name", school.strip())
            set_setting("school_motto", motto.strip())
            set_setting("grades_offered", ",".join(g for g in ALL_GRADES if g in offered))
            set_setting("school_pobox", " ".join(pobox.split()))
            set_setting("school_phone", " ".join(phone.split()))
            set_setting("school_email", email.strip())
            set_setting("religion_label", religion.strip().upper() or "CRE")
            set_setting("academic_year", int(year))
            set_setting("level_ee", int(ee))
            set_setting("level_me", int(me))
            set_setting("level_ae", int(ae))
            log_action("Changed settings", f"{school.strip()}, {int(year)}")
            flash("Settings saved.")
            st.rerun()

    if school_contact_line():
        st.caption(f"Printed under the motto as: {school_contact_line()}")

    st.subheader("School logo (shown on printed reports)")
    if LOGO_PATH.exists():
        st.image(str(LOGO_PATH), width=80)
        if st.button("Remove logo"):
            LOGO_PATH.unlink()
            log_action("Removed logo")
            st.rerun()
    logo = st.file_uploader("Upload logo (PNG or JPG)", type=["png", "jpg", "jpeg"])
    if logo and st.button("Save logo", type="primary"):
        LOGO_PATH.write_bytes(logo.getvalue())
        log_action("Uploaded logo")
        flash("Logo saved.")
        st.rerun()

    st.subheader("Dashboard background")
    st.caption("A photo shown behind the dashboard. A dark or light layer over the photo keeps "
               "the text readable.")
    if BG_PATH.exists():
        st.image(str(BG_PATH), width=260)
    try:
        strength_now = max(0, min(90, int(get_setting("dashboard_overlay", "60"))))
    except ValueError:
        strength_now = 60
    with st.form("bg_style"):
        tone = st.radio(
            "Overlay and text",
            ["Dark overlay, white text", "Light overlay, dark text"],
            index=1 if get_setting("dashboard_tone", "dark") == "light" else 0,
            horizontal=True,
        )
        strength = st.slider("Overlay strength (higher makes the text easier to read)",
                             0, 90, strength_now, step=5)
        ok_bg = st.form_submit_button("Save style")
    if ok_bg:
        set_setting("dashboard_tone", "light" if tone.startswith("Light") else "dark")
        set_setting("dashboard_overlay", int(strength))
        log_action("Changed dashboard background style")
        flash("Dashboard background style saved.")
        st.rerun()

    photo = st.file_uploader("Upload a photo (JPG, PNG or WEBP)", type=["jpg", "jpeg", "png", "webp"],
                             key="bg_upload")
    if photo and st.button("Use this photo", type="primary", key="bg_save"):
        data = photo.getvalue()
        if len(data) > 25 * 1024 * 1024:
            st.error("That photo is over 25 MB. Choose a smaller one.")
        else:
            try:
                save_dashboard_background(data)
            except ImportError:
                st.error("Pillow is missing. In the terminal run: python -m pip install pillow")
            except Exception:
                st.error("Could not read that file as a picture. Try a JPG or PNG.")
            else:
                log_action("Set dashboard background")
                flash("Dashboard background saved. Open the Dashboard to see it.")
                st.rerun()
    if BG_PATH.exists() and st.button("Remove background photo", key="bg_remove"):
        BG_PATH.unlink()
        log_action("Removed dashboard background")
        flash("Dashboard background removed.")
        st.rerun()


def admin_backup():
    st.write(f"Data folder: `{DATA_DIR}`")
    st.caption("A backup is made automatically once a day (last 14 kept) in the `backups` folder.")

    if st.button("Create backup now", type="primary"):
        path = make_backup("manual")
        st.session_state["last_backup"] = str(path)
        log_action("Created backup", path.name)
    last = st.session_state.get("last_backup")
    if last and Path(last).exists():
        st.success(f"Backup saved: {Path(last).name}")
        st.download_button(
            "Download this backup",
            Path(last).read_bytes(),
            file_name=Path(last).name,
            mime="application/octet-stream",
        )

    st.divider()
    st.subheader("Restore from a backup")
    st.warning("Restoring replaces ALL current data, including user accounts. "
               "A safety copy of the current data is saved first.")
    upload = st.file_uploader("Backup file (.db)", type="db")
    confirm = st.checkbox("I understand and want to restore this backup")
    if upload and st.button("Restore backup"):
        data = upload.getvalue()
        if not confirm:
            st.warning("Tick the confirmation box first.")
        elif not data.startswith(b"SQLite format 3\x00"):
            st.error("That file is not a valid database backup.")
        else:
            make_backup("pre_restore")
            DB_PATH.write_bytes(data)
            st.session_state.clear()
            st.rerun()


def admin_audit():
    df = query_df(
        "SELECT created_at AS Time, username AS User, action AS Action, details AS Details "
        "FROM audit_log ORDER BY id DESC LIMIT 500"
    )
    if df.empty:
        st.info("Nothing logged yet.")
    else:
        show_df(df)
        st.download_button(
            "Download audit log (CSV)",
            df.to_csv(index=False).encode("utf-8"),
            file_name="audit_log.csv",
            mime="text/csv",
        )


def admin_exams():
    st.write("Exam types appear when recording results and in reports and analysis. "
             "Add, rename, reorder or remove them to match your school's calendar.")
    ex = query_df(
        """
        SELECT e.id, e.name, e.sort_order,
               (SELECT COUNT(*) FROM results r WHERE r.exam_type = e.name) AS used
        FROM exam_types e ORDER BY e.sort_order, e.id
        """
    )
    show_df(ex.rename(columns={"name": "Exam type", "used": "Results recorded"})[
        ["Exam type", "Results recorded"]])

    st.subheader("Add exam type")
    with st.form("add_exam", clear_on_submit=True):
        name = st.text_input("Name (e.g. Opener, Midterm, End of Term, Mock)")
        ok = st.form_submit_button("Add exam type", type="primary")
    if ok:
        name = " ".join(name.split())
        if not name:
            st.error("Enter a name.")
        elif len(name) > 40:
            st.error("Keep the name under 40 characters.")
        else:
            try:
                run_sql(
                    "INSERT INTO exam_types (name, sort_order) "
                    "VALUES (?, (SELECT COALESCE(MAX(sort_order), 0) + 1 FROM exam_types))",
                    (name,),
                )
                log_action("Added exam type", name)
                flash(f"Exam type '{name}' added.")
                st.rerun()
            except sqlite3.IntegrityError:
                st.error("That exam type already exists.")

    st.subheader("Edit exam type")
    names = {int(r.id): r.name for r in ex.itertuples()}
    eid = st.selectbox("Exam type", list(names), format_func=names.get, key="exam_pick")
    old = names[eid]

    with st.form(f"rename_exam_{eid}"):
        new = st.text_input("Rename to", old)
        ok = st.form_submit_button("Rename")
    if ok:
        new = " ".join(new.split())
        if not new:
            st.error("Enter a name.")
        elif new == old:
            st.info("The name is unchanged.")
        else:
            try:
                run_many(
                    [
                        ("UPDATE exam_types SET name = ? WHERE id = ?", (new, eid)),
                        ("UPDATE results SET exam_type = ? WHERE exam_type = ?", (new, old)),
                    ]
                )
                log_action("Renamed exam type", f"{old} -> {new}")
                flash("Exam type renamed. Existing results were updated too.")
                st.rerun()
            except sqlite3.IntegrityError:
                st.error("An exam type with that name already exists.")

    order = ex["id"].astype(int).tolist()
    pos = order.index(eid)
    c1, c2 = st.columns(2)
    if c1.button("Move up", disabled=pos == 0):
        order[pos - 1], order[pos] = order[pos], order[pos - 1]
        run_many([("UPDATE exam_types SET sort_order = ? WHERE id = ?", (i, x))
                  for i, x in enumerate(order, start=1)])
        st.rerun()
    if c2.button("Move down", disabled=pos == len(order) - 1):
        order[pos + 1], order[pos] = order[pos], order[pos + 1]
        run_many([("UPDATE exam_types SET sort_order = ? WHERE id = ?", (i, x))
                  for i, x in enumerate(order, start=1)])
        st.rerun()

    used = int(ex.loc[ex["id"] == eid, "used"].iloc[0])
    confirm = st.checkbox("I confirm I want to delete this exam type", key=f"delx_{eid}")
    if st.button("Delete exam type", type="primary"):
        if not confirm:
            st.warning("Tick the confirmation box first.")
        elif len(order) <= 1:
            st.error("You must keep at least one exam type.")
        elif used:
            st.error(f"{used} result(s) use '{old}'. Rename it instead, or delete those results first.")
        else:
            run_sql("DELETE FROM exam_types WHERE id = ?", (eid,))
            log_action("Deleted exam type", old)
            flash("Exam type deleted.")
            st.rerun()


LEVELS = {
    "Lower primary (Grades 1-3)": range(1, 4),
    "Upper primary (Grades 4-6)": range(4, 7),
    "Junior school (Grades 7-9)": range(7, 10),
}


def grade_ranges(grade_names):
    """['Grade 1', 'Grade 2', 'Grade 3', 'Grade 5'] -> '1-3, 5'"""
    nums = sorted({int(g.split()[1]) for g in grade_names if g.startswith("Grade ")})
    parts, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        parts.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    return ", ".join(parts)


def admin_teachers():
    st.write("Set the class teacher and the teacher of each learning area, class by class. "
             "These names are printed on class lists.")
    cls = st.selectbox("Class", grades(), key="tch_class")
    areas = areas_for(cls)
    current = subject_teachers(cls)
    with st.form(f"teachers_{cls}"):
        ct = st.text_input("Class teacher", class_teacher(cls), key=f"tch_ct_{cls}")
        entries = {
            s: st.text_input(f"{s} teacher", current.get(s, ""), key=f"tch_{cls}_{s}")
            for s in areas
        }
        everywhere = st.checkbox("Also use these names for every other class that takes the same learning area")
        ok = st.form_submit_button("Save teachers", type="primary")
    if ok:
        set_setting(f"class_teacher_{cls}", " ".join(ct.split()))
        statements = []
        for s, name in entries.items():
            name = " ".join(name.split())
            targets = [g for g in grades() if s in areas_for(g)] if (everywhere and name) else [cls]
            for target in targets:
                if name:
                    statements.append((
                        "INSERT INTO subject_teachers (class_name, subject, teacher_name) "
                        "VALUES (?, ?, ?) ON CONFLICT(class_name, subject) "
                        "DO UPDATE SET teacher_name = excluded.teacher_name",
                        (target, s, name),
                    ))
                else:
                    statements.append((
                        "DELETE FROM subject_teachers WHERE class_name = ? AND subject = ?",
                        (target, s),
                    ))
        run_many(statements)
        log_action("Set teachers", cls + (" (and other classes)" if everywhere else ""))
        flash(f"Teachers saved for {cls}" + (" and the other classes." if everywhere else "."))
        st.rerun()

    overview = query_df(
        "SELECT class_name AS Class, subject AS 'Learning area', teacher_name AS Teacher "
        "FROM subject_teachers ORDER BY " + class_sort_sql("class_name") + ", subject"
    )
    if not overview.empty:
        st.subheader("All subject teachers")
        show_df(overview)


def admin_areas():
    st.write("Learning areas (subjects) and the grades that take them. Changes apply to results "
             "entry, report cards, class lists and analysis. The starting lists follow the Kenyan "
             "CBE; check them against your own curriculum.")
    df = query_df(
        """
        SELECT a.id, a.name, a.short_name, a.optional,
               (SELECT COUNT(*) FROM results r WHERE r.subject = a.name) AS used
        FROM learning_areas a ORDER BY a.sort_order, a.id
        """
    )
    links = query_df("SELECT area_id, class_name FROM grade_areas")
    taught = {}
    for r in links.itertuples():
        taught.setdefault(int(r.area_id), []).append(r.class_name)

    show_df(pd.DataFrame({
        "Learning area": df["name"],
        "Short name": df["short_name"],
        "Grades": [grade_ranges(taught.get(int(i), [])) for i in df["id"]],
        "Optional": df["optional"].map({1: "Yes", 0: ""}),
        "Results recorded": df["used"],
    }))
    st.caption("Optional learning areas appear on a learner's report card only if the learner has "
               "marks in them.")

    st.subheader("Add learning area")
    with st.form("add_area", clear_on_submit=True):
        name = st.text_input("Name (e.g. Biology, Computer Studies)")
        short = st.text_input("Short name for lists and charts (optional, e.g. BIO)")
        levels = st.multiselect("Whole levels that take it", list(LEVELS))
        extra = st.multiselect("Or pick individual grades", ALL_GRADES)
        optional = st.checkbox("Optional (only shown on a learner's report if they have marks in it)")
        ok = st.form_submit_button("Add learning area", type="primary")
    if ok:
        name = " ".join(name.split())
        chosen = {f"Grade {n}" for lv in levels for n in LEVELS[lv]} | set(extra)
        if not name:
            st.error("Enter a name.")
        elif len(name) > 50:
            st.error("Keep the name under 50 characters.")
        elif not chosen:
            st.error("Choose at least one grade that takes it.")
        else:
            try:
                new_id = run_sql(
                    "INSERT INTO learning_areas (name, short_name, sort_order, optional) VALUES "
                    "(?, ?, (SELECT COALESCE(MAX(sort_order), 0) + 1 FROM learning_areas), ?)",
                    (name, short.strip().upper()[:12], int(optional)),
                )
            except sqlite3.IntegrityError:
                st.error("A learning area with that name already exists.")
            else:
                run_many([("INSERT INTO grade_areas (class_name, area_id) VALUES (?, ?)", (g, new_id))
                          for g in ALL_GRADES if g in chosen])
                log_action("Added learning area", f"{name} ({grade_ranges(chosen)})")
                flash(f"Learning area '{name}' added.")
                st.rerun()

    st.subheader("Edit learning area")
    names = {int(r.id): r.name for r in df.itertuples()}
    aid = st.selectbox("Learning area", list(names), format_func=names.get, key="area_pick")
    row = df[df["id"] == aid].iloc[0]
    old = row["name"]
    with st.form(f"edit_area_{aid}"):
        new_name = st.text_input("Name", old)
        new_short = st.text_input("Short name", row["short_name"])
        new_grades = st.multiselect("Grades that take it", ALL_GRADES,
                                    default=sorted(taught.get(int(aid), []), key=ALL_GRADES.index))
        new_optional = st.checkbox("Optional", value=bool(row["optional"]))
        ok = st.form_submit_button("Save changes", type="primary")
    if ok:
        new_name = " ".join(new_name.split())
        if not new_name:
            st.error("Enter a name.")
        elif not new_grades:
            st.error("Choose at least one grade.")
        else:
            statements = [(
                "UPDATE learning_areas SET name = ?, short_name = ?, optional = ? WHERE id = ?",
                (new_name, new_short.strip().upper()[:12], int(new_optional), int(aid)),
            )]
            if new_name != old:
                statements += [
                    ("UPDATE results SET subject = ? WHERE subject = ?", (new_name, old)),
                    ("UPDATE subject_teachers SET subject = ? WHERE subject = ?", (new_name, old)),
                ]
            statements.append(("DELETE FROM grade_areas WHERE area_id = ?", (int(aid),)))
            statements += [("INSERT INTO grade_areas (class_name, area_id) VALUES (?, ?)", (g, int(aid)))
                           for g in ALL_GRADES if g in new_grades]
            try:
                run_many(statements)
            except sqlite3.IntegrityError:
                st.error("Another learning area already has that name.")
            else:
                log_action("Edited learning area", f"{old} -> {new_name} ({grade_ranges(new_grades)})")
                flash("Learning area updated. Existing marks were renamed too.")
                st.rerun()

    order = df["id"].astype(int).tolist()
    pos = order.index(int(aid))
    c1, c2 = st.columns(2)
    if c1.button("Move up", disabled=pos == 0, key="area_up"):
        order[pos - 1], order[pos] = order[pos], order[pos - 1]
        run_many([("UPDATE learning_areas SET sort_order = ? WHERE id = ?", (i, x))
                  for i, x in enumerate(order, start=1)])
        st.rerun()
    if c2.button("Move down", disabled=pos == len(order) - 1, key="area_down"):
        order[pos + 1], order[pos] = order[pos], order[pos + 1]
        run_many([("UPDATE learning_areas SET sort_order = ? WHERE id = ?", (i, x))
                  for i, x in enumerate(order, start=1)])
        st.rerun()

    used = int(row["used"])
    confirm = st.checkbox("I confirm I want to delete this learning area", key=f"delarea_{aid}")
    if st.button("Delete learning area", type="primary", key="area_del"):
        if not confirm:
            st.warning("Tick the confirmation box first.")
        elif len(order) <= 1:
            st.error("You must keep at least one learning area.")
        elif used:
            st.error(f"{used} mark(s) are recorded for '{old}'. Rename it instead, or delete those results first.")
        else:
            run_many([
                ("DELETE FROM subject_teachers WHERE subject = ?", (old,)),
                ("DELETE FROM grade_areas WHERE area_id = ?", (int(aid),)),
                ("DELETE FROM learning_areas WHERE id = ?", (int(aid),)),
            ])
            log_action("Deleted learning area", old)
            flash("Learning area deleted.")
            st.rerun()


def page_admin():
    st.header("🛠️ Admin")
    show_flash()
    tabs = st.tabs(
        ["Users", "Learners", "Exams", "Learning areas", "Teachers", "Promote", "Import",
         "Settings", "Backup", "Audit log"]
    )
    with tabs[0]:
        admin_users()
    with tabs[1]:
        admin_learners()
    with tabs[2]:
        admin_exams()
    with tabs[3]:
        admin_areas()
    with tabs[4]:
        admin_teachers()
    with tabs[5]:
        admin_promote()
    with tabs[6]:
        admin_import()
    with tabs[7]:
        admin_settings()
    with tabs[8]:
        admin_backup()
    with tabs[9]:
        admin_audit()


# ---------------------------------------------------------------
# App
# ---------------------------------------------------------------
PAGE_FUNCS = {
    "Dashboard": page_dashboard,
    "Learners": page_learners,
    "Results": page_results,
    "Analysis": page_analysis,
    "Fees": page_fees,
    "Reports": page_reports,
    "Admin": page_admin,
}


def main():
    st.set_page_config(page_title="School Tracker", page_icon="🏫", layout="wide")
    init_db()

    if "backup_done" not in st.session_state:
        auto_backup()
        st.session_state["backup_done"] = True

    user = current_user()
    if not user:
        login_screen()
        return
    if user["must_change"]:
        force_password_change_screen()
        return

    st.sidebar.title(f"🏫 {get_setting('school_name', 'School Tracker')}")
    sidebar_account()
    page = st.sidebar.radio("Menu", ROLE_PAGES.get(user["role"], ["Dashboard"]))
    st.sidebar.caption("Kenya CBE · Grades 1-9")
    PAGE_FUNCS[page]()


main()
