# app.py 전체 정리 + toggle_used / delete_reservation 비동기 응답 지원
import os
from datetime import datetime, timedelta
from functools import wraps
from collections import defaultdict
from flask import Flask, render_template, request, redirect, session, url_for, jsonify
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import traceback

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
        cur.execute("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'reservations' AND column_name = 'order_in_slot';
                    """)
        if not cur.fetchone():
            cur.execute("ALTER TABLE reservations ADD COLUMN order_in_slot INTEGER;")
            cur.execute("""
                        WITH ordered AS (
                            SELECT id, ROW_NUMBER() OVER (PARTITION BY timeslot ORDER BY created_at) AS rn
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
    all_slots = [(base_time + timedelta(minutes=5 * i)) for i in range(100)]
    times = []
    for t in all_slots:
        if datetime(2025, 5, 25, 11, 0) <= t < datetime(2025, 5, 25, 12, 30):
            continue
        times.append(t.strftime('%Y-%m-%d %H:%M'))
    return times

@app.route('/', methods=['GET', 'POST'])
def index():
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    message = None

    if request.method == 'POST':
        name = request.form.get('name')
        timeslot = request.form.get('timeslot')

        cur.execute("SELECT value FROM settings WHERE key = 'open_time'")
        row = cur.fetchone()
        open_time = None
        if row and row.get("value"):
            raw = row["value"].strip()
            try:
                open_time = datetime.strptime(raw, "%Y-%m-%d %H:%M")
            except:
                try:
                    open_time = datetime.strptime(raw, "%Y-%m-%dT%H:%M")
                except:
                    pass
        if open_time and datetime.now() < open_time:
            slots, slot_counts = load_slots_with_counts(cur)
            return render_template("index.html", message="⏰ 예약은 아직 오픈되지 않았습니다.", timeslots=slots, timeslot_counts=slot_counts)

        try:
            raw_dt = timeslot.split(' ')[0] + ' ' + timeslot.split(' ')[1]
            slot_time = datetime.strptime(raw_dt, "%Y-%m-%d %H:%M")
            if slot_time < datetime(2025, 5, 25, 12, 30) and '(안)' in timeslot:
                message = "❌ 오전 시간은 '바깥'만 예약 가능합니다."
                slots, slot_counts = load_slots_with_counts(cur)
                return render_template("index.html", message=message, timeslots=slots, timeslot_counts=slot_counts)
        except Exception as e:
            print("❗ 시간 파싱 실패:", e)

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
            result = cur.fetchone()
            count = result["count"] if result else 0
            if count < 3:
                order_in_slot = count + 1
                cur.execute("INSERT INTO reservations (name, timeslot, order_in_slot) VALUES (%s, %s, %s)",
                            (name, timeslot, order_in_slot))
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
        is_morning = datetime.strptime(t, '%Y-%m-%d %H:%M') < datetime(2025, 5, 25, 12, 30)
        base_time = t[:16]
        if is_morning:
            cur.execute("SELECT COUNT(*) as count FROM reservations WHERE timeslot = %s", (t,))
            out_count = cur.fetchone()['count']
            in_count = 0
        else:
            cur.execute("SELECT COUNT(*) as count FROM reservations WHERE timeslot = %s", (t + " (안)",))
            in_count = cur.fetchone()['count']
            cur.execute("SELECT COUNT(*) as count FROM reservations WHERE timeslot = %s", (t + " (밖)",))
            out_count = cur.fetchone()['count']

        slot_counts[base_time] = {
            "in": {"reserved": in_count, "remaining": 3 - in_count},
            "out": {"reserved": out_count, "remaining": 3 - out_count}
        }

        total_reserved = in_count + out_count
        total_limit = 3 if is_morning else 6
        total_remaining = max(0, total_limit - total_reserved)

        slots.append({
            'time': base_time,
            'label': t,
            'count': total_reserved,
            'full': total_reserved >= total_limit,
            'remaining': total_remaining,
            'in': in_count,
            'out': out_count
        })
    return slots, slot_counts

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
