from django import forms

from .models import Area, JobType, Supervisor, TradeTask, Void

DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class VoidForm(forms.ModelForm):
    class Meta:
        model = Void
        fields = ["job_type", "simpro", "address", "postcode", "area", "supervisor", "access", "keys_received",
                  "handback_date", "expected_completion", "status", "status_fixed", "status_note",
                  "electrical_flag", "issue_flag", "last_inspection", "notes"]
        widgets = {"keys_received": DATE, "handback_date": DATE, "expected_completion": DATE,
                   "last_inspection": DATE, "notes": forms.Textarea(attrs={"rows": 4})}
        help_texts = {
            "area": "Leave blank to fill in from the postcode.",
            "supervisor": "Leave blank to fill in from the postcode (the supervisor's patch).",
            "status_fixed": "Tick to keep the status you choose. Otherwise it is worked out from the trades.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["supervisor"].queryset = Supervisor.objects.filter(active=True)


class TaskForm(forms.ModelForm):
    class Meta:
        model = TradeTask
        fields = ["status", "assignee", "date", "claimed", "notes"]
        widgets = {"date": DATE, "notes": forms.Textarea(attrs={"rows": 2})}


class UploadForm(forms.Form):
    file = forms.FileField(label="Void tracker (.xlsx)")

    def clean_file(self):
        f = self.cleaned_data["file"]
        if not f.name.lower().endswith((".xlsx", ".xlsm")):
            raise forms.ValidationError("Choose an Excel .xlsx file.")
        if f.size > 20 * 1024 * 1024:
            raise forms.ValidationError("That file is larger than 20 MB.")
        return f


class FilterForm(forms.Form):
    q = forms.CharField(required=False, label="Search", widget=forms.TextInput(
        attrs={"placeholder": "Address, postcode, SIMPRO"}))
    job = forms.ModelChoiceField(JobType.objects.all(), required=False, empty_label="All job types",
                                 label="Job type")
    area = forms.ModelChoiceField(Area.objects.all(), required=False, empty_label="All areas", label="Area")
    supervisor = forms.ModelChoiceField(Supervisor.objects.all(), required=False, empty_label="All supervisors",
                                        label="Supervisor")
    status = forms.ChoiceField(required=False, label="Status", choices=[
        ("", "All open"), *Void.Status.choices, ("all", "Everything")])
    trade = forms.CharField(required=False, label="Trade outstanding")
    due = forms.ChoiceField(required=False, label="Hand-back", choices=[
        ("", "Any date"), ("overdue", "Overdue"), ("week", "Due in 7 days"), ("none", "No date")])
    flag = forms.ChoiceField(required=False, label="Flag", choices=[
        ("", "Any"), ("electrical", "Electrical"), ("issue", "Issue"), ("late", "Expected after hand-back")])
