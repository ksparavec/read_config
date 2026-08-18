# Minimal AWX settings for the live suite. Only the read API is exercised, so
# this configures the database, the redis broker, and enough of AWX's paths to
# let `awx-manage migrate` and the web service start.
import os

ADMINS = ()
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'awx',
        'USER': 'awx',
        'PASSWORD': 'awx',
        'HOST': 'pg',
        'PORT': 5432,
    }
}
BROKER_URL = 'redis://:rcpass@cache:6379/2'
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
CLUSTER_HOST_ID = 'awx'
SECRET_KEY = 'rc-live-suite-awx-secret-key'
ALLOWED_HOSTS = ['*']
INTERNAL_API_URL = 'http://127.0.0.1:8052'
AWX_CLEANUP_PATHS = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
DEBUG = False
SERVER_EMAIL = 'root@localhost'
DEFAULT_FROM_EMAIL = 'awx@localhost'
STATIC_ROOT = '/var/lib/awx/public/static'
PROJECTS_ROOT = '/var/lib/awx/projects'
JOBOUTPUT_ROOT = '/var/lib/awx/job_status'
SECRET_KEY_FILE = None
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'awx.settings.production')
