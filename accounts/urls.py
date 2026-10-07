from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("sign-in/", views.SignInView.as_view(), name="login"),
    path("sign-out/", auth_views.LogoutView.as_view(), name="logout"),
    path("register/", views.register, name="register"),
    path("register/done/", views.register_done, name="register_done"),
    path("password/forgot/", views.ForgotPasswordView.as_view(), name="password_reset"),
    path("password/forgot/sent/", auth_views.PasswordResetDoneView.as_view(
        template_name="accounts/password_reset_done.html"), name="password_reset_done"),
    path("password/reset/<uidb64>/<token>/", views.ResetConfirmView.as_view(), name="password_reset_confirm"),
    path("password/reset/complete/", auth_views.PasswordResetCompleteView.as_view(
        template_name="accounts/password_reset_complete.html"), name="password_reset_complete"),
    path("staff/users/", views.user_list, name="user_list"),
    path("staff/users/<uuid:user_id>/", views.user_action, name="user_action"),
]
