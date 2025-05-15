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

        # 중복 예약 검사 (오전은 '밖'으로 간주)
        cur.execute("SELECT timeslot FROM reservations WHERE name = %s", (name,))
        existing = [r['timeslot'] for r in cur.fetchall()]

        has_in = any('(안)' in t for t in existing)
        has_out = any('(밖)' in t or '(안)' not in t and '(밖)' not in t for t in existing)  # 오전도 밖으로 간주
        is_in = '(안)' in timeslot
        is_out = '(밖)' in timeslot or ('(안)' not in timeslot and '(밖)' not in timeslot)

        if (is_in and has_in) or (is_out and has_out):
            message = "이미 해당 구역에 예약하셨습니다."
        else:
            cur.execute("SELECT COUNT(*) as count FROM reservations WHERE timeslot = %s", (timeslot,))
            count_slot = cur.fetchone()
            max_count = 3
            if count_slot and count_slot['count'] < max_count:
                cur.execute("INSERT INTO reservations (name, timeslot) VALUES (%s, %s)", (name, timeslot))
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
        if t < '2025-05-25 12:30':
            cur.execute("SELECT COUNT(*) as count FROM reservations WHERE timeslot = %s", (t,))
            count = cur.fetchone()['count'] if cur.rowcount else 0
            slot_counts[t] = {
                "in": {"reserved": 0, "remaining": 3},
                "out": {"reserved": count, "remaining": 3 - count}  # 오전은 밖
            }
            slots.append({
                'time': t,
                'count': count,
                'full': count >= 3,
                'remaining': max(0, 3 - count),
                'in': 0,
                'out': count
            })
        else:
            cur.execute("SELECT COUNT(*) as count FROM reservations WHERE timeslot = %s", (t + " (안)",))
            in_count = cur.fetchone()['count'] if cur.rowcount else 0
            cur.execute("SELECT COUNT(*) as count FROM reservations WHERE timeslot = %s", (t + " (밖)",))
            out_count = cur.fetchone()['count'] if cur.rowcount else 0

            slot_counts[t] = {
                "in": {
                    "reserved": in_count,
                    "remaining": 3 - in_count
                },
                "out": {
                    "reserved": out_count,
                    "remaining": 3 - out_count
                }
            }

            total_reserved = in_count + out_count
            total_remaining = max(0, 6 - total_reserved)

            slots.append({
                'time': t,
                'count': total_reserved,
                'full': total_reserved >= 6,
                'remaining': total_remaining,
                'in': in_count,
                'out': out_count
            })
    return slots, slot_counts

# --- 나머지 라우트는 동일 ---
