from waitress import serve
from flask import Flask, send_file
import os

app = Flask(__name__)

@app.route('/')
def index():
    return send_file(os.path.join(os.path.dirname(__file__), '..', 'preview', 'competitor.html'))

if __name__ == '__main__':
    print('Preview server running on http://localhost:5002')
    serve(app, host='0.0.0.0', port=5002)