from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("inbox/", include("intake.urls")),
    path("", RedirectView.as_view(url="/inbox/", permanent=False)),
]
