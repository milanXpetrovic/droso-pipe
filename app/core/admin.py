from django.contrib import admin

from .models import Asset, AssetType, Run, Tool


@admin.register(AssetType)
class AssetTypeAdmin(admin.ModelAdmin):
    list_display = ("__str__", "name")


@admin.register(Tool)
class ToolAdmin(admin.ModelAdmin):
    list_display = ("__str__", "description")


class OutputInline(admin.TabularInline):
    model = Asset
    fk_name = "produced_by"
    extra = 0
    can_delete = False
    fields = ("type", "rel_path", "size_bytes", "warnings")
    readonly_fields = fields


@admin.register(Run)
class RunAdmin(admin.ModelAdmin):
    list_display = ("pk", "tool", "status", "recording", "arena", "worker", "created_at", "finished_at")
    list_filter = ("status", "tool")
    search_fields = ("recording__code", "error")
    readonly_fields = ("tool", "params", "inputs", "recording", "arena", "worker", "created_at", "started_at",
                       "finished_at", "log", "error")
    inlines = [OutputInline]


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = ("rel_path", "type", "recording", "arena", "size_bytes", "status", "has_warnings", "created_at")
    list_filter = ("type", "status")
    search_fields = ("rel_path", "recording__code", "sha256")
    readonly_fields = ("type", "rel_path", "sha256", "size_bytes", "meta", "warnings", "recording", "arena",
                       "produced_by", "created_at")

    @admin.display(boolean=True, description="Warnings")
    def has_warnings(self, obj):
        return bool(obj.warnings)
