from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "NexScout admin"
admin.site.site_title = "NexScout"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("scout.urls")),
]
