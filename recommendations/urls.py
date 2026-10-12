from django.urls import path
from .views import RecommendationDetailView, RecommendationListView, RecommendationPreferenceView, RecommendationProfileView, RecommendationIdentityReviewView, RecommendationWantView

app_name = 'recommendations'
urlpatterns = [
    path('', RecommendationListView.as_view(), name='list'),
    path('profile/', RecommendationProfileView.as_view(), name='profile'),
    path('preferences/', RecommendationPreferenceView.as_view(), name='preferences'),
    path('identities/', RecommendationIdentityReviewView.as_view(), name='identities'),
    path('<int:bangumi_id>/', RecommendationDetailView.as_view(), name='detail'),
    path('<int:bangumi_id>/want/', RecommendationWantView.as_view(), name='want'),
]
