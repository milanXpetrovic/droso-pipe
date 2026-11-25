from django.urls import path

from . import views

app_name = "intake"
urlpatterns = [
    path("", views.inbox, name="inbox"),
    path("tracking/", views.tracking, name="tracking"),
    path("upload/", views.upload_page, name="upload"),
    path("upload/start/", views.upload_start, name="upload_start"),
    path("upload/<int:pk>/chunk/", views.upload_chunk, name="upload_chunk"),
    path("upload/<int:pk>/form/", views.upload_form, name="upload_form"),
    path("upload/<int:pk>/cancel/", views.upload_cancel, name="upload_cancel"),
    path("video/<int:pk>/", views.register, name="register"),
    path("video/<int:pk>/ignore/", views.ignore, name="ignore"),
    path("video/<int:pk>/frame.jpg", views.frame, name="frame"),
    path("video/<int:pk>/retry/", views.retry_ingest, name="retry"),
    path("recording/<str:code>/arenas/", views.arenas, name="arenas"),
]
