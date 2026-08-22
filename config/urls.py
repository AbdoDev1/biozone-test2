from pathlib import Path

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.shortcuts import redirect, render
from django.http import HttpResponse
from django.views.generic.base import RedirectView
from store.views import store_home
from config.home import home

def service_worker(request):
    """
    بيقرا static/js/sw.js من على القرص مباشرة (مش عن طريق /static/js/sw.js
    العادي) عشان الـ scope الافتراضي بتاع الـ service worker يبقى الجذر "/"
    مطابق لـ "scope": "/" في manifest.json. لو الملف اتسجل من مساره
    الطبيعي جوه /static/js/، الـ scope الافتراضي كان هيبقى "/static/js/"
    بس (المجلد اللي الملف نفسه فيه) وهيقصّر تغطيته على static بس بدل
    الموقع كله. بنقراه مباشرة من BASE_DIR/static (مش عن طريق آلية
    staticfiles/collectstatic) عشان يشتغل صح في DEBUG من غير أي خطوة
    build إضافية، وبرضه بعد collectstatic في الإنتاج.
    """
    sw_path = Path(settings.BASE_DIR) / 'static' / 'js' / 'sw.js'
    return HttpResponse(sw_path.read_text(encoding='utf-8'), content_type='application/javascript')

def healthz(request):
    """
    فحص خفيف لحالة container الـ web — بيرجع 200 بس لو Django فعليًا
    قادر يستقبل طلبات (مش بس الـ process بدأ). مقصود إنه من غير أي
    اعتماد على قاعدة البيانات، عشان يفرّق بين "web جاهز" و"db مش جاهزة"
    (الانتظار على الداتابيز أصلًا متكفّل بيه entrypoint.sh قبل ما gunicorn يشتغل).
    """
    return HttpResponse('ok')

class LegacyCatalogRedirect(RedirectView):
    """تحويل دائم (301) لأي رابط قديم كان بادئ بـ /catalog/ إلى /store/
    المكافئ له، حفاظًا على أي روابط محفوظة عند العملاء أو مفهرسة في جوجل
    من قبل إعادة التسمية."""
    permanent = True
    query_string = True

    def get_redirect_url(self, *args, **kwargs):
        subpath = kwargs.get('subpath', '')
        return f'/store/{subpath}'

urlpatterns = [
    path('healthz/', healthz, name='healthz'),
    path('sw.js', service_worker, name='service_worker'),
    path('admin/', admin.site.urls),
    path('', home, name='home'),
    path('accounts/', include('accounts.urls')),
    path('staff/', include('staff.urls')),
    path('store/', include('store.urls')),
    path('catalog/', LegacyCatalogRedirect.as_view()),
    path('catalog/<path:subpath>', LegacyCatalogRedirect.as_view()),
    path('', include('orders.urls')),
    path('invoices/', include('invoices.urls')),
    path('notifications/', include('notifications.urls')),
    path('activity/', include('activity.urls')),
    path('tags/', include('tags.urls')),
    path('followups/', include('followups.urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

if settings.DEBUG:
    try:
        import debug_toolbar
        urlpatterns += [path('__debug__/', include(debug_toolbar.urls))]
    except ImportError:
        pass

    # مؤقت لتجربة Sentry محليًا بس — محمي بشرط DEBUG عشان مستحيل
    # يظهر على السيرفر الحقيقي حتى لو نسيت تشيله بعد التجربة.
    # امسحه بعد ما تتأكد إن الـ Error وصل للوحة Sentry.
    def trigger_error(request):
        division_by_zero = 1 / 0

    urlpatterns += [path('sentry-debug/', trigger_error, name='sentry-debug')]
