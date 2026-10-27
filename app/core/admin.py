from django.contrib import admin

from django.contrib import messages

from .models import Asset, AssetType, Run, Tool, Worker


@admin.register(Worker)
class WorkerAdmin(admin.ModelAdmin):
    list_display = ("name", "enabled", "tools", "last_seen_at")
    readonly_fields = ("last_seen_at",)
    actions = ["issue_token"]

    def save_model(self, request, obj, form, change):
        if not obj.token_hash:
            token = obj.new_token()
            messages.warning(request, f"Token for {obj.name} (shown only now, put it in the worker config): {token}")
        super().save_model(request, obj, form, change)

    @admin.action(description="Issue a new token (the old one stops working)")
    def issue_token(self, request, queryset):
        for w in queryset:
            token = w.new_token()
            w.save(update_fields=["token_hash"])
            messages.warning(request, f"New token for {w.name}: {token}")


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
    list_display = ("pk", "tool", "status", "recording", "arena", "worker", "attempts", "created_at", "finished_at")
    list_filter = ("status", "tool")
    search_fields = ("recording__code", "error")
    readonly_fields = ("tool", "params", "inputs", "recording", "arena", "worker", "created_at", "started_at",
                       "heartbeat_at", "finished_at", "attempts", "result", "log", "error")
    actions = ["requeue"]

    @admin.action(description="Run again (back to pending)")
    def requeue(self, request, queryset):
        n = queryset.filter(status__in=[Run.Status.FAILED, Run.Status.CANCELLED]).update(
            status=Run.Status.PENDING, worker="", error="", attempts=0)
        messages.info(request, f"{n} run(s) back in the queue.")
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
