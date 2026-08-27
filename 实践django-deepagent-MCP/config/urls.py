"""URL configuration for config project."""

from django.conf import settings
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.urls import include, path

urlpatterns = [
    path("", include("chat.urls")),
    path("", include("mcp.urls")),
]

# uvicorn 不会自动托管静态文件；开发模式下挂上 Django staticfiles
if settings.DEBUG:
    urlpatterns += staticfiles_urlpatterns()
