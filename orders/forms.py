from django import forms
from django.utils import timezone

from .models import Order


class OrderHeaderForm(forms.ModelForm):
    """Order details. Fields are optional while drafting; submit checks they are complete."""

    class Meta:
        model = Order
        fields = ["job_number", "property_address", "property_type", "delivery_date", "special_instructions"]
        labels = {
            "job_number": "Job number",
            "property_address": "Property address",
            "property_type": "Property type",
            "delivery_date": "Requested delivery date",
            "special_instructions": "Special instructions (optional)",
        }
        widgets = {
            "property_address": forms.Textarea(attrs={"rows": 3}),
            "special_instructions": forms.Textarea(attrs={"rows": 3}),
            "property_type": forms.RadioSelect,
            "delivery_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.required = False
        self.fields["property_type"].choices = Order.PropertyType.choices
        self.fields["delivery_date"].widget.attrs["min"] = timezone.localdate().isoformat()
        self.fields["job_number"].widget.attrs["autofocus"] = True
