from flask import Flask

app = Flask(__name__)

@app.route('/')
def index():
    return "🎉 Railway에서 Flask 앱이 실행 중입니다!"

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
