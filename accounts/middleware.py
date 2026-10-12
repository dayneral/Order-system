"""Keeps each module (Material Orders, Voids) to the users who have access to it."""

from django.core.exceptions import PermissionDenied

# URL prefix -> user property that must be true.
MODULE_PREFIXES = {
    "/orders/": "can_order",
    "/catalogue/": "can_order",
    "/voids/": "can_voids",
}


class ModuleAccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            for prefix, prop in MODULE_PREFIXES.items():
                if request.path.startswith(prefix) and not getattr(user, prop):
                    raise PermissionDenied
        return self.get_response(request)
