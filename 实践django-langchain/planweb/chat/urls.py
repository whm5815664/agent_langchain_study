from django.urls import path

from chat.agent.planServer import views as plan_views

from . import views

urlpatterns = [
    path("", views.index, name="chat_index"),
    path("api/chat/", views.chat_api, name="chat_api"),
    path("api/chat/stop/", views.chat_stop_api, name="chat_stop"),
    path("api/chat/refresh/", views.chat_refresh_api, name="chat_refresh"),
    path("plan/", plan_views.plan_page, name="plan_page"),
    path("api/plans/", plan_views.plans_api, name="plans_api"),
    path("api/plans/<str:plan_id>/pause/", plan_views.plan_pause_api, name="plan_pause"),
    path(
        "api/plans/<str:plan_id>/execute/",
        plan_views.plan_execute_api,
        name="plan_execute",
    ),
    path(
        "api/plans/<str:plan_id>/delete/",
        plan_views.plan_delete_api,
        name="plan_delete",
    ),
    path(
        "api/plans/<str:plan_id>/supplement/",
        plan_views.plan_supplement_api,
        name="plan_supplement",
    ),
]
