from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from . import models as m


class BaseAdmin(SimpleHistoryAdmin):
    readonly_fields = ("created_at", "updated_at")


# --- catalogues -------------------------------------------------------------

@admin.register(m.Genotype)
class GenotypeAdmin(BaseAdmin):
    list_display = ("code", "name")


@admin.register(m.Condition)
class ConditionAdmin(BaseAdmin):
    list_display = ("code", "genotype", "description")
    list_filter = ("genotype",)
    search_fields = ("code", "description")


@admin.register(m.Protocol)
class ProtocolAdmin(BaseAdmin):
    list_display = ("code", "name")


@admin.register(m.Rig)
class RigAdmin(BaseAdmin):
    list_display = ("code", "incoming_dir")


@admin.register(m.Camera)
class CameraAdmin(BaseAdmin):
    list_display = ("code", "model", "serial", "usual_rig")


@admin.register(m.ArenaLayout)
class ArenaLayoutAdmin(BaseAdmin):
    list_display = ("code", "rows", "cols", "n_arenas", "arena_diameter_mm", "zone_list")

    @admin.display(description="Zones")
    def zone_list(self, obj):
        return ", ".join(obj.zone_names())


@admin.register(m.EventKind)
class EventKindAdmin(BaseAdmin):
    list_display = ("code", "name", "is_interval")


@admin.register(m.Experiment)
class ExperimentAdmin(BaseAdmin):
    list_display = ("code", "name", "status", "owner", "start_date")
    list_filter = ("status",)
    search_fields = ("code", "name")


# --- recording ----------------------------------------------------------------

class ArenaInline(admin.TabularInline):
    model = m.Arena
    extra = 0
    fields = ("index", "position", "status", "exclusion_reason", "window_start_s", "window_end_s", "fly_groups_summary")
    readonly_fields = ("position", "fly_groups_summary")
    show_change_link = True

    @admin.display(description="Row, col")
    def position(self, obj):
        return "%d, %d" % obj.position if obj.pk else "—"

    @admin.display(description="Flies")
    def fly_groups_summary(self, obj):
        if not obj.pk:
            return "—"
        return "; ".join(f"{g.zone}: {g.condition} {g.sex} n={g.n_flies or '?'}" for g in obj.fly_groups.all()) or "—"


class EventInline(admin.TabularInline):
    model = m.Event
    extra = 0
    fields = ("kind", "t_seconds", "duration_s", "arena", "params", "notes")

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "arena":
            object_id = request.resolver_match.kwargs.get("object_id")
            kwargs["queryset"] = m.Arena.objects.filter(recording_id=object_id) if object_id else m.Arena.objects.none()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(m.Recording)
class RecordingAdmin(BaseAdmin):
    list_display = ("code", "experiment", "condition", "layout", "started_at", "status")
    list_filter = ("status", "experiment", "condition", "rig", "layout")
    search_fields = ("code", "technical_notes", "behavior_notes")
    date_hierarchy = "started_at"
    readonly_fields = ("code", "created_at", "updated_at")
    fieldsets = [
        ("Experiment details", {"fields": ["code", "experiment", "protocol", "operator", "rig", "camera", "layout",
                                           "started_at", "status", "rejection_reason"]}),
        ("Flies (copied to every arena)", {"fields": ["condition", "sex", "n_flies", "cross_date", "sorting_date",
                                                      "incubator"]}),
        ("Analysis window (copied to every arena)", {"fields": ["window_start_s", "window_end_s"]}),
        ("Notes", {"fields": ["technical_notes", "behavior_notes"]}),
    ]
    inlines = [ArenaInline, EventInline]

    def save_model(self, request, obj, form, change):
        if not obj.operator_id:
            obj.operator = request.user
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        form.instance.populate_arenas()


class FlyGroupInline(admin.TabularInline):
    model = m.FlyGroup
    extra = 0
    fields = ("zone", "condition", "sex", "n_flies", "notes")


@admin.register(m.Arena)
class ArenaAdmin(BaseAdmin):
    list_display = ("__str__", "status", "window_start_s", "window_end_s")
    list_filter = ("status", "recording__experiment")
    search_fields = ("recording__code",)
    inlines = [FlyGroupInline]


@admin.register(m.Event)
class EventAdmin(BaseAdmin):
    list_display = ("recording", "kind", "t_seconds", "duration_s", "arena")
    list_filter = ("kind",)
