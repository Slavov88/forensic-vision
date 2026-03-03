"""
Celery application factory for ForensicVision.
Loaded via the __init__.py so that `shared_task` works without explicit app ref.
"""
import os
from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "forensicvision.settings")

app = Celery("forensicvision")

# Load Celery settings from Django settings (namespace CELERY_)
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover tasks from all installed apps
app.autodiscover_tasks()
