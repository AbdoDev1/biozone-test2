# --- مرحلة 1: بناء Tailwind CSS ---
# مرحلة منفصلة بس عشان نبني tailwind.css من التمبليتس الحالية وقت الـ build.
# مفيش Node في الصورة النهائية خالص — الناتج (ملف CSS واحد) بس اللي بيتنقل.
FROM node:20-slim AS css-builder
WORKDIR /build
COPY package.json tailwind.config.js ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./frontend/
COPY templates/ ./templates/
COPY staff/templates/ ./staff/templates/
COPY staff/templatetags/ ./staff/templatetags/
COPY orders/templates/ ./orders/templates/
COPY accounts/templates/ ./accounts/templates/
COPY invoices/templates/ ./invoices/templates/
COPY notifications/templates/ ./notifications/templates/
COPY store/templates/ ./store/templates/
RUN npx tailwindcss -i ./frontend/input.css -o ./static/css/tailwind.css --minify

# htmx/Alpine/Chart.js كانوا محملين من CDN خارجي (unpkg.com، cdn.jsdelivr.net)
# على كل صفحة — نفس الفلسفة اللي خلتنا نبني tailwind.css محليًا بدل
# CDN، بس متطبقتش على السكريبتات دي. بننسخهم هنا من node_modules
# (نفس النسخ المثبتة بالظبط zي integrity hash القديم) لملفات ثابتة
# عادية، فيستفيدوا من نفس التخزين المؤقت/الضغط بتاع باقي static/
# (whitenoise hash في الاسم + gzip_static في nginx)، ومفيش أي اتصال
# لدومين تاني وقت تحميل الصفحة.
RUN mkdir -p ./static/js \
    && cp node_modules/htmx.org/dist/htmx.min.js ./static/js/htmx.min.js \
    && cp node_modules/alpinejs/dist/cdn.min.js ./static/js/alpine.min.js \
    && cp node_modules/chart.js/dist/chart.umd.min.js ./static/js/chart.umd.min.js \
    && cp node_modules/chart.js/dist/chart.umd.min.js.map ./static/js/chart.umd.min.js.map

# --- مرحلة 2: صورة التشغيل (Python فقط) ---
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# مكتبات نظام لازمة لـ psycopg2 و Pillow + curl لـ healthcheck
# + postgresql-client (pg_dump) عشان زرار "تشغيل نسخة احتياطية الآن" في
# لوحة الموظفين يقدر يعمل النسخة مباشرة من جوه الحاوية (اتصال شبكة عادي
# بـ DB_HOST:DB_PORT، نفس الاتصال اللي Django نفسه بيستخدمه) من غير ما
# يحتاج docker CLI ولا وصول لـ docker socket — راجع staff/services/backup.py
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc curl postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY . .

# استبدال tailwind.css المبني محليًا (ده بيتحدث تلقائيًا مع أي تعديل في
# التمبليتس، من غير الحاجة لـ Node وقت التشغيل ولا تشغيل build يدوي)
COPY --from=css-builder /build/static/css/tailwind.css /app/static/css/tailwind.css
# htmx/Alpine/Chart.js بقوا ملفات محلية بدل CDN خارجي (راجع تعليق مرحلة
# css-builder فوق) — نفس أسلوب نسخ tailwind.css بالظبط.
COPY --from=css-builder /build/static/js/htmx.min.js /app/static/js/htmx.min.js
COPY --from=css-builder /build/static/js/alpine.min.js /app/static/js/alpine.min.js
COPY --from=css-builder /build/static/js/chart.umd.min.js /app/static/js/chart.umd.min.js
COPY --from=css-builder /build/static/js/chart.umd.min.js.map /app/static/js/chart.umd.min.js.map

RUN chmod +x /app/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
