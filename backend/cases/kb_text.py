"""Text handling shared by the two knowledge-base suggesters.

There are two of them now: the agent-facing one in `kb_views` and the
customer-facing one in `portal_views`. They answer to different visibility
rules and return different shapes, but they read the same articles, so the
matching and the truncation live here rather than being written twice and
drifting apart.

What is deliberately NOT shared is the queryset. Each caller builds its own,
because deciding who may see which article is the security boundary and it
should be written out at each call site rather than inherited from a helper.
"""

from django.db.models import Q

SNIPPET_MAX = 200


def snippet(text):
    """A single-line preview of an article body."""
    if not text:
        return ""
    text = text.strip()
    if len(text) <= SNIPPET_MAX:
        return text
    return text[: SNIPPET_MAX - 1].rstrip() + "…"


def text_match(term):
    """Match `term` against an article's title or body.

    Substring matching, not full-text search. At the size of one org's
    knowledge base that is the right trade, and it keeps the two suggesters
    returning the same articles for the same words.
    """
    return Q(title__icontains=term) | Q(description__icontains=term)
