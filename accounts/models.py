"""
User accounts.

This users table is designed to be shared with a future job management
system, so it holds only identity and access information. Users have a
stable UUID primary key.
"""

import uuid

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager):
    use_in_migrations = True

    def get_by_natural_key(self, email):
        # Sign-in is not case sensitive.
        return self.get(email__iexact=email)

    def create_user(self, email, full_name, password=None, **extra):
        if not email:
            raise ValueError("An email address is required.")
        user = self.model(email=self.normalize_email(email).lower(), full_name=full_name, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, full_name, password=None, **extra):
        extra.setdefault("role", User.Role.ADMIN)
        extra.setdefault("status", User.Status.APPROVED)
        extra.setdefault("is_superuser", True)
        return self.create_user(email, full_name, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    class Role(models.TextChoices):
        ORDERING = "ordering", "Ordering user"
        ADMIN = "admin", "Admin"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        DISABLED = "disabled", "Disabled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField("email address", unique=True)
    full_name = models.CharField(max_length=150)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.ORDERING)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    # Kept in step with status on save; only approved users can sign in.
    is_active = models.BooleanField(default=False, editable=False)
    date_joined = models.DateTimeField(default=timezone.now)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        ordering = ["full_name"]

    def __str__(self):
        return f"{self.full_name} <{self.email}>"

    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip().lower()
        self.is_active = self.status == self.Status.APPROVED
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "status" in update_fields:
            kwargs["update_fields"] = set(update_fields) | {"is_active"}
        super().save(*args, **kwargs)

    # --- Roles -------------------------------------------------------------

    @property
    def is_admin(self):
        return self.is_active and (self.role == self.Role.ADMIN or self.is_superuser)

    @property
    def can_order(self):
        return self.is_active

    @property
    def is_staff(self):
        # Gives admins access to the built-in Django admin screens.
        return self.is_admin

    def has_perm(self, perm, obj=None):
        if self.is_admin:
            return True
        return super().has_perm(perm, obj)

    def has_module_perms(self, app_label):
        if self.is_admin:
            return True
        return super().has_module_perms(app_label)

    def get_full_name(self):
        return self.full_name

    def get_short_name(self):
        return self.full_name.split(" ")[0] if self.full_name else self.email
