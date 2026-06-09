"""ASGI config for the HRMS project."""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'hrms_project.settings')

application = get_asgi_application()
