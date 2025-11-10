import os

# ワーカータイムアウトを大幅に延長
timeout = 300

# geventワーカーを使用
worker_class = 'gevent'

# ワーカー数
workers = 1

# バインド
bind = f"0.0.0.0:{os.getenv('PORT', '5000')}"

# ログレベル
loglevel = 'info'

# アクセスログ
accesslog = '-'

# エラーログ
errorlog = '-'