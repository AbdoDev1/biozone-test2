"""
ديكوريتور بيخلي view متزامن (sync عادي) يتنفذ في thread pool عام بدل
الـthread المفرد بتاع الـworker تحت ASGI.

السياق: web-store شغال ASGI (gunicorn + uvicorn worker — راجع
entrypoint.sh)، وDjango بينفّذ أي sync view تحته عبر
sync_to_async(..., thread_sensitive=True) بشكل افتراضي — وthread_sensitive
=True معناه كل الـviews المتزامنة في نفس الـworker process بتتسلسل على
thread واحد بس. يعني لو 8 مستخدمين حاولوا يسجلوا دخول مع بعض (كل واحد
فيهم هاش باسورد 350 ألف iteration — راجع accounts/hashers.py)، كل واحد
فيهم بيوقف اللي وراه، وحتى صفحات خفيفة زي / بتقعد تستنى في نفس الطابور.

## محاولة أولى فشلت — ودرس مهم منها

أول محاولة حوّلت login_view نفسها لـ async def واستخدمت
await request.auser() بدل request.user. ده كسر فعليًا تحت حمل حقيقي:
render() بينفّذ كل الـcontext processors المسجّلة في TEMPLATES تلقائيًا
(auth, cart_count, new_arrivals_count, staff_nav, notifications) — وهي
بتلمس request.user (مش request.auser()) وبتعمل queries متزامنة عادية.
جوه async def view حقيقية (تنفيذها على event loop مباشرة مش thread
منفصل)، أي query متزامن زي ده بيرمي SynchronousOnlyOperation فورًا —
ظهر للمستخدمين كـ500 على صفحة اللوجين (فمفيهاش csrfmiddlewaretoken أصلًا،
وده اللي كسّر locustfile على طول من أول نص ثانية في التست).

## الحل الحالي

بدل ما نحوّل محتوى الـview، بنسيبها sync عادي 100% (زي الأصل بالظبط —
request.user، render()، كل حاجة عادية)، وبنلف عليها بديكوريتور بسيط
بيخلي *كل* تنفيذ الـview (مش سطر بعينه) يحصل في thread pool عام
(thread_sensitive=False) بدل الـthread المفرد. مفيش خطر
SynchronousOnlyOperation خالص لأن الكود جواها لسه بينفّذ في context متزامن
حقيقي (thread فعلي)، بس thread تاني غير المسلسل.

MAX_CONCURRENT_UNBOUND_VIEWS بيحدد أقصى عدد تنفيذات متوازية للـviews
المعلَّمة بالديكوريتور ده مع بعض — بدون الحد ده، burst كبير من محاولات
الدخول كان ممكن يفتح threads كتير أوي ويحرق الـCPU المتاح (cpus: 1.0
حاليًا لـweb-store — راجع docker-compose.yml) مرة واحدة. يستاهل مراجعة
لو cpus اتغيّرت.
"""

import asyncio

from asgiref.sync import sync_to_async

MAX_CONCURRENT_UNBOUND_VIEWS = 4

_semaphore = asyncio.Semaphore(MAX_CONCURRENT_UNBOUND_VIEWS)


def thread_unbound(view_func):
    """بيحوّل view متزامن عادي لتنفيذ في thread pool عام بدل الـthread
    المفرد بتاع الـworker — راجع docstring الملف فوق للتفاصيل والسبب."""

    async def wrapped(request, *args, **kwargs):
        async with _semaphore:
            return await sync_to_async(view_func, thread_sensitive=False)(request, *args, **kwargs)

    wrapped.__name__ = getattr(view_func, '__name__', 'wrapped')
    wrapped.__doc__ = view_func.__doc__
    return wrapped
