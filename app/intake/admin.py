from django.contrib import admin

from .models import IncomingVideo


@admin.register(IncomingVideo)
class IncomingVideoAdmin(admin.ModelAdmin):
    list_display = ("file_name", "rig", "status", "camera_serial", "started_at", "size_bytes", "recording")
    list_filter = ("status", "rig")
    search_fields = ("file_name", "camera_serial")
    readonly_fields = ("rel_path", "rig", "file_name", "size_bytes", "mtime", "changed_at", "first_seen_at",
                       "camera_model", "camera_serial", "started_at", "recording")
