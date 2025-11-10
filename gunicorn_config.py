import os

timeout = 180
worker_class = 'sync'
workers = 2
threads = 8
bind = f"0.0.0.0:{os.getenv('PORT', '5000')}"
loglevel = 'info'
accesslog = '-'
errorlog = '-'