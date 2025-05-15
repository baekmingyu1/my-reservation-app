import os
from dotenv import load_dotenv
import psycopg2
from flask import Flask, render_template, request
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta
import traceback

# 💡 .env 강제 로드
env_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(dotenv_path=env_path)

DATABASE_URL = os.getenv("DATABASE_URL")
SECRET_KEY = os.getenv("SECRET_KEY", "default-secret")
print("📡 DB URL:", DATABASE_URL)

app = Flask(__name__)
app.secret_key = SECRET_KEY

# ✅ DB 초기화 함수
def init_db():
    print("DB URL 확인:", repr(DATABASE_URL))
    try:
        conn = psycopg2.connect(DATABASE_URL)
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
        print("✅ DB 연결 및 테이블 확인 완료")
    except Exception as e:
        print("❌ DB 연결 실패:")
        traceback.print_exc()

init_db()

# 시간대 생성 함수 (11:00~12:30 제외)
def generate_timeslots():
    base_time = datetime(2025, 5, 25, 10, 0)
    all_slots = [(base_time + timedelta(minutes=5 * i)) for i in range(60)]
    filtered = [t for t in all_slots if not (datetime(2025, 5, 25, 11, 0) <= t < datetime(2025, 5, 25, 12, 30))]
    return [t.strftime('%Y-%m-%d %H:%M') for t in filtered]

@app.route('/', methods=['GET', 'POST'])
def index():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    message = None

    if request.method == 'POST':
        name = request.form.get('name')
        timeslot = request.form.get('timeslot')
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
        slots.append({ 'time': t, 'count': count, 'full': count >= 3 })

    cur.close()
    conn.close()
    return render_template('index.html', timeslots=slots, message=message)

@app.route('/my', methods=['GET', 'POST'])
def my_reservations():
    reservations = []
    if request.method == 'POST':
        name = request.form.get('name')
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT timeslot FROM reservations WHERE name = %s", (name,))
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
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("DELETE FROM reservations WHERE name = %s AND timeslot = %s", (name, timeslot))
        conn.commit()
        cur.close()
        conn.close()
        message = f"{name}님의 {timeslot} 예약이 취소되었습니다."
    return render_template('cancel.html', message=message)

@app.route('/admin')
def admin():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("SELECT timeslot, name FROM reservations ORDER BY timeslot")
    rows = cur.fetchall()
    grouped = {}
    for time, name in rows:
        grouped.setdefault(time, []).append(name)
    cur.close()
    conn.close()
    return render_template('admin.html', grouped=grouped)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
