from django.urls import path
from .views import RecommendationDetailView, RecommendationListView, RecommendationPreferenceView, RecommendationProfileView

app_name = 'recommendations'
urlpatterns = [
    path('', RecommendationListView.as_view(), name='list'),
    path('profile/', RecommendationProfileView.as_view(), name='profile'),
    path('preferences/', RecommendationPreferenceView.as_view(), name='preferences'),
    path('<int:bangumi_id>/', RecommendationDetailView.as_view(), name='detail'),
]
