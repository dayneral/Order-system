from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.db.models import Case, IntegerField, Value, When
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import services, throttle
from .decorators import admin_required
from .forms import RegisterForm, SignInForm
from .models import User


class SignInView(auth_views.LoginView):
    template_name = "accounts/login.html"
    authentication_form = SignInForm
    redirect_authenticated_user = True


def register(request):
    if request.user.is_authenticated:
        return redirect("home")
    if request.method == "POST":
        ip = throttle.client_ip(request)
        if throttle.blocked("register-ip", ip):
            messages.error(request, "Too many account requests from this connection. Please try again in an hour.")
            return render(request, "accounts/register.html", {"form": RegisterForm()}, status=429)
        throttle.hit("register-ip", ip)
        form = RegisterForm(request.POST)
        if form.is_valid():
            if form.email_taken:
                # Same response as a new request, so the page never reveals who has an account.
                services.notify_existing_account(form.cleaned_data["email"])
            else:
                user = form.save()
                services.notify_admins_of_signup(user)
            return redirect("accounts:register_done")
    else:
        form = RegisterForm()
    return render(request, "accounts/register.html", {"form": form})


def register_done(request):
    return render(request, "accounts/register_done.html")


class ForgotPasswordView(auth_views.PasswordResetView):
    template_name = "accounts/password_reset_form.html"
    email_template_name = "accounts/password_reset_email.txt"
    subject_template_name = "accounts/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")

    def form_valid(self, form):
        """Limit reset emails per connection and per address. When over the limit the
        usual "check your email" page is still shown, but nothing is sent."""
        ip, email = throttle.client_ip(self.request), form.cleaned_data["email"]
        if throttle.blocked("reset-ip", ip) or throttle.blocked("reset-email", email):
            return redirect(self.success_url)
        throttle.hit("reset-ip", ip)
        throttle.hit("reset-email", email)
        return super().form_valid(form)


class ResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    success_url = reverse_lazy("accounts:password_reset_complete")


# --- Admin: user management --------------------------------------------------

@admin_required
def user_list(request):
    status_order = Case(
        When(status=User.Status.PENDING, then=Value(0)),
        When(status=User.Status.APPROVED, then=Value(1)),
        default=Value(2),
        output_field=IntegerField(),
    )
    users = User.objects.annotate(status_order=status_order).order_by("status_order", "full_name")
    return render(request, "staff/user_list.html", {"users": users})


@admin_required
@require_POST
def user_action(request, user_id):
    user = get_object_or_404(User, pk=user_id)
    action = request.POST.get("action")
    try:
        if action == "approve":
            services.approve(user, request.user)
            messages.success(request, f"{user.full_name} is approved and can now sign in.")
        elif action == "reject":
            services.reject(user, request.user)
            messages.success(request, f"Account request from {user.full_name} rejected.")
        elif action == "disable":
            services.disable(user, request.user)
            messages.success(request, f"{user.full_name} has been disabled and can no longer sign in.")
        elif action == "reactivate":
            services.approve(user, request.user)
            messages.success(request, f"{user.full_name} has been re-enabled.")
        elif action in ("make_admin", "make_ordering"):
            role = User.Role.ADMIN if action == "make_admin" else User.Role.ORDERING
            services.set_role(user, role, request.user)
            messages.success(request, f"{user.full_name} is now: {user.get_role_display()}.")
        elif action in ("grant_orders", "revoke_orders", "grant_voids", "revoke_voids"):
            allowed, module = action.split("_")
            services.set_module_access(user, module, allowed == "grant", request.user)
            label = services.MODULES[module][1]
            messages.success(request, f"{user.full_name}: {label} access {'on' if allowed == 'grant' else 'off'}.")
        elif action == "send_reset":
            link, sent = services.send_password_reset(user, request.user, request)
            if sent:
                messages.success(request, f"A password reset link has been emailed to {user.email}.")
            else:
                messages.warning(
                    request,
                    f"The email could not be sent. Give {user.full_name} this one-time link instead "
                    f"(valid for 3 days): {link}",
                )
        else:
            messages.error(request, "Unknown action.")
    except services.AccountActionError as exc:
        messages.error(request, str(exc))
    return redirect("accounts:user_list")


@login_required
@require_POST
def display(request):
    """Save the signed-in user's Display options and go back to the page they were on."""
    user = request.user
    for field, choices in (("display_background", User.Background), ("display_font", User.Font),
                           ("display_text_size", User.TextSize)):
        value = request.POST.get(field)
        if value in choices.values:
            setattr(user, field, value)
    user.save(update_fields=["display_background", "display_font", "display_text_size"])
    back = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        back = "/"
    return redirect(back)
