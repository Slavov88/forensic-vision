"""
API URL configuration.
"""
from django.urls import path, include
from rest_framework.authtoken.views import obtain_auth_token
from rest_framework.routers import DefaultRouter

from .views import (
    AnalysisJobViewSet,
    CaseViewSet,
    EvidenceViewSet,
    ShareLinkRevokeView,
    CommentDeleteView,
)

router = DefaultRouter()
router.register(r"cases", CaseViewSet, basename="case")
router.register(r"evidence", EvidenceViewSet, basename="evidence")
router.register(r"analysis/jobs", AnalysisJobViewSet, basename="analysisjob")

urlpatterns = [
    # Token auth for API testing (curl)
    path("auth/token/", obtain_auth_token, name="api-token"),

    # Share link revocation
    path("share-links/<uuid:token>/revoke/", ShareLinkRevokeView.as_view(), name="sharelink-revoke"),
    
    # Comments deletion
    path("comments/<int:pk>/", CommentDeleteView.as_view(), name="comment-delete"),

    # Router-registered ViewSets
    path("", include(router.urls)),
]
