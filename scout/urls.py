from django.urls import path

from . import views

app_name = "scout"

urlpatterns = [
    path("", views.overview, name="overview"),
    path("run/", views.run_action, name="run"),
    path("jobs/", views.jobs, name="jobs"),
    path("jobs/<int:pk>/", views.job_detail, name="job_detail"),
    path("jobs/<int:pk>/status/", views.job_status, name="job_status"),
    path("jobs/<int:pk>/save/", views.job_save, name="job_save"),
    path("jobs/<int:pk>/action/", views.job_action, name="job_action"),
    path("pipeline/", views.pipeline, name="pipeline"),
    path("outreach/", views.outreach_list, name="outreach"),
    path("outreach/action/", views.outreach_action, name="outreach_bulk"),
    path("outreach/<int:pk>/", views.outreach_detail, name="outreach_detail"),
    path("outreach/<int:pk>/action/", views.outreach_action, name="outreach_action"),
    path("settings/", views.settings_view, name="settings"),
    path("activity/", views.activity, name="activity"),
]
