import os
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, session, url_for
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from collections import defaultdict
import traceback

# Load .env
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
SECRET_KEY = os.getenv("SECRET_KEY", "default-secret")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "your-password")

app = Flask(__name__)
app.secret_key = SECRET_KEY

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
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        traceback.print_exc()

init_db()

def generate_timeslots():
    base_time = datetime(2025, 5, 25, 10, 0)
    all_slots = [(base_time + timedelta(minutes=5 * i)) for i in range(100)]
    times = []
    for t in all_slots:
        if datetime(2025, 5, 25, 11, 0) <= t < datetime(2025, 5, 25, 12, 30):
            continue
        formatted = t.strftime('%Y-%m-%d %H:%M')
        times.append(formatted)
    return times

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
        if row:
            open_time = datetime.strptime(row['value'], '%Y-%m-%d %H:%M')
            if datetime.now() < open_time:
                slots, _ = load_slots_with_counts(cur)
                return render_template("index.html", message="⏰ 예약은 아직 오픈되지 않았습니다.", timeslots=slots, timeslot_counts={})

        # 중복 예약 검사
        cur.execute("SELECT timeslot FROM reservations WHERE name = %s", (name,))
        existing = [r['timeslot'] for r in cur.fetchall()]
        has_in = any('(안)' in t for t in existing)
        has_out = any('(밖)' in t or ('(안)' not in t and '(밖)' not in t) for t in existing)
        is_in = '(안)' in timeslot
        is_out = '(밖)' in timeslot or ('(안)' not in timeslot and '(밖)' not in timeslot)

        if (is_in and has_in) or (is_out and has_out):
            message = "이미 해당 구역에 예약하셨습니다."
        else:
            cur.execute("SELECT COUNT(*) as count FROM reservations WHERE timeslot = %s", (timeslot,))
            count_slot = cur.fetchone()
            max_count = 3
            if count_slot and count_slot['count'] < max_count:
                order_in_slot = count_slot['count'] + 1
                cur.execute(
                    "INSERT INTO reservations (name, timeslot, order_in_slot) VALUES (%s, %s, %s)",
                    (name, timeslot, order_in_slot)
                )
                conn.commit()
                message = f"{name}님, {timeslot} 예약이 완료되었습니다."
            else:
                message = "해당 시간대는 예약이 마감되었습니다."

    slots, slot_counts = load_slots_with_counts(cur)
    cur.close()
    conn.close()
    return render_template('index.html', timeslots=slots, message=message, timeslot_counts=slot_counts)

def load_slots_with_counts(cur):
    slots = []
    slot_counts = {}
    for t in generate_timeslots():
        is_morning = datetime.strptime(t[:16], '%Y-%m-%d %H:%M') < datetime(2025, 5, 25, 12, 30)
        base_time = t[:16]
        if is_morning:
            cur.execute("SELECT COUNT(*) as count FROM reservations WHERE timeslot = %s", (t,))
            out_count = cur.fetchone()['count'] if cur.rowcount else 0
            in_count = 0
        else:
            cur.execute("SELECT COUNT(*) as count FROM reservations WHERE timeslot = %s", (t + " (안)",))
            in_count = cur.fetchone()['count'] if cur.rowcount else 0
            cur.execute("SELECT COUNT(*) as count FROM reservations WHERE timeslot = %s", (t + " (밖)",))
            out_count = cur.fetchone()['count'] if cur.rowcount else 0

        slot_counts[base_time] = {
            "in": {"reserved": in_count, "remaining": 3 - in_count},
            "out": {"reserved": out_count, "remaining": 3 - out_count}
        }

        total_reserved = in_count + out_count
        total_limit = 3 if is_morning else 6
        total_remaining = max(0, total_limit - total_reserved)
        label = t if not is_morning else t + " (밖)"

        slots.append({
            'time': base_time,
            'label': label,
            'count': total_reserved,
            'full': total_reserved >= total_limit,
            'remaining': total_remaining,
            'in': in_count,
            'out': out_count
        })
    return slots, slot_counts

@app.route('/my', methods=['GET', 'POST'])
def my():
    name = request.form.get('name') if request.method == 'POST' else None
    reservations = []
    if name:
        conn = get_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM reservations WHERE name = %s ORDER BY created_at", (name,))
        reservations = cur.fetchall()
        cur.close()
        conn.close()
    return render_template("my.html", name=name, reservations=reservations)

@app.route('/cancel_reservation', methods=['POST'])
def cancel_reservation():
    name = request.form.get('name')
    timeslot = request.form.get('timeslot')
    message = ""
    reservations = []

    if name and timeslot:
        conn = get_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("DELETE FROM reservations WHERE name = %s AND timeslot = %s", (name, timeslot))
        deleted = cur.rowcount
        conn.commit()
        cur.execute("SELECT * FROM reservations WHERE name = %s ORDER BY created_at", (name,))
        reservations = cur.fetchall()
        cur.close()
        conn.close()
        message = f"✅ {timeslot} 예약이 취소되었습니다." if deleted else "❌ 예약을 찾을 수 없습니다."
    else:
        message = "❌ 이름 또는 시간 정보가 누락되었습니다."

    return render_template("my.html", name=name, message=message, reservations=reservations)

@app.route("/admin", methods=["GET", "POST"])
@admin_required
def admin():
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    action = request.form.get("action")

    if action == "reset_used":
        cur.execute("UPDATE reservations SET used = FALSE")
    elif action == "set_open":
        open_time = request.form.get("open_time")
        cur.execute("""
                    INSERT INTO settings (key, value) VALUES ('open_time', %s)
                        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                    """, (open_time,))
    elif action == "add_reservation":
        name = request.form.get("admin_name")
        timeslot = request.form.get("admin_time")
        if name and timeslot:
            cur.execute("SELECT COUNT(*) FROM reservations WHERE timeslot = %s", (timeslot,))
            count = cur.fetchone()[0]
            if count < 3:
                order_in_slot = count + 1
                cur.execute("INSERT INTO reservations (name, timeslot, order_in_slot) VALUES (%s, %s, %s)",
                            (name, timeslot, order_in_slot))
    conn.commit()

    cur.execute("SELECT * FROM reservations ORDER BY timeslot, order_in_slot")
    reservations = cur.fetchall()

    cur.execute("SELECT value FROM settings WHERE key = 'open_time'")
    row = cur.fetchone()
    open_time = row["value"] if row else "설정 안 됨"

    cur.close()
    conn.close()
    return render_template("admin.html", reservations=reservations, open_time=open_time)

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
    return redirect("/admin")

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
    return redirect("/admin")

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
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
