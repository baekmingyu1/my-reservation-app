from flask import Flask, render_template, request, redirect
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta

app = Flask(__name__)
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

# 시간대 생성 함수
def generate_timeslots():
    base_time = datetime(2025, 5, 25, 10, 0)
    return [(base_time + timedelta(minutes=5 * i)).strftime('%Y-%m-%d %H:%M') for i in range(60)]

@app.route('/', methods=['GET', 'POST'])
def index():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    cur = conn.cursor()

    if request.method == 'POST':
        name = request.form.get('name')
        timeslot = request.form.get('timeslot')
        cur.execute("SELECT COUNT(*) FROM reservations WHERE name = %s", (name,))
        if cur.fetchone()['count'] == 0:
            cur.execute("SELECT COUNT(*) FROM reservations WHERE timeslot = %s", (timeslot,))
            if cur.fetchone()['count'] < 3:
                cur.execute("INSERT INTO reservations (name, timeslot) VALUES (%s, %s)", (name, timeslot))
                conn.commit()

    timeslots = generate_timeslots()
    slot_status = []
    for t in timeslots:
        cur.execute("SELECT COUNT(*) FROM reservations WHERE timeslot = %s", (t,))
        count = cur.fetchone()['count']
        full = count >= 3
        slot_status.append({ 'time': t, 'count': count, 'full': full })

    cur.close()
    conn.close()
    return render_template('index.html', timeslots=slot_status)

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
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    cur = conn.cursor()
    cur.execute("SELECT name, timeslot FROM reservations ORDER BY timeslot")
    reservations = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('admin.html', reservations=reservations)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)