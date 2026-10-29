from rest_framework.pagination import LimitOffsetPagination

MAX_PAGE_SIZE = 1000


class CappedLimitOffsetPagination(LimitOffsetPagination):
    """The list envelope every view uses, with a ceiling on ``limit``.

    1000 is the most the web and phone ask for. A larger ``limit`` is clamped,
    not refused, so the response shape is unchanged.
    """

    max_limit = MAX_PAGE_SIZE
