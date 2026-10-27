from django import forms

from domain.models import Arena, Camera, FlyGroup, Recording

DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
DATETIME = forms.DateTimeInput(attrs={"type": "datetime-local", "step": "1"}, format="%Y-%m-%dT%H:%M:%S")

GROUPS = [
    ("Experiment", ["experiment", "protocol", "layout", "camera", "started_at"]),
    ("Flies (copied to every arena)", ["condition", "sex", "n_flies", "cross_date", "sorting_date", "incubator"]),
    ("Analysis window, seconds from video start (10 min = 600)", ["window_start_s", "window_end_s"]),
    ("Notes", ["technical_notes", "behavior_notes"]),
]


class RegisterForm(forms.ModelForm):
    """Recording metadata for one incoming video. Rig, and when the file name allows it, camera and start
    time come from the file; the operator fills in the rest."""

    class Meta:
        model = Recording
        fields = [f for _, names in GROUPS for f in names]
        widgets = {"started_at": DATETIME, "cross_date": DATE, "sorting_date": DATE,
                   "technical_notes": forms.Textarea(attrs={"rows": 3}),
                   "behavior_notes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, camera_from_file=None, started_from_file=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["camera"].queryset = Camera.objects.all()
        if camera_from_file is not None:
            self.fields["camera"].initial = camera_from_file
            self.fields["camera"].disabled = True
        if started_from_file is not None:
            self.fields["started_at"].initial = started_from_file
            self.fields["started_at"].disabled = True
            self.fields["started_at"].help_text = "From the file name."

    def grouped(self):
        return [(title, [self[name] for name in names]) for title, names in GROUPS]


class ArenaForm(forms.ModelForm):
    class Meta:
        model = Arena
        fields = ["status", "exclusion_reason", "window_start_s", "window_end_s"]
        widgets = {"exclusion_reason": forms.TextInput(attrs={"placeholder": "reason (optional)"})}


class FlyGroupForm(forms.ModelForm):
    class Meta:
        model = FlyGroup
        fields = ["condition", "sex", "n_flies"]


ArenaFormSet = forms.modelformset_factory(Arena, form=ArenaForm, extra=0)
FlyGroupFormSet = forms.modelformset_factory(FlyGroup, form=FlyGroupForm, extra=0)
