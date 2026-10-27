from django.urls import path

from . import views

app_name = "intake"
urlpatterns = [
    path("", views.inbox, name="inbox"),
    path("video/<int:pk>/", views.register, name="register"),
    path("video/<int:pk>/ignore/", views.ignore, name="ignore"),
    path("recording/<str:code>/arenas/", views.arenas, name="arenas"),
]
