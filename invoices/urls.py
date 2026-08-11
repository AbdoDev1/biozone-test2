from django.urls import path
from . import views

app_name = 'invoices'

urlpatterns = [
    path('<int:pk>/print/', views.invoice_print, name='print'),
    path('reversals/<int:pk>/print/', views.reversal_print, name='reversal_print'),
]
