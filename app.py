from flask import Flask, render_template, jsonify
import requests
import time

app = Flask(__name__)

CLOUDFLARE_URL = "https://rose-commodity-why-morrison.trycloudflare.com"

@app.route('/')
def index():
    return "Server is running!"

@app.route('/check')
def check():
    return render_template('check.html')

@app.route('/api/check', methods=['POST'])
def api_check():
    try:
        # PC側に「check?」を送信
        start_time = time.time()
        response = requests.get(
            f"{CLOUDFLARE_URL}/receive_check",
            timeout=5
        )
        elapsed_time = time.time() - start_time
        
        # レスポンスが「yes!」かチェック
        if response.status_code == 200 and response.text.strip() == "yes!":
            return jsonify({
                "status": "success",
                "message": "チェック完了",
                "response_time": f"{elapsed_time:.2f}秒"
            })
        else:
            return jsonify({
                "status": "error",
                "message": "チェック失敗（予期しないレスポンス）"
            })
    
    except requests.exceptions.Timeout:
        return jsonify({
            "status": "error",
            "message": "チェック失敗（タイムアウト）"
        })
    
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"チェック失敗（エラー: {str(e)}）"
        })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)