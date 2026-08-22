// Service worker مبسّط، غرضه الوحيد إنه يفعّل خاصية "تثبيت التطبيق"
// (installability) في المتصفحات اللي بتدعمها (Chrome/Edge/Samsung Internet
// على أندرويد وديسكتوب) — المعيار الأساسي عندهم إن الموقع لازم يكون عنده
// service worker مسجّل بمعالج "fetch" فعلي، وإلا حدث beforeinstallprompt
// (اللي بيظهر عليه زرار التثبيت) مش هيتفعّل خالص.
//
// عمدًا من غير أي تخزين مؤقت (cache) لصفحات ديناميكية: بيانات المخزون/
// الأسعار/الطلبات/الإشعارات لازم توصل فريش من السيرفر دايمًا — تخزينها هنا
// هيسبب عرض بيانات قديمة (مخزون أو سعر غلط لموظف أو عميل)، وهو أخطر بكتير
// من أي فايدة أوفلاين بسيطة. التخزين هنا مقصور على أصول static ثابتة بس
// (css/js/fonts/icons)، واللي أصلاً متكاشيه المتصفح لمدة سنة كاملة (راجع
// كومنتات base.html) فمفيش فايدة حقيقية إضافية من التخزين ده غير تحقيق
// شرط الـ installability.
const STATIC_CACHE = 'biozone-static-v1';

self.addEventListener('install', () => {
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keys) =>
            Promise.all(keys.filter((k) => k !== STATIC_CACHE).map((k) => caches.delete(k)))
        )
    );
    self.clients.claim();
});

self.addEventListener('fetch', (event) => {
    const req = event.request;
    if (req.method !== 'GET') return;

    const url = new URL(req.url);
    // أصول static بس (خط، أيقونات، js، css) — استراتيجية cache-first مع
    // تحديث في الخلفية. أي حاجة تانية (صفحات HTML، /media/، أي endpoint
    // ديناميكي) بتعدي للشبكة مباشرة من غير أي تدخل من الـ service worker.
    if (url.origin === self.location.origin && url.pathname.startsWith('/static/')) {
        event.respondWith(
            caches.open(STATIC_CACHE).then((cache) =>
                cache.match(req).then((cached) => {
                    const network = fetch(req)
                        .then((res) => {
                            if (res.ok) cache.put(req, res.clone());
                            return res;
                        })
                        .catch(() => cached);
                    return cached || network;
                })
            )
        );
    }
});
