from django.urls import path
from .views import AdminAnalyticsAPIView

urlpatterns = [
    path("analytics/", AdminAnalyticsAPIView.as_view(), name="admin-analytics"),
]
