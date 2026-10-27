from django.urls import path

from . import api

app_name = "api"
urlpatterns = [
    path("runs/claim", api.claim, name="claim"),
    path("runs/<int:pk>/heartbeat", api.heartbeat, name="heartbeat"),
    path("runs/<int:pk>/complete", api.complete, name="complete"),
    path("runs/<int:pk>/fail", api.fail, name="fail"),
]
