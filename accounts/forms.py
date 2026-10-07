from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm

from .models import User


class SignInForm(AuthenticationForm):
    username = forms.EmailField(label="Email address", widget=forms.EmailInput(attrs={"autofocus": True}))

    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": "Email address or password not recognised.",
        "pending": "Your account is waiting for an admin to approve it. You will be able to sign in once it is approved.",
        "blocked": "This account cannot sign in. Please contact an admin.",
    }

    def clean(self):
        try:
            return super().clean()
        except forms.ValidationError:
            # Explain why a correct password was refused for a pending or
            # disabled account. Wrong passwords always get the generic message.
            email = (self.cleaned_data.get("username") or "").lower()
            password = self.cleaned_data.get("password")
            user = User.objects.filter(email__iexact=email).first()
            if user and password and user.check_password(password):
                if user.status == User.Status.PENDING:
                    raise forms.ValidationError(self.error_messages["pending"], code="pending")
                raise forms.ValidationError(self.error_messages["blocked"], code="blocked")
            raise


class RegisterForm(forms.ModelForm):
    password1 = forms.CharField(label="Password", widget=forms.PasswordInput, strip=False,
                                help_text=password_validation.password_validators_help_text_html())
    password2 = forms.CharField(label="Confirm password", widget=forms.PasswordInput, strip=False)

    class Meta:
        model = User
        fields = ["full_name", "email"]
        labels = {"full_name": "Full name", "email": "Work email address"}
        help_texts = {"full_name": "Your first name and surname. This appears on every order you place."}

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email address already exists.")
        return email

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "The two passwords do not match.")
        elif p1:
            candidate = User(email=cleaned.get("email", ""), full_name=cleaned.get("full_name", ""))
            try:
                password_validation.validate_password(p1, candidate)
            except forms.ValidationError as exc:
                self.add_error("password1", exc)
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.status = User.Status.PENDING
        user.role = User.Role.ORDERING
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user
