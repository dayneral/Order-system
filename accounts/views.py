from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.db.models import Case, IntegerField, Value, When
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.decorators.http import require_POST

from . import services
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
        form = RegisterForm(request.POST)
        if form.is_valid():
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
