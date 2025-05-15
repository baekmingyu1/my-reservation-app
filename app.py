import os
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, session
from psycopg2.extras import RealDictCursor
import psycopg2
from dotenv import load_dotenv
from collections import defaultdict
import traceback

load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "default-secret")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")

def get_connection():
    return psycopg2.connect(DATABASE_URL)

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("admin") != True:
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated

def init_db():
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
                    CREATE TABLE IF NOT EXISTS reservations (
                                                                id SERIAL PRIMARY KEY,
                                                                name TEXT NOT NULL,
                                                                timeslot TEXT NOT NULL,
                                                                order_in_slot INTEGER,
                                                                used BOOLEAN DEFAULT FALSE,
                                                                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    """)
        cur.execute("""
                    CREATE TABLE IF NOT EXISTS settings (
                                                            key TEXT PRIMARY KEY,
                                                            value TEXT
                    );
                    """)
        # order_in_slot 컬럼 추가 확인
        cur.execute("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'reservations' AND column_name = 'order_in_slot';
                    """)
        if not cur.fetchone():
            cur.execute("ALTER TABLE reservations ADD COLUMN order_in_slot INTEGER;")
            cur.execute("""
                        WITH ordered AS (
                            SELECT id,
                                   ROW_NUMBER() OVER (PARTITION BY timeslot ORDER BY created_at) AS rn
                            FROM reservations
                        )
                        UPDATE reservations
                        SET order_in_slot = ordered.rn
                            FROM ordered
                        WHERE reservations.id = ordered.id;
                        """)
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        traceback.print_exc()

init_db()

def generate_timeslots():
    base_time = datetime(2025, 5, 25, 10, 0)
    slots = []
    for i in range(100):
        t = base_time + timedelta(minutes=5 * i)
        if datetime(2025, 5, 25, 11, 0) <= t < datetime(2025, 5, 25, 12, 30):
            continue
        slots.append(t.strftime('%Y-%m-%d %H:%M'))
    return slots

def load_slots_with_counts(cur):
    timeslots = []
    slot_counts = {}
    for t in generate_timeslots():
        is_morning = datetime.strptime(t, "%Y-%m-%d %H:%M") < datetime(2025, 5, 25, 12, 30)
        if is_morning:
            cur.execute("SELECT COUNT(*) as count FROM reservations WHERE timeslot = %s", (t,))
            out = cur.fetchone()["count"]
            slot_counts[t] = {"in": {"reserved": 0, "remaining": 0}, "out": {"reserved": out, "remaining": 3 - out}}
            timeslots.append({"time": t, "count": out, "full": out >= 3})
        else:
            cur.execute("SELECT COUNT(*) as count FROM reservations WHERE timeslot = %s", (t + " (안)",))
            in_count = cur.fetchone()["count"]
            cur.execute("SELECT COUNT(*) as count FROM reservations WHERE timeslot = %s", (t + " (밖)",))
            out_count = cur.fetchone()["count"]
            total = in_count + out_count
            slot_counts[t] = {
                "in": {"reserved": in_count, "remaining": 3 - in_count},
                "out": {"reserved": out_count, "remaining": 3 - out_count}
            }
            timeslots.append({"time": t, "count": total, "full": total >= 6})
    return timeslots, slot_counts

@app.route('/', methods=['GET', 'POST'])
def index():
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    message = None

    if request.method == 'POST':
        name = request.form.get('name')
        timeslot = request.form.get('timeslot')

        # 오픈 시간 체크
        cur.execute("SELECT value FROM settings WHERE key = 'open_time'")
        row = cur.fetchone()
        open_time = None
        if row and row.get("value"):
            raw = row["value"].strip()
            try:
                open_time = datetime.strptime(raw, "%Y-%m-%d %H:%M")
            except ValueError:
                try:
                    open_time = datetime.strptime(raw, "%Y-%m-%dT%H:%M")
                except:
                    open_time = None
        if open_time and datetime.now() < open_time:
            slots, counts = load_slots_with_counts(cur)
            return render_template("index.html", message="⏰ 예약은 아직 오픈되지 않았습니다.", timeslots=slots, timeslot_counts=counts)

        # 오전 시간 (안) 예약 방지
        try:
            dt_base = timeslot.split(" ")[0] + " " + timeslot.split(" ")[1]
            slot_time = datetime.strptime(dt_base, "%Y-%m-%d %H:%M")
            if slot_time < datetime(2025, 5, 25, 12, 30) and "(안)" in timeslot:
                message = "❌ 오전 시간은 '바깥'만 예약 가능합니다."
                slots, counts = load_slots_with_counts(cur)
                return render_template("index.html", message=message, timeslots=slots, timeslot_counts=counts)
        except:
            pass

        # 중복 검사
        cur.execute("SELECT timeslot FROM reservations WHERE name = %s", (name,))
        reserved = [r["timeslot"] for r in cur.fetchall()]
        has_in = any("(안)" in r for r in reserved)
        has_out = any("(밖)" in r or "(안)" not in r for r in reserved)
        is_in = "(안)" in timeslot
        is_out = "(밖)" in timeslot or "(안)" not in timeslot

        if (is_in and has_in) or (is_out and has_out):
            message = "이미 해당 구역에 예약하셨습니다."
        else:
            cur.execute("SELECT COUNT(*) as count FROM reservations WHERE timeslot = %s", (timeslot,))
            count = cur.fetchone()["count"]
            if count < 3:
                order = count + 1
                cur.execute("INSERT INTO reservations (name, timeslot, order_in_slot) VALUES (%s, %s, %s)", (name, timeslot, order))
                conn.commit()
                message = f"{name}님, {timeslot} 예약이 완료되었습니다."
            else:
                message = "해당 시간대는 예약이 마감되었습니다."

    slots, counts = load_slots_with_counts(cur)
    cur.close()
    conn.close()
    return render_template("index.html", timeslots=slots, message=message, timeslot_counts=counts)

@app.route('/my', methods=['GET', 'POST'])
def my():
    name = request.form.get('name') if request.method == 'POST' else None
    message = None
    reservations = []
    if name:
        conn = get_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM reservations WHERE name = %s ORDER BY created_at", (name,))
        reservations = cur.fetchall()
        cur.close()
        conn.close()
        if not reservations:
            message = "예약 내역이 없습니다."
    return render_template("my.html", name=name, message=message, reservations=reservations)

@app.route('/cancel_reservation', methods=['POST'])
def cancel_reservation():
    name = request.form.get('name')
    timeslot = request.form.get('timeslot')
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("DELETE FROM reservations WHERE name = %s AND timeslot = %s", (name, timeslot))
    conn.commit()
    cur.execute("SELECT * FROM reservations WHERE name = %s ORDER BY created_at", (name,))
    reservations = cur.fetchall()
    cur.close()
    conn.close()
    message = "✅ 예약이 취소되었습니다."
    return render_template("my.html", name=name, message=message, reservations=reservations)

@app.route("/admin", methods=["GET", "POST"])
@admin_required
def admin():
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    action = request.form.get("action")
    if request.method == "POST":
        if action == "reset_used":
            cur.execute("UPDATE reservations SET used = FALSE")
        elif action == "set_open":
            val = request.form.get("open_time")
            try:
                dt = datetime.strptime(val, "%Y-%m-%dT%H:%M")
                formatted = dt.strftime("%Y-%m-%d %H:%M")
            except:
                formatted = val
            cur.execute("""
                        INSERT INTO settings (key, value) VALUES ('open_time', %s)
                            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                        """, (formatted,))
        elif action == "add_reservation":
            name = request.form.get("admin_name")
            time = request.form.get("admin_time")
            if name and time:
                cur.execute("SELECT COUNT(*) as count FROM reservations WHERE timeslot = %s", (time,))
                count = cur.fetchone()["count"]
                if count < 3:
                    order = count + 1
                    cur.execute("INSERT INTO reservations (name, timeslot, order_in_slot) VALUES (%s, %s, %s)", (name, time, order))
        conn.commit()

    cur.execute("SELECT * FROM reservations ORDER BY timeslot, order_in_slot")
    reservations = cur.fetchall()
    grouped = defaultdict(list)
    for r in reservations:
        grouped[r["timeslot"]].append(r)

    cur.execute("SELECT value FROM settings WHERE key = 'open_time'")
    row = cur.fetchone()
    open_time = ""
    if row and row.get("value"):
        raw = row["value"]
        try:
            dt = datetime.strptime(raw, "%Y-%m-%d %H:%M")
            open_time = dt.strftime("%Y-%m-%dT%H:%M")
        except:
            open_time = raw

    cur.close()
    conn.close()
    return render_template("admin.html", grouped=grouped, open_time=open_time)

@app.route("/admin/toggle_used", methods=["POST"])
@admin_required
def toggle_used():
    reservation_id = request.form.get("id")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE reservations SET used = NOT used WHERE id = %s", (reservation_id,))
    conn.commit()
    cur.close()
    conn.close()
    return "ok"

@app.route("/admin/delete_reservation", methods=["POST"])
@admin_required
def delete_reservation():
    reservation_id = request.form.get("reservation_id")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM reservations WHERE id = %s", (reservation_id,))
    conn.commit()
    cur.close()
    conn.close()
    return "ok"

@app.route("/login", methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == 'admin' and password == ADMIN_PASSWORD:
            session['admin'] = True
            return redirect('/admin')
        else:
            error = "아이디 또는 비밀번호가 틀렸습니다."
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.pop("admin", None)
    return redirect("/")

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
