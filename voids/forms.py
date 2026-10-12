from django import forms

from .models import Area, JobType, Supervisor, TradeTask, Void, VoidUpdate

DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class VoidForm(forms.ModelForm):
    class Meta:
        model = Void
        fields = ["key_info", "job_type", "simpro", "address", "postcode", "area", "supervisor", "access", "keys_received",
                  "start_date", "handback_date", "status", "status_fixed", "status_note",
                  "electrical_flag", "issue_flag", "issue_note", "on_hold", "last_inspection"]
        widgets = {"keys_received": DATE, "start_date": DATE, "handback_date": DATE,
                   "last_inspection": DATE,
                   "key_info": forms.Textarea(attrs={"rows": 3, "placeholder": "e.g. Key safe 1234 · Asbestos on chimney · Dog next door"})}
        help_texts = {
            "area": "Leave blank to fill in from the postcode.",
            "supervisor": "Leave blank to fill in from the postcode (the supervisor's patch).",
            "handback_date": "The client's nominated hand-back date (expected completion).",
            "status_fixed": "Tick to keep the status you choose. Otherwise it is worked out from the trades.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["supervisor"].queryset = Supervisor.objects.filter(active=True)


class TaskForm(forms.ModelForm):
    comment = forms.CharField(required=False, label="Comment for the job diary (optional)",
                              widget=forms.TextInput(attrs={"placeholder": "e.g. Waiting on parts"}))

    class Meta:
        model = TradeTask
        fields = ["status", "assignee", "date", "claimed", "notes"]
        widgets = {"date": DATE, "notes": forms.Textarea(attrs={"rows": 2})}


class UpdateForm(forms.Form):
    kind = forms.ChoiceField(label="Type", initial="general",
                             choices=[(k, label) for k, label in VoidUpdate.Kind.choices if k in VoidUpdate.WRITTEN_KINDS])
    text = forms.CharField(label="Update", max_length=2000, widget=forms.Textarea(
        attrs={"rows": 2, "placeholder": "What's happened? e.g. Visited – strip-out done, plasterer booked Thurs"}))


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
        ("", "Any"), ("stale", "No update in 7+ days"), ("stale_amber", "Needs an update (amber or red)"),
        ("stale_red", "Needs an update (red)"), ("on_hold", "On hold"), ("electrical", "Electrical works"), ("issue", "Issue")])
    sort = forms.ChoiceField(required=False, label="Sort", choices=[
        ("", "Job number (lowest first)"), ("handback", "Hand-back date"),
        ("start", "Works start"), ("stale", "Least recently updated"), ("recent", "Most recently updated"),
        ("address", "Address")])
