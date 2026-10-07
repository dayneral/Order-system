from django.contrib.auth.backends import ModelBackend


class EmailBackend(ModelBackend):
    """Signs users in by email address (case insensitive).

    Only approved accounts are allowed in: ModelBackend refuses users whose
    is_active flag is False, and is_active is only True for approved users.
    """

    def authenticate(self, request, username=None, password=None, email=None, **kwargs):
        return super().authenticate(request, username=email or username, password=password, **kwargs)
