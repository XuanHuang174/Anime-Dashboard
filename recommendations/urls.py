from django.urls import path
from .views import RecommendationDetailView, RecommendationListView, RecommendationPreferenceView, RecommendationProfileView, RecommendationIdentityReviewView, RecommendationWantView
from .views import RecommendationFeedbackView, RecommendationFeedbackUndoView, RecommendationHiddenFeedbackView
from .views import RecommendationCapabilitiesView
from .views import RecommendationIdentityBatchView
from .views import RecommendationProfilePreviewView
from .views import RecommendationContextView

app_name = 'recommendations'

urlpatterns = [
    path('', RecommendationListView.as_view(), name='list'),
    path('capabilities/', RecommendationCapabilitiesView.as_view(), name='capabilities'),
    path('context/', RecommendationContextView.as_view(), name='context'),
    path('profile/', RecommendationProfileView.as_view(), name='profile'),
    path('profile/preview/', RecommendationProfilePreviewView.as_view(), name='profile-preview'),
    path('preferences/', RecommendationPreferenceView.as_view(), name='preferences'),
    path('identities/', RecommendationIdentityReviewView.as_view(), name='identities'),
    path('identities/batch/', RecommendationIdentityBatchView.as_view(), name='identity-batch'),
    path('feedback/', RecommendationFeedbackView.as_view(), name='feedback'),
    path('feedback/undo/', RecommendationFeedbackUndoView.as_view(), name='feedback-undo'),
    path('feedback/hidden/', RecommendationHiddenFeedbackView.as_view(), name='feedback-hidden'),
    path('<int:bangumi_id>/', RecommendationDetailView.as_view(), name='detail'),
    path('<int:bangumi_id>/want/', RecommendationWantView.as_view(), name='want'),
]
