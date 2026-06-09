from django.urls import include, path, re_path

from api.views import spa_index

urlpatterns = [
    path('api/', include('api.urls')),
    # Everything that is not an /api/ route and not a real file served by
    # WhiteNoise falls through to the React single-page app.
    re_path(r'^(?!api/).*$', spa_index),
]
