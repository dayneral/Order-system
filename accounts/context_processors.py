from .models import User


def pending_accounts(request):
    """Shows admins how many accounts are waiting for approval (top bar badge)."""
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated and user.is_admin:
        return {"pending_count": User.objects.filter(status=User.Status.PENDING).count()}
    return {}


def display_options(request):
    """Choices for the Display menu in the top bar."""
    from .models import User
    return {"display_backgrounds": User.Background.choices, "display_fonts": User.Font.choices,
            "display_sizes": User.TextSize.choices}
