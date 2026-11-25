from django.urls import path

from . import views

app_name = "web"
urlpatterns = [
    path("", views.recordings, name="recordings"),
    path("recordings/<str:code>/", views.recording, name="recording"),
    path("recordings/<str:code>/delete/", views.recording_delete, name="recording_delete"),
    path("review/", views.review_next, name="review_next"),
    path("review/<int:pk>/", views.review_item, name="review_item"),
    path("review/<int:pk>/paths.svg", views.paths_svg, name="paths"),
    path("review/<int:pk>/frame.jpg", views.arena_frame, name="arena_frame"),
    path("experiments/", views.experiments, name="experiments"),
    path("experiments/new/", views.experiment_new, name="experiment_new"),
    path("conditions/new/", views.condition_new, name="condition_new"),
    path("machines/", views.machines, name="machines"),
    path("machines/runs/<int:pk>/retry/", views.run_retry, name="run_retry"),
    path("settings/", views.settings_page, name="settings"),
]
