from django.contrib.auth import get_user_model
from django.db.models import Count
from django.shortcuts import render

from products.models import Category, Product
from studio.models import LandingPageSettings


def home(request):
    """Public Biozone landing page; staff users keep the existing dashboard behavior."""
    if request.user.is_authenticated and request.user.role in ['ADMIN', 'WAREHOUSE']:
        from django.shortcuts import redirect
        return redirect('staff:dashboard')

    settings_obj = LandingPageSettings.objects.select_related(
        'hero_image', 'banner_1', 'banner_2'
    ).first()

    active_products = Product.objects.filter(is_active=True)
    categories = Category.objects.filter(is_active=True).annotate(
        product_count=Count('products')
    )[:6]

    User = get_user_model()
    client_count = User.objects.filter(role='CLIENT', is_active=True).count()
    manufacturer_count = active_products.exclude(manufacturer='').values('manufacturer').distinct().count()

    context = {
        'landing_settings': settings_obj,
        'categories': categories,
        'total_products': active_products.count(),
        'client_count': client_count,
        'manufacturer_count': manufacturer_count,
    }
    return render(request, 'landing.html', context)
