from django.urls import path

from . import views

app_name = 'followups'

urlpatterns = [
    path('add/<str:app_label>/<str:model_name>/<int:object_id>/', views.followup_add, name='followup_add'),
    path('<int:pk>/done/', views.followup_done, name='followup_done'),
    path('<int:pk>/delete/', views.followup_delete, name='followup_delete'),
]
