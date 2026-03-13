from flask import Flask, render_template, request, jsonify, send_file, send_from_directory

app = Flask(__name__)

# existing routes
@app.route('/')
def home():
    return render_template("index.html")

# Monetag verification/service worker
@app.route('/sw.js')
def monetag_sw():
    return send_from_directory('static', 'sw.js')
