import os

timeout = 120
worker_class = 'sync'
workers = 1
threads = 4
bind = f"0.0.0.0:{os.getenv('PORT', '5000')}"
loglevel = 'info'
accesslog = '-'
errorlog = '-'