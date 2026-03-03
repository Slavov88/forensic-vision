"""
Root URL configuration for ForensicVision.
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, include
from core import views as core_views

urlpatterns = [
    # Admin
    path("admin/", admin.site.urls),

    # Authentication (session-based for browser)
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="login.html"),
        name="login",
    ),
    path(
        "logout/",
        core_views.logout_view,
        name="logout",
    ),

    # Share-link redemption (web view)
    path("share/", include("core.share_urls")),

    # API (DRF)
    path("api/", include("api.urls")),

    # Frontend pages
    path("", include("core.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
