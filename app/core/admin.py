from django.contrib import admin

import tempfile
from pathlib import Path

from django import forms
from django.contrib import messages

from .flytracker import calibration_info, template_warnings
from .formats import FT_CALIBRATION
from .models import Asset, AssetType, Run, Tool, TrackerTemplate, Worker
from .uploads import store_upload


class TrackerTemplateForm(forms.ModelForm):
    calibration_file = forms.FileField(required=False,
                                       help_text="calibration.mat saved by the FlyTracker calibrator on one crop.")

    class Meta:
        model = TrackerTemplate
        fields = ["name", "layout", "mode", "active", "calibration_file", "min_fractional_arena_size",
                  "max_fractional_arena_size", "num_cores", "notes"]

    def clean(self):
        data = super().clean()
        f = data.get("calibration_file")
        if not self.instance.pk and not f:
            raise forms.ValidationError("Upload the calibration file.")
        if f:
            with tempfile.NamedTemporaryFile(suffix=".mat") as tmp:
                for chunk in f.chunks():
                    tmp.write(chunk)
                tmp.flush()
                try:
                    self.info = calibration_info(Path(tmp.name))
                except Exception as e:  # noqa: BLE001
                    raise forms.ValidationError(f"Not a FlyTracker calibration file: {e}")
            f.seek(0)
        return data


@admin.register(TrackerTemplate)
class TrackerTemplateAdmin(admin.ModelAdmin):
    form = TrackerTemplateForm
    list_display = ("name", "layout", "mode", "active", "scale", "has_warnings", "created_at")
    list_filter = ("layout", "mode", "active")
    readonly_fields = ("calibration", "warnings_text", "created_at", "created_by")

    @admin.display(description="px/mm")
    def scale(self, obj):
        return f"{obj.asset.meta.get('ppm', 0):.2f}" if obj.pk else "—"

    @admin.display(boolean=True, description="Warnings")
    def has_warnings(self, obj):
        return bool(obj.warnings)

    @admin.display(description="Calibration")
    def calibration(self, obj):
        if not obj.pk:
            return "—"
        m = obj.asset.meta
        return (f"{obj.asset.rel_path} · {m.get('ppm', 0):.2f} px/mm · radius {m.get('arena_r_px')} px · "
                f"{m.get('n_flies')} flies · {m.get('fps')} fps")

    @admin.display(description="Warnings")
    def warnings_text(self, obj):
        return " ".join(obj.warnings) if obj.warnings else "none"

    def save_model(self, request, obj, form, change):
        f = form.cleaned_data.get("calibration_file")
        if f:
            info = form.info
            obj.warnings = template_warnings(info, obj.mode, obj.layout.arena_diameter_mm)
            obj.asset = store_upload(f, "support/flytracker/templates", f"{obj.name}.mat", FT_CALIBRATION, info,
                                     request.user, warnings=obj.warnings)
            if not obj.created_by_id:
                obj.created_by = request.user
        super().save_model(request, obj, form, change)
        for w in obj.warnings:
            messages.warning(request, w)


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
