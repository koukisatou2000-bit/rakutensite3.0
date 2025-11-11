import os

timeout = 120
worker_class = 'eventlet'  # sync → eventlet
workers = 2
bind = f"0.0.0.0:{os.getenv('PORT', '5000')}"
loglevel = 'info'
accesslog = '-'
errorlog = '-'