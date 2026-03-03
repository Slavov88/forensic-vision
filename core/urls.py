"""
Frontend URL patterns (non-API browser views).
"""
from django.urls import path
from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("cases/", views.case_list, name="case-list"),
    path("cases/new/", views.case_create, name="case-create"),
    path("cases/<int:pk>/", views.case_detail, name="case-detail"),
    path("cases/<int:pk>/comments/add/", views.case_comment_create, name="case-comment-create"),
    path("cases/<int:pk>/comments/<int:comment_id>/delete/", views.case_comment_delete, name="case-comment-delete"),
    path("evidence/<int:pk>/", views.evidence_detail, name="evidence-detail"),
    path("methodology/", views.methodology, name="methodology"),
    path("register/", views.register_view, name="register"),
]
