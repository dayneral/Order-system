import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_display_options_saved_to_account_and_applied(client, make_user):
    user = make_user()
    client.force_login(user)
    page = client.get(reverse("orders:list")).content.decode()
    assert 'data-bg="cream" data-font="atkinson" data-size="normal"' in page
    assert "Display" in page

    resp = client.post(reverse("accounts:display"), {"display_background": "dark", "display_font": "lexend",
                                                     "display_text_size": "large", "next": "/orders/"})
    assert resp.status_code == 302 and resp.url == "/orders/"
    user.refresh_from_db()
    assert (user.display_background, user.display_font, user.display_text_size) == ("dark", "lexend", "large")
    assert 'data-bg="dark" data-font="lexend" data-size="large"' in client.get(reverse("orders:list")).content.decode()


def test_display_rejects_bad_values_and_offsite_redirects(client, make_user):
    user = make_user()
    client.force_login(user)
    resp = client.post(reverse("accounts:display"), {"display_background": "neon", "next": "https://evil.example/"})
    assert resp.url == "/"
    user.refresh_from_db()
    assert user.display_background == "cream"


def test_display_requires_sign_in(client):
    resp = client.post(reverse("accounts:display"), {"display_background": "dark"})
    assert resp.status_code == 302 and "sign-in" in resp.url
