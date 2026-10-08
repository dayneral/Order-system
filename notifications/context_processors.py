from .models import OrderEmail


def failed_emails(request):
    """Number of failed order emails, shown to admins in the top bar."""
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated and user.is_admin:
        return {"failed_email_count": OrderEmail.objects.filter(status=OrderEmail.Status.FAILED).count()}
    return {}
