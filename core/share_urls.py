"""
Share link URL patterns: /share/<token>/
"""
from django.urls import path
from .share_views import redeem_share_link

urlpatterns = [
    path("<uuid:token>/", redeem_share_link, name="share-redeem"),
]
