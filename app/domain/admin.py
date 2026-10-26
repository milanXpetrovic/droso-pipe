from django import forms
from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from . import models as m


class BaseAdmin(SimpleHistoryAdmin):
    """History button on every object; timestamps shown read-only."""
    readonly_fields = ("created_at", "updated_at")


# ---------------------------------------------------------------------------
# Catalogues
# ---------------------------------------------------------------------------

@admin.register(m.Genotype)
class GenotypeAdmin(BaseAdmin):
    list_display = ("code", "name", "source", "stock_id")
    search_fields = ("code", "name", "stock_id")


@admin.register(m.ManipulationKind)
class ManipulationKindAdmin(BaseAdmin):
    list_display = ("code", "name")


@admin.register(m.EventKind)
class EventKindAdmin(BaseAdmin):
    list_display = ("code", "name", "is_interval")


@admin.register(m.Rig)
class RigAdmin(BaseAdmin):
    list_display = ("code", "location")


@admin.register(m.Camera)
class CameraAdmin(BaseAdmin):
    list_display = ("serial", "model", "max_fps")
    search_fields = ("serial", "model")


@admin.register(m.ArenaLayout)
class ArenaLayoutAdmin(BaseAdmin):
    list_display = ("code", "n_arenas", "shape", "size_x_mm", "size_y_mm")


@admin.register(m.DamMonitor)
class DamMonitorAdmin(BaseAdmin):
    list_display = ("code", "model", "n_channels")


@admin.register(m.Incubator)
class IncubatorAdmin(BaseAdmin):
    list_display = ("code", "location")


@admin.register(m.SensorDevice)
class SensorDeviceAdmin(BaseAdmin):
    list_display = ("code", "kind", "rig", "incubator")


# ---------------------------------------------------------------------------
# Experimental design
# ---------------------------------------------------------------------------

@admin.register(m.Manipulation)
class ManipulationAdmin(BaseAdmin):
    list_display = ("code", "kind", "phase", "params")
    list_filter = ("kind", "phase")
    search_fields = ("code",)


class ConditionForm(forms.ModelForm):
    class Meta:
        model = m.Condition
        fields = "__all__"

    def clean(self):
        data = super().clean()
        genotype, manips = data.get("genotype"), data.get("manipulations")
        if genotype is not None and manips is not None:
            dup = self.instance.duplicates(genotype.pk, [x.pk for x in manips]).first()
            if dup:
                raise forms.ValidationError(f"This genotype + manipulations combination already exists as '{dup}'.")
        return data


@admin.register(m.Condition)
class ConditionAdmin(BaseAdmin):
    form = ConditionForm
    list_display = ("code", "genotype", "manipulation_list")
    list_filter = ("genotype",)
    search_fields = ("code",)
    filter_horizontal = ("manipulations",)

    @admin.display(description="Manipulations")
    def manipulation_list(self, obj):
        return ", ".join(x.code for x in obj.manipulations.all()) or "—"

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        form.instance.refresh_signature()


@admin.register(m.Experiment)
class ExperimentAdmin(BaseAdmin):
    list_display = ("code", "name", "status", "owner", "start_date")
    list_filter = ("status",)
    search_fields = ("code", "name")


@admin.register(m.Cohort)
class CohortAdmin(BaseAdmin):
    list_display = ("code", "genotype", "eclosion_from", "eclosion_to", "collected_on")
    list_filter = ("genotype",)
    search_fields = ("code",)


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------

class ArenaInline(admin.TabularInline):
    model = m.Arena
    extra = 0
    fields = ("index", "status", "exclusion_reason", "px_per_mm", "geometry", "fly_group_summary")
    readonly_fields = ("fly_group_summary",)
    show_change_link = True   # opens the arena with its fly groups

    @admin.display(description="Fly groups")
    def fly_group_summary(self, obj):
        return "; ".join(f"{g.zone}: {g.condition} {g.sex} n={g.n_flies or '?'}"
                         for g in obj.fly_groups.all()) if obj.pk else "—"


class EventInline(admin.TabularInline):
    model = m.Event
    extra = 0
    fields = ("kind", "t_seconds", "duration_s", "arena", "zone", "manipulation", "params", "notes")

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "arena":   # only this recording's arenas
            object_id = request.resolver_match.kwargs.get("object_id")
            kwargs["queryset"] = m.Arena.objects.filter(recording_id=object_id) if object_id else m.Arena.objects.none()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(m.Recording)
class RecordingAdmin(BaseAdmin):
    list_display = ("code", "experiment", "rig", "layout", "default_condition", "started_at", "status")
    list_filter = ("status", "rig", "experiment", "layout")
    search_fields = ("code", "notes")
    date_hierarchy = "started_at"
    readonly_fields = ("code", "created_at", "updated_at")
    inlines = [ArenaInline, EventInline]

    def save_model(self, request, obj, form, change):
        if not obj.operator_id:
            obj.operator = request.user
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        form.instance.populate_arenas()   # creates missing arenas and default fly groups


class FlyGroupInline(admin.TabularInline):
    model = m.FlyGroup
    extra = 0
    fields = ("zone", "condition", "cohort", "sex", "n_flies", "notes")


@admin.register(m.Arena)
class ArenaAdmin(BaseAdmin):
    list_display = ("__str__", "status", "px_per_mm")
    list_filter = ("status", "recording__experiment")
    search_fields = ("recording__code",)
    inlines = [FlyGroupInline]


@admin.register(m.FlyGroup)
class FlyGroupAdmin(BaseAdmin):
    list_display = ("__str__", "condition", "cohort", "sex", "n_flies")
    list_filter = ("condition", "sex", "arena__recording__experiment")


@admin.register(m.Event)
class EventAdmin(BaseAdmin):
    list_display = ("recording", "kind", "t_seconds", "duration_s", "arena", "manipulation")
    list_filter = ("kind",)


# ---------------------------------------------------------------------------
# Support data
# ---------------------------------------------------------------------------

class DamChannelInline(admin.TabularInline):
    model = m.DamChannel
    extra = 0
    fields = ("channel", "condition", "cohort", "sex", "status", "exclusion_reason", "fly_group")
    raw_id_fields = ("fly_group",)


@admin.register(m.DamRun)
class DamRunAdmin(BaseAdmin):
    list_display = ("code", "monitor", "incubator", "started_at", "ended_at", "light_regime")
    list_filter = ("monitor", "incubator")
    inlines = [DamChannelInline]


@admin.register(m.SensorReading)
class SensorReadingAdmin(admin.ModelAdmin):
    list_display = ("device", "ts", "temperature_c", "humidity_pct", "light_lux")
    list_filter = ("device",)
    date_hierarchy = "ts"
