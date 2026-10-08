from django.contrib import messages
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.utils import timezone

from .services import draft_expiry, drafts_due_for_deletion


@receiver(user_logged_in)
def warn_about_expiring_drafts(sender, request, user, **kwargs):
    """On sign-in, warn about drafts that will be deleted within the next few days."""
    drafts = list(drafts_due_for_deletion(user))
    if not drafts:
        return
    names = "; ".join(
        f"{d.job_number or 'no job number'} (deleted {timezone.localtime(draft_expiry(d)):%d %b})" for d in drafts[:5]
    )
    messages.warning(
        request,
        f"You have {len(drafts)} draft order{'s' if len(drafts) != 1 else ''} that will be deleted soon "
        f"because they have not been saved for nearly 30 days: {names}. "
        "Open and save a draft to keep it, or submit it.",
    )
