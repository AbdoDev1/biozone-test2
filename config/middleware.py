class NoBrowserCacheMiddleware:
    """
    كل صفحات النظام (لوحة الموظفين ومتجر العملاء) ديناميكية ومعتمدة على
    حالة الجلسة — عدد الإشعارات، توكن CSRF، محتوى السلة، صلاحيات
    المستخدم... من غير أي Cache-Control صريح، بعض المتصفحات (خصوصًا على
    الموبايل) بتطبّق تخزين مؤقت افتراضي (heuristic caching) على أي صفحة
    من غير Cache-Control واضح، فالتنقل العادي (لينك، تبويب جديد، حتى F5
    عادي) كان ممكن يرجّع نسخة HTML قديمة متخزنة عند المتصفح بدل ما يطلب
    نسخة جديدة فعليًا من السيرفر — وده اللي كان بيخلي أي تعديل حديث (زي
    كود جرس الإشعارات) يفضل "مش شغال" لحد ما تعمل hard refresh
    (Ctrl+Shift+R) يجبر المتصفح يتجاهل الكاش بالكامل.

    الحل: نمنع أي تخزين مؤقت للصفحات دي خالص (Cache-Control: no-store)،
    فكل تنقل عادي بيوصل السيرفر فعليًا ويرجع أحدث نسخة دايمًا.

    الملفات الثابتة (CSS/JS/الصور تحت /static/) مش متأثرة بالميدل وير ده
    خالص — WhiteNoiseMiddleware (فوقه في MIDDLEWARE في settings.py) بيردّ
    عليها بنفسه ويقفل السلسلة قبل ما توصل هنا أصلاً، فتفضل مستفيدة من
    الكاش الطويل بتاعها زي ما هي (راجع nginx.conf: expires 1y + hash في
    اسم الملف — تخزين المتصفح للملفات دي مقصود وصحيح، عكس صفحات HTML).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response['Cache-Control'] = 'no-store, no-cache, must-revalidate'
        response['Pragma'] = 'no-cache'
        return response
