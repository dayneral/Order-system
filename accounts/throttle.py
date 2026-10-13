"""
Simple attempt limits for sign-in, registration and password reset, kept in
the cache. The app runs as one process (see render.yaml), so the default
in-memory cache is shared by every request. If more processes are added,
switch CACHES to a shared backend (database or Redis) so the limits hold.
"""

from django.core.cache import cache

WINDOW = 15 * 60

LIMITS = {
    # name: (max attempts, seconds)
    "login-email": (5, WINDOW),        # failed sign-ins per email address
    "login-ip": (20, WINDOW),          # failed sign-ins per IP address
    "register-ip": (5, 60 * 60),       # registration submissions per IP address
    "reset-ip": (5, 60 * 60),          # password reset requests per IP address
    "reset-email": (3, 60 * 60),       # password reset emails per address
}


def client_ip(request):
    """The visitor's IP behind Render's proxies (Cloudflare headers first, then X-Forwarded-For).

    A forged header only lets someone dodge the per-IP limit; the per-email limit still applies.
    Reading a proxy's own address instead would make every user share one limit, which is worse.
    """
    for header in ("HTTP_CF_CONNECTING_IP", "HTTP_TRUE_CLIENT_IP"):
        if request.META.get(header):
            return request.META[header].strip()
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _key(name, value):
    return f"throttle:{name}:{(value or '').strip().lower()}"


def blocked(name, value):
    limit, _ = LIMITS[name]
    return cache.get(_key(name, value), 0) >= limit


def hit(name, value):
    _, seconds = LIMITS[name]
    key = _key(name, value)
    if cache.add(key, 1, seconds):
        return 1
    try:
        return cache.incr(key)
    except ValueError:  # expired between add and incr
        cache.set(key, 1, seconds)
        return 1


def clear(name, value):
    cache.delete(_key(name, value))
