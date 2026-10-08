"""Deployment plumbing: health check and waiting for the database."""

import pytest
from django.core.management import call_command
from django.test import Client, override_settings


@override_settings(DEBUG=False, ALLOWED_HOSTS=["bfs-orders.onrender.com"], SECURE_SSL_REDIRECT=True)
def test_health_check_answers_on_any_host_without_redirect():
    client = Client(HTTP_HOST="10.201.3.4:10000")
    resp = client.get("/healthz")
    assert resp.status_code == 200 and resp.content == b"ok"


@override_settings(DEBUG=False, ALLOWED_HOSTS=["bfs-orders.onrender.com"])
def test_other_pages_still_reject_unknown_hosts():
    assert Client(HTTP_HOST="evil.example.com").get("/accounts/sign-in/").status_code == 400


@pytest.mark.django_db
def test_wait_for_db_returns_when_database_is_up(capsys):
    call_command("wait_for_db", "--timeout", "5")
    assert "Database is available" in capsys.readouterr().out
