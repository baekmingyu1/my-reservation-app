import os
from dotenv import load_dotenv
import psycopg2
from flask import Flask, render_template, request, redirect
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta
from pytz import timezone
import traceback

# 💡 환경 변수 로드
env_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(dotenv_path=env_path)

DATABASE_URL = os.getenv("DATABASE_URL")
SECRET_KEY = os.getenv("SECRET_KEY", "default-secret")

app = Flask(__name__)
app.secret_key = SECRET_KEY

# ✅ 한국 시간 가져오기 (KST)
def get_kst_now():
    return datetime.now(timezone("Asia/Seoul"))

# ✅ DB 초기화
def init_db():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()

        # 예약 테이블
        cur.execute("""
                    CREATE TABLE IF NOT EXISTS reservations (
                                                                id SERIAL PRIMARY KEY,
                                                                name TEXT NOT NULL,
                                                                timeslot TEXT NOT NULL,
                                                                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    """)

        # 설정 테이블
        cur.execute("""
                    CREATE TABLE IF NOT EXISTS settings (
                                                            key TEXT PRIMARY KEY,
                                                            value TEXT NOT NULL
                    );
                    """)

        # 기본 예약 오픈 시간 삽입 (중복 시 무시)
        cur.execute("""
                    INSERT INTO settings (key, value)
                    VALUES ('reservation_open_time', '2025-05-25 09:00')
                        ON CONFLICT (key) DO NOTHING;
                    """)

        conn.commit()
        cur.close()
        conn.close()
        print("✅ DB 초기화 완료")
    except Exception:
        print("❌ DB 연결 실패:")
        traceback.print_exc()

init_db()

# 설정된 예약 오픈 시간 가져오기
def get_reservation_open_time():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key = 'reservation_open_time'")
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row:
        return datetime.strptime(row[0], '%Y-%m-%d %H:%M')
    return None

# 시간대 생성 함수
def generate_timeslots():
    base_time = datetime(2025, 5, 25, 10, 0)
    all_slots = [(base_time + timedelta(minutes=5 * i)) for i in range(60)]
    return [
        t.strftime('%Y-%m-%d %H:%M')
        for t in all_slots
        if not (datetime(2025, 5, 25, 11, 0) <= t < datetime(2025, 5, 25, 12, 30))
    ]

# 메인 예약 페이지
@app.route('/', methods=['GET', 'POST'])
def index():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    message = None
    open_time = get_reservation_open_time()
    now = get_kst_now()  # 한국 시간 기준

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

# 내 예약 확인
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

# 예약 취소
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

# 관리자 페이지
@app.route('/admin')
def admin():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("SELECT timeslot, name FROM reservations ORDER BY timeslot")
    rows = cur.fetchall()
    grouped = {}
    for time, name in rows:
        grouped.setdefault(time, []).append(name)
    cur.execute("SELECT value FROM settings WHERE key = 'reservation_open_time'")
    open_time = cur.fetchone()[0] if cur.rowcount else "설정 안 됨"
    cur.close()
    conn.close()
    return render_template('admin.html', grouped=grouped, open_time=open_time)

# 오픈 시간 설정
@app.route('/admin/set_open_time', methods=['POST'])
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

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
