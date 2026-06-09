"""Settings used only by the local smoke test (SQLite, no MySQL needed)."""
from .settings import *  # noqa: F401,F403

ALLOWED_HOSTS = ['*']

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'smoke_test.sqlite3',  # noqa: F405
    }
}
