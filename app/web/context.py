def nav(request):
    """The number on the Review menu item: tracking results nobody has judged yet."""
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}
    from .views import review_queue
    return {"nav_review": review_queue().count()}
