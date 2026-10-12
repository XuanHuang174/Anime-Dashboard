"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from django.views.generic import TemplateView
from rest_framework_simplejwt.views import TokenRefreshView

from anime.views import CustomTokenObtainPairView


def health_check(request):
    return JsonResponse({
        'status': 'ok',
        'message': 'Anime API is running',
    })


urlpatterns = [
    path('admin/', admin.site.urls),
    path(
        'api/token/',
        CustomTokenObtainPairView.as_view(),
        name='token_obtain_pair',
    ),
    path(
        'api/token/refresh/',
        TokenRefreshView.as_view(),
        name='token_refresh',
    ),
    path('api/', include('anime.urls')),
    path('api/recommendations/', include('recommendations.urls')),
    path('login/', TemplateView.as_view(template_name='index.html')),
    path('activate/', TemplateView.as_view(template_name='index.html')),
    path('reset-password/', TemplateView.as_view(template_name='index.html')),
    path('featured/', TemplateView.as_view(template_name='index.html')),
    path('dashboard/', TemplateView.as_view(template_name='index.html')),
    path('admin-login/', TemplateView.as_view(template_name='index.html')),
    path('admin-dashboard/', TemplateView.as_view(template_name='index.html')),
    path('', health_check, name='health-check'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
