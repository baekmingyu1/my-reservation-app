import os
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, session, url_for
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import traceback

# Load .env
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
SECRET_KEY = os.getenv("SECRET_KEY", "default-secret")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "your-password")

app = Flask(__name__)
app.secret_key = SECRET_KEY

# --- Utility ---
def get_connection():
    return psycopg2.connect(DATABASE_URL)

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("admin") != True:
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated

# --- DB 초기화 ---
def init_db():
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
                    CREATE TABLE IF NOT EXISTS reservations (
                                                                id SERIAL PRIMARY KEY,
                                                                name TEXT NOT NULL,
                                                                timeslot TEXT NOT NULL,
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

# --- 시간대 생성 ---
def generate_timeslots():
    base_time = datetime(2025, 5, 25, 10, 0)
    all_slots = [(base_time + timedelta(minutes=5 * i)) for i in range(100)]
    times = []
    for t in all_slots:
        if datetime(2025, 5, 25, 11, 0) <= t < datetime(2025, 5, 25, 12, 30):
            continue
        times.append(t.strftime('%Y-%m-%d %H:%M'))
    return times

# --- Index ---
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
                return render_template("index.html", message="⏰ 예약은 아직 오픈되지 않았습니다.", timeslots=load_slots(cur), timeslot_counts={})

        cur.execute("SELECT COUNT(*) FROM reservations WHERE name = %s", (name,))
        count_check = cur.fetchone()
        if count_check and count_check['count'] == 0:
            cur.execute("SELECT COUNT(*) FROM reservations WHERE timeslot = %s", (timeslot,))
            count_slot = cur.fetchone()
            if count_slot and count_slot['count'] < 3:
                cur.execute("INSERT INTO reservations (name, timeslot) VALUES (%s, %s)", (name, timeslot))
                conn.commit()
                message = f"{name}님, {timeslot} 예약이 완료되었습니다."
            else:
                message = "해당 시간대는 예약이 마감되었습니다."
        else:
            message = "이미 예약한 이름입니다."

    slots, slot_counts = load_slots_with_counts(cur)
    cur.close()
    conn.close()
    return render_template('index.html', timeslots=slots, message=message, timeslot_counts=slot_counts)

def load_slots_with_counts(cur):
    slots = []
    slot_counts = {}
    for t in generate_timeslots():
        cur.execute("SELECT COUNT(*) as count FROM reservations WHERE timeslot = %s", (t,))
        result = cur.fetchone()
        count = result['count'] if result else 0
        slots.append({
            'time': t,
            'count': count,
            'full': count >= 3,
            'remaining': 3 - count
        })
        # 모달용 안/밖 카운트
        cur.execute("SELECT COUNT(*) as count FROM reservations WHERE timeslot = %s", (t + " (안)",))
        in_count = cur.fetchone()['count'] if cur.rowcount else 0
        cur.execute("SELECT COUNT(*) as count FROM reservations WHERE timeslot = %s", (t + " (밖)",))
        out_count = cur.fetchone()['count'] if cur.rowcount else 0
        slot_counts[t] = {"in": 3 - in_count, "out": 3 - out_count}
    return slots, slot_counts


# --- 내 예약 확인 ---
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

# --- 관리자 ---
from collections import defaultdict

@app.route("/admin", methods=["GET", "POST"])
@admin_required
def admin():
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    if request.method == "POST":
        cur.execute("UPDATE reservations SET used = FALSE")
        used_ids = request.form.getlist("used_ids")
        if used_ids:
            cur.execute("UPDATE reservations SET used = TRUE WHERE id = ANY(%s)", (used_ids,))
        conn.commit()

    cur.execute("SELECT * FROM reservations ORDER BY timeslot, created_at")
    reservations = cur.fetchall()

    grouped = defaultdict(list)
    for r in reservations:
        grouped[r["timeslot"]].append(r)

    cur.execute("SELECT value FROM settings WHERE key = 'open_time'")
    row = cur.fetchone()
    open_time = row["value"] if row else "설정 안 됨"

    cur.close()
    conn.close()

    return render_template("admin.html", grouped=grouped, open_time=open_time)

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

@app.route("/admin/set_open_time", methods=["POST"])
@admin_required
def set_open_time():
    open_time = request.form.get("open_time")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
                INSERT INTO settings (key, value)
                VALUES ('open_time', %s)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """, (open_time,))
    conn.commit()
    cur.close()
    conn.close()
    return redirect("/admin")

@app.route("/admin/add_reservation", methods=["POST"])
@admin_required
def add_reservation():
    name = request.form.get("name")
    timeslot = request.form.get("timeslot")
    if name and timeslot:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM reservations WHERE timeslot = %s", (timeslot,))
        count = cur.fetchone()[0]
        if count < 3:
            cur.execute("INSERT INTO reservations (name, timeslot) VALUES (%s, %s)", (name, timeslot))
            conn.commit()
        cur.close()
        conn.close()
    return redirect("/admin")

# --- 로그인 / 로그아웃 ---
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

# --- Run ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
