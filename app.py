from flask import Flask, render_template, request, redirect, session, url_for
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta
if os.environ.get("RENDER") != "true":
    from dotenv import load_dotenv
    load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "default-secret")
DATABASE_URL = os.environ.get("DATABASE_URL")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "default-secret")
DATABASE_URL = os.environ.get('DATABASE_URL')

# DB 초기화

def init_db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    cur = conn.cursor()
    cur.execute("""
                CREATE TABLE IF NOT EXISTS reservations (
                                                            id SERIAL PRIMARY KEY,
                                                            name TEXT NOT NULL,
                                                            timeslot TEXT NOT NULL,
                                                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """)
    conn.commit()
    cur.close()
    conn.close()

init_db()

# 시간대 생성 함수 (11:00~12:30 제외)

def generate_timeslots():
    base_time = datetime(2025, 5, 25, 10, 0)
    all_slots = [(base_time + timedelta(minutes=5 * i)) for i in range(60)]
    filtered_slots = [t for t in all_slots if not (datetime(2025, 5, 25, 11, 0) <= t < datetime(2025, 5, 25, 12, 30))]
    return [t.strftime('%Y-%m-%d %H:%M') for t in filtered_slots]

@app.route('/', methods=['GET', 'POST'])
def index():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    cur = conn.cursor()
    message = None

    if request.method == 'POST':
        name = request.form.get('name')
        timeslot = request.form.get('timeslot')
        cur.execute("SELECT COUNT(*) FROM reservations WHERE name = %s", (name,))
        if cur.fetchone()['count'] == 0:
            cur.execute("SELECT COUNT(*) FROM reservations WHERE timeslot = %s", (timeslot,))
            if cur.fetchone()['count'] < 3:
                cur.execute("INSERT INTO reservations (name, timeslot) VALUES (%s, %s)", (name, timeslot))
                conn.commit()
                message = f"{name}님, {timeslot} 예약이 완료되었습니다."
            else:
                message = "해당 시간대는 예약이 마감되었습니다."
        else:
            message = "이미 예약한 이름입니다."

    timeslots = generate_timeslots()
    slot_status = []
    for t in timeslots:
        cur.execute("SELECT COUNT(*) FROM reservations WHERE timeslot = %s", (t,))
        count = cur.fetchone()['count']
        full = count >= 3
        slot_status.append({ 'time': t, 'count': count, 'full': full })

    cur.close()
    conn.close()
    return render_template('index.html', timeslots=slot_status, message=message)

@app.route('/my', methods=['GET', 'POST'])
def my():
    reservations = []
    if request.method == 'POST':
        name = request.form.get('name')
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        cur = conn.cursor()
        cur.execute("SELECT timeslot, created_at FROM reservations WHERE name = %s ORDER BY timeslot", (name,))
        reservations = cur.fetchall()
        cur.close()
        conn.close()
    return render_template('my.html', reservations=reservations)

@app.route('/cancel', methods=['GET', 'POST'])
def cancel():
    message = None
    if request.method == 'POST':
        name = request.form.get('name')
        timeslot = request.form.get('timeslot')
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        cur = conn.cursor()
        cur.execute("DELETE FROM reservations WHERE name = %s AND timeslot = %s", (name, timeslot))
        deleted = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        message = "예약이 취소되었습니다." if deleted else "예약 내역이 없습니다."
    return render_template('cancel.html', message=message)

@app.route('/admin')
def admin():
    if not session.get("admin"):
        return redirect(url_for("login"))
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    cur = conn.cursor()
    cur.execute("SELECT name, timeslot FROM reservations ORDER BY timeslot")
    reservations = cur.fetchall()
    cur.close()
    conn.close()
    grouped = {}
    for r in reservations:
        grouped.setdefault(r['timeslot'], []).append(r['name'])
    return render_template('admin.html', grouped=grouped)

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get("username")
        password = request.form.get("password")
        if username == "admin" and password == os.environ.get("ADMIN_PASSWORD"):
            session['admin'] = True
            return redirect(url_for("admin"))
        else:
            error = "로그인 실패: 관리자만 접근할 수 있습니다."
    return render_template("login.html", error=error)

@app.route('/logout')
def logout():
    session.pop('admin', None)
    return redirect('/')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)