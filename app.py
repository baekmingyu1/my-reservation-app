import os
from dotenv import load_dotenv
import psycopg2
from flask import Flask, render_template, request, redirect, session, url_for
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta
from pytz import timezone
from functools import wraps
import traceback

env_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(dotenv_path=env_path)

DATABASE_URL = os.getenv("DATABASE_URL")
SECRET_KEY = os.getenv("SECRET_KEY", "default-secret")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "changeme")

app = Flask(__name__)
app.secret_key = SECRET_KEY

def get_kst_now():
    return datetime.now(timezone("Asia/Seoul"))

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def init_db():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("""
                    CREATE TABLE IF NOT EXISTS reservations (
                                                                id SERIAL PRIMARY KEY,
                                                                name TEXT NOT NULL,
                                                                timeslot TEXT NOT NULL,
                                                                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                                                                used BOOLEAN DEFAULT FALSE
                    );
                    """)
        cur.execute("ALTER TABLE reservations ADD COLUMN IF NOT EXISTS used BOOLEAN DEFAULT FALSE;")
        cur.execute("""
                    CREATE TABLE IF NOT EXISTS settings (
                                                            key TEXT PRIMARY KEY,
                                                            value TEXT NOT NULL
                    );
                    """)
        cur.execute("""
                    INSERT INTO settings (key, value)
                    VALUES ('reservation_open_time', '2025-05-25 09:00')
                        ON CONFLICT (key) DO NOTHING;
                    """)
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        traceback.print_exc()

init_db()

def get_reservation_open_time():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key = 'reservation_open_time'")
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row:
        naive = datetime.strptime(row[0], '%Y-%m-%d %H:%M')
        return timezone("Asia/Seoul").localize(naive)
    return None

def generate_timeslots():
    base_time = datetime(2025, 5, 25, 10, 0)
    all_slots = [(base_time + timedelta(minutes=5 * i)) for i in range(60)]
    return [
        t.strftime('%Y-%m-%d %H:%M')
        for t in all_slots
        if not (datetime(2025, 5, 25, 11, 0) <= t < datetime(2025, 5, 25, 12, 30))
    ]

@app.route('/', methods=['GET', 'POST'])
def index():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    message = None
    open_time = get_reservation_open_time()
    now = get_kst_now()

    if request.method == 'POST':
        name = request.form.get('name')
        timeslot = request.form.get('timeslot')
        if open_time and now < open_time:
            message = "⏳ 예약이 아직 열리지 않았습니다."
        else:
            cur.execute("SELECT COUNT(*) FROM reservations WHERE name = %s", (name,))
            if cur.fetchone()[0] == 0:
                cur.execute("SELECT COUNT(*) FROM reservations WHERE timeslot = %s", (timeslot,))
                if cur.fetchone()[0] < 3:
                    cur.execute("INSERT INTO reservations (name, timeslot) VALUES (%s, %s)", (name, timeslot))
                    conn.commit()
                    message = f"{name}님, {timeslot} 예약이 완료되었습니다."
                else:
                    message = "해당 시간대는 예약이 마감되었습니다."
            else:
                message = "이미 예약한 이름입니다."

    slots = []
    for t in generate_timeslots():
        cur.execute("SELECT COUNT(*) FROM reservations WHERE timeslot = %s", (t,))
        count = cur.fetchone()[0]
        slots.append({'time': t, 'count': count, 'full': count >= 3})

    cur.close()
    conn.close()
    return render_template('index.html', timeslots=slots, message=message)

@app.route('/my', methods=['GET', 'POST'])
def my():
    name = request.form.get('name')
    reservations = []

    if name:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM reservations WHERE name = %s ORDER BY created_at", (name,))
        reservations = cur.fetchall()
        cur.close()
        conn.close()

    return render_template("my.html", name=name, reservations=reservations)


# @app.route('/cancel', methods=['GET', 'POST'])
# def cancel():
#     message = None
#     if request.method == 'POST':
#         name = request.form.get('name')
#         timeslot = request.form.get('timeslot')
#         conn = psycopg2.connect(DATABASE_URL)
#         cur = conn.cursor()
#         cur.execute("DELETE FROM reservations WHERE name = %s AND timeslot = %s", (name, timeslot))
#         conn.commit()
#         cur.close()
#         conn.close()
#         message = f"{name}님의 {timeslot} 예약이 취소되었습니다."
#     return render_template('cancel.html', message=message)

@app.route('/cancel_reservation', methods=['POST'])
def cancel_reservation():
    name = request.form.get('name')
    timeslot = request.form.get('timeslot')

    message = ""
    reservations = []

    if name and timeslot:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("DELETE FROM reservations WHERE name = %s AND timeslot = %s", (name, timeslot))
        deleted = cur.rowcount
        conn.commit()

        cur.execute("SELECT * FROM reservations WHERE name = %s ORDER BY created_at", (name,))
        reservations = cur.fetchall()

        cur.close()
        conn.close()

        if deleted:
            message = f"✅ {name}님의 {timeslot} 예약이 취소되었습니다."
        else:
            message = "❌ 해당 예약을 찾을 수 없습니다."
    else:
        message = "❌ 이름 또는 시간 정보가 누락되었습니다."

    return render_template("my.html", message=message, name=name, reservations=reservations)




@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == 'admin' and password == ADMIN_PASSWORD:
            session['logged_in'] = True
            return redirect('/admin')
        else:
            error = "❌ 로그인 실패: 아이디 또는 비밀번호가 올바르지 않습니다."
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

@app.route('/admin', methods=['GET', 'POST'])
@login_required
def admin():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor(cursor_factory=RealDictCursor)

    if request.method == 'POST':
        ids = request.form.getlist('used_ids')
        cur.execute("UPDATE reservations SET used = FALSE")
        if ids:
            cur.execute("UPDATE reservations SET used = TRUE WHERE id = ANY(%s)", (list(map(int, ids)),))
        conn.commit()

    cur.execute("SELECT * FROM reservations ORDER BY timeslot, created_at")
    rows = cur.fetchall()
    grouped = {}
    for row in rows:
        grouped.setdefault(row['timeslot'], []).append(row)

    cur.execute("SELECT value FROM settings WHERE key = 'reservation_open_time'")
    row = cur.fetchone()
    open_time = row['value'] if row else "설정 안 됨"

    cur.close()
    conn.close()
    return render_template('admin.html', grouped=grouped, open_time=open_time)

@app.route('/admin/set_open_time', methods=['POST'])
@login_required
def set_open_time():
    new_time = request.form.get('open_time')
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("""
                INSERT INTO settings (key, value)
                VALUES ('reservation_open_time', %s)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """, (new_time,))
    conn.commit()
    cur.close()
    conn.close()
    return redirect('/admin')

@app.route('/admin/delete_reservation', methods=['POST'])
@login_required
def delete_reservation():
    reservation_id = request.form.get('reservation_id')
    if reservation_id:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("DELETE FROM reservations WHERE id = %s", (reservation_id,))
        conn.commit()
        cur.close()
        conn.close()
    return redirect('/admin')

@app.route('/admin/add_reservation', methods=['POST'])
@login_required
def admin_add_reservation():
    name = request.form.get('name')
    timeslot = request.form.get('timeslot')

    try:
        datetime.strptime(timeslot, "%Y-%m-%d %H:%M")
    except ValueError:
        print("❌ 잘못된 시간 형식:", timeslot)
        return redirect('/admin')

    if not name or not timeslot:
        return redirect('/admin')

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM reservations WHERE name = %s", (name,))
    if cur.fetchone()[0] == 0:
        cur.execute("SELECT COUNT(*) FROM reservations WHERE timeslot = %s", (timeslot,))
        if cur.fetchone()[0] < 3:
            cur.execute("INSERT INTO reservations (name, timeslot) VALUES (%s, %s)", (name, timeslot))
            conn.commit()
    cur.close()
    conn.close()
    return redirect('/admin')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
