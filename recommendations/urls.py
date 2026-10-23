from django.urls import path
from .views import RecommendationDetailView, RecommendationListView, RecommendationPreferenceView, RecommendationProfileView, RecommendationIdentityReviewView, RecommendationWantView
from .views import RecommendationFeedbackView, RecommendationFeedbackUndoView, RecommendationHiddenFeedbackView
from .views import RecommendationCapabilitiesView
from .views import RecommendationIdentityBatchView
from .views import RecommendationProfilePreviewView
from .views import RecommendationContextView
from .eligibility_views import RecommendationEligibilityView, RecommendationEligibilityRevokeView, RecommendationEligibilityApplyView

app_name = 'recommendations'

from .exploration_views import ExplorationCatalogView, ExplorationContractView

from .access_admin_views import AccessControlView, AccessApplicationsView, AccessApplicationView

urlpatterns = [
    path('explore/catalog/', ExplorationCatalogView.as_view(), name='explore-catalog'),
    path('explore/contract/', ExplorationContractView.as_view(), name='explore-contract'),
    path('admin/control/', AccessControlView.as_view()),
    path('admin/applications/', AccessApplicationsView.as_view()),
    path('admin/applications/<int:user_id>/', AccessApplicationView.as_view()),
    path('', RecommendationListView.as_view(), name='list'),
    path('capabilities/', RecommendationCapabilitiesView.as_view(), name='capabilities'),
    path('context/', RecommendationContextView.as_view(), name='context'),
    path('eligibility/', RecommendationEligibilityView.as_view(), name='eligibility'),
    path('eligibility/apply/', RecommendationEligibilityApplyView.as_view(), name='eligibility-apply'),
    path('eligibility/revoke/', RecommendationEligibilityRevokeView.as_view(), name='eligibility-revoke'),
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
