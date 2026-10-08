"""
Health check for Render at /healthz.

Placed first in MIDDLEWARE so it answers before host validation and the
HTTPS redirect: Render's checker calls the service on its internal address.
"""

from django.http import HttpResponse

HEALTH_PATH = "/healthz"


class HealthCheckMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == HEALTH_PATH:
            return HttpResponse("ok", content_type="text/plain")
        return self.get_response(request)
