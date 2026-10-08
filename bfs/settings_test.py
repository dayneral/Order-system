"""Settings for the automated tests: no production secrets needed."""

import os

os.environ.setdefault("DEBUG", "1")

from .settings import *  # noqa: E402,F403

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # fast hashing in tests only

# Most tests cover the pricing rules, so run with order values on; tests for the
# shelved (default) behaviour switch it off with override_settings.
ORDER_VALUES_ENABLED = True
