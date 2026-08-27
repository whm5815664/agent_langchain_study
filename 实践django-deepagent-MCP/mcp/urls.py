from django.urls import path

from . import views

urlpatterns = [
    path("api/edge/status/", views.edge_status, name="edge_status"),
    path("api/mcp/control/", views.mcp_control, name="mcp_control"),
]
