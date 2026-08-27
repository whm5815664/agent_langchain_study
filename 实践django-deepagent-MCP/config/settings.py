"""
Django settings for config project（无数据库）。
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.getenv(
    "DJANGO_SECRET_KEY",
    "django-insecure-f*t*!%gyren&fb=d*akl2ks0*fm6oyuckfqs!%q682mx$zf8l2",
)

DEBUG = os.getenv("DJANGO_DEBUG", "1") == "1"

ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "channels",
    "mcp",
    "chat",
]

ASGI_APPLICATION = "config.asgi.application"

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}

# CSRF：HTTPS 启动脚本会注入 DJANGO_CSRF_TRUSTED_ORIGINS（含局域网 IP）
_csrf_origins = os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").strip()
if _csrf_origins:
    CSRF_TRUSTED_ORIGINS = [
        item.strip() for item in _csrf_origins.split(",") if item.strip()
    ]
else:
    CSRF_TRUSTED_ORIGINS = [
        "https://127.0.0.1:8000",
        "https://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    ]

# 反向代理终止 HTTPS 后转发时，按转发头识别安全连接
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True


MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# 不使用数据库
DATABASES = {}

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [
    BASE_DIR / "mcp" / "server" / "static",
]
# 开发态：无需 collectstatic，由 WhiteNoise 走 finders（避免 uvicorn 慢速流式静态）
WHITENOISE_USE_FINDERS = DEBUG
WHITENOISE_AUTOREFRESH = DEBUG

# LLM（OpenAI 兼容接口）
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.7"))
