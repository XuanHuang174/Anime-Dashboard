from django.urls import path
from .views import RecommendationPreferenceView, RecommendationProfileView

app_name = 'recommendations'
urlpatterns = [
    path('profile/', RecommendationProfileView.as_view(), name='profile'),
    path('preferences/', RecommendationPreferenceView.as_view(), name='preferences'),
]
