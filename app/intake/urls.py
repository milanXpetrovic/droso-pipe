from django.urls import path

from . import views

app_name = "intake"
urlpatterns = [
    path("", views.inbox, name="inbox"),
    path("tracking/", views.tracking, name="tracking"),
    path("video/<int:pk>/", views.register, name="register"),
    path("video/<int:pk>/ignore/", views.ignore, name="ignore"),
    path("video/<int:pk>/frame.jpg", views.frame, name="frame"),
    path("video/<int:pk>/retry/", views.retry_ingest, name="retry"),
    path("recording/<str:code>/arenas/", views.arenas, name="arenas"),
]
