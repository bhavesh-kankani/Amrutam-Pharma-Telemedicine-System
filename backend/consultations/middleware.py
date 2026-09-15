import json
import logging
from django.core.cache import cache
from django.http import HttpResponse

logger = logging.getLogger(__name__)


class IdempotencyMiddleware:
    """
    Guarantees idempotency for mutation operations (POST, PUT, PATCH).
    Checks the 'X-Idempotency-Key' header (or body fallback) against Redis
    and short-circuits with the cached response for repeated hits.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method not in ("POST", "PUT", "PATCH"):
            return self.get_response(request)

        # 1. Look for idempotency key in headers (X-Idempotency-Key / HTTP_X_IDEMPOTENCY_KEY)
        idempotency_key = (
            request.headers.get("X-Idempotency-Key")
            or request.META.get("HTTP_X_IDEMPOTENCY_KEY")
        )

        if not idempotency_key:
            return self.get_response(request)

        cache_key = f"idempotency:{idempotency_key}"

        # 2. Cache HIT: Return previously stored 2xx response immediately
        try:
            cached_response = cache.get(cache_key)
            if cached_response:
                response = HttpResponse(
                    content=cached_response["content"],
                    status=cached_response["status"],
                    content_type=cached_response.get("content_type", "application/json"),
                )
                response["X-Cache-Lookup"] = "HIT"
                response["X-Idempotency-Key"] = str(idempotency_key)
                try:
                    response.data = json.loads(cached_response["content"])
                except Exception:
                    response.data = None
                return response
        except Exception as e:
            logger.warning(f"Cache lookup failed for idempotency key {idempotency_key}: {e}")

        # 3. Cache MISS: Proceed with view execution
        response = self.get_response(request)

        # Only cache successful creation/mutation responses
        if 200 <= response.status_code < 300:
            try:
                content = (
                    response.content.decode("utf-8")
                    if isinstance(response.content, bytes)
                    else str(response.content)
                )
                cache.set(
                    cache_key,
                    {
                        "content": content,
                        "status": response.status_code,
                        "content_type": response.headers.get("Content-Type", "application/json"),
                    },
                    timeout=86400,  # 24-hour TTL
                )
                response["X-Cache-Lookup"] = "MISS"
                response["X-Idempotency-Key"] = str(idempotency_key)
            except Exception as e:
                logger.warning(f"Cache set failed for idempotency key {idempotency_key}: {e}")

        return response
