import os

timeout = 300
worker_class = 'eventlet'
workers = 1
bind = f"0.0.0.0:{os.getenv('PORT', '5000')}"
loglevel = 'info'
accesslog = '-'
errorlog = '-'