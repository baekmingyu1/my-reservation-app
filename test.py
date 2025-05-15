import psycopg2

try:
    conn = psycopg2.connect("postgresql://postgres:1234@localhost:5432/ticket_db")
    print("✅ 연결 성공")
except Exception as e:
    print("❌ 연결 실패:", e)

