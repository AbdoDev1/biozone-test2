"""
اختبار ضغط لموقع Biozone باستخدام Locust.

التشغيل (بعد ما الموقع يشتغل محليًا عبر docker-compose على المنفذ 8080):
    pip install locust
    locust -f locustfile.py --host=http://localhost:8080

بعدها افتح المتصفح على http://localhost:8089 وحدد:
    - عدد المستخدمين (Number of users) — مثلاً ابدأ بـ 20 وزوّد تدريجيًا
    - معدل الزيادة (Spawn rate) — مثلاً 5 مستخدمين/ثانية

راقب: Requests/sec, Response time (p95), Failure rate.
لو الـ p95 بدأ ياخد أكتر من ثانية أو الأخطاء بدأت تظهر، يبقى وصلت لحد سعة
السيرفر بالمواصفات دي.
"""

import random
import re
from locust import HttpUser, task, between

# ملحوظة CSRF: Django بيستخدم "masked" CSRF tokens — يعني القيمة اللي بتتخزن
# في كوكي csrftoken (32 حرف) مختلفة عن القيمة اللي بتتعرض في حقل الفورم
# csrfmiddlewaretoken (64 حرف، معمول لها XOR بقناع عشوائي كل مرة). عشان كده
# لازم ناخد القيمة من جسم صفحة اللوجن (HTML) مش من الكوكي — القيمة القديمة
# اللي كانت بتتاخد من response.cookies.get("csrftoken") كانت بتفشل بصمت
# (400 Bad Request) لأنها مش نفس القيمة اللي الفورم بيتوقعها.
CSRF_TOKEN_RE = re.compile(r'name="csrfmiddlewaretoken" value="([^"]+)"')


def get_csrf_token(response):
    """يستخرج csrfmiddlewaretoken من جسم صفحة الفورم (HTML)."""
    match = CSRF_TOKEN_RE.search(response.text)
    if not match:
        raise ValueError("csrfmiddlewaretoken مش موجود في صفحة الفورم")
    return match.group(1)


class GuestBrowsing(HttpUser):
    """زائر بيتصفح المتجر من غير تسجيل دخول — أكتر سيناريو هيحصل كتير."""
    weight = 3
    wait_time = between(1, 3)

    @task(3)
    def browse_home(self):
        self.client.get("/", name="/ (الرئيسية)")

    @task(2)
    def browse_store(self):
        self.client.get("/store/", name="/store/")

    @task(1)
    def view_product(self):
        # عدّل نطاق الـ IDs دي حسب عدد المنتجات الفعلي عندك في قاعدة الاختبار
        pk = random.randint(1, 20)
        self.client.get(f"/store/product/{pk}/", name="/store/product/[pk]/")

    @task(1)
    def view_login_page(self):
        self.client.get("/accounts/login/", name="/accounts/login/")


class LoggedInClient(HttpUser):
    """عميل مسجّل دخول بيتصفح ويضيف للسلة — سيناريو أتقل على قاعدة البيانات."""
    weight = 1
    wait_time = between(2, 5)

    def on_start(self):
        # عدّل بيانات الدخول دي لحساب عميل تجريبي حقيقي موجود في قاعدة اختبارك
        login_page = self.client.get("/accounts/login/", name="/accounts/login/ (GET)")
        csrf_token = get_csrf_token(login_page)
        self.client.post(
            "/accounts/login/",
            {
                "username": "test_client",
                "password": "test_password_123",
                "csrfmiddlewaretoken": csrf_token,
            },
            headers={"Referer": self.client.base_url + "/accounts/login/"},
            name="/accounts/login/ (POST)",
        )

    @task(2)
    def browse_store(self):
        self.client.get("/store/", name="/store/ (عميل)")

    @task(1)
    def view_cart(self):
        self.client.get("/cart/", name="/cart/")

    @task(1)
    def view_orders(self):
        self.client.get("/orders/", name="/orders/")
