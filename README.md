# نصب آسان سرور Element X با تماس صوتی و تصویری

این پروژه **سرور مورد نیاز Element X** را نصب می‌کند. خود Element X اپلیکیشن Android و iOS است. نسخه وب Element هم همراه سرور نصب می‌شود.

اجزا: Synapse، PostgreSQL، Element Web، LiveKit، سرویس مجوز MatrixRTC، Nginx و گواهی خودکار Let's Encrypt. برای تماس روی شبکه‌های محدود، HTTPS و TURN/TLS با مسیریابی SNI روی پورت ۴۴۳ مشترک هستند. تماس‌های داخل Element X به نصب جداگانه رابط Element Call نیاز ندارند.

## پیش‌نیاز

- Ubuntu 22.04 یا 24.04، معماری amd64 یا arm64؛ مسیر پیشنهادی و آزمایش نصب: Ubuntu 22.04 amd64.
- پیشنهاد برای استفاده سبک: حداقل ۲ هسته، ۴ گیگ RAM و ۳۰ گیگ دیسک. ظرفیت تماس به پهنای باند، تعداد کاربران و کیفیت تصویر بستگی دارد.
- حداقل ۸ گیگ فضای خالی هنگام نصب؛ برای فایل‌ها و بکاپ فضای بیشتری لازم است.
- IPv4 عمومی ثابت، دسترسی root، دسترسی به مخازن Ubuntu، Docker و GHCR.
- پورت ۴۴۳ باید آزاد باشد. Nginx موجود روی پورت ۸۰ قابل استفاده است؛ وب‌سرورهای دیگر یا پیکربندی‌های دارای `stream` به ادغام دستی نیاز دارند.

## DNS

برای دامنه فرضی `example.com` چهار رکورد زیر را بسازید:

| نوع | نام | مقدار |
|---|---|---|
| A | matrix | IP عمومی سرور |
| A | element | IP عمومی سرور |
| A | rtc | IP عمومی سرور |
| A | turn | IP عمومی سرور |

در Cloudflare همه **DNS only** باشند. این نصب IPv4 است؛ برای این چهار نام رکورد AAAA نسازید. دامنه اصلی نیازی به تغییر ندارد. شناسه کاربران به صورت `@user:matrix.example.com` خواهد بود؛ نام سرور بعد از ایجاد حساب‌ها قابل تغییر نیست.

## نصب

```bash
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/Mehdi682007/element-x-server-installer.git
cd element-x-server-installer
sudo bash install.sh
```

اسکریپت دامنه، IPv4 و ایمیل اختیاری را می‌پرسد. نمونه اجرای غیرتعاملی:

```bash
sudo env DOMAIN=example.com PUBLIC_IP=203.0.113.10 EMAIL=admin@example.com bash install.sh
```

اسکریپت دیسک‌ها را فرمت یا ادغام نمی‌کند. اطلاعات نصب در `/opt/element-stack` قرار می‌گیرد. اجرای مجدد روی همان دامنه و IP رمزهای موجود را نگه می‌دارد، اما فایل‌های پیکربندی تولیدشده را بازسازی می‌کند؛ پیکربندی قبلی در `config-history` نگه داشته می‌شود. این قابلیت جای بکاپ را نمی‌گیرد. تغییر دامنه/IP نصب موجود عمداً متوقف می‌شود.

فایل `state.json`، پیکربندی‌های اجرایی، کلیدها، اطلاعات حساب و بکاپ‌ها خصوصی‌اند و نباید در گیت‌هاب یا ویدئو نمایش داده شوند.

## فایروال

اگر UFW فعال باشد، اسکریپت قواعد لازم را اضافه می‌کند؛ UFW غیرفعال را فعال نمی‌کند. فایروال پنل VPS باید این ورودی‌ها را مجاز کند:

| پروتکل | پورت | کاربرد |
|---|---|---|
| TCP | 22 یا پورت SSH خودتان | مدیریت |
| TCP | 80 | صدور و تمدید گواهی |
| TCP | 443 | HTTPS و TURN/TLS |
| TCP | 7881 | مسیر جایگزین رسانه روی TCP |
| UDP | 3478 | TURN/STUN |
| UDP | 50000–50200 | رسانه و relay |

پورت‌های 8008، 7880، 18080، 18088 و 8443 فقط روی localhost هستند. PostgreSQL پورت عمومی ندارد. پورت داخلی TURN یعنی 5349 فقط هدر PROXY از localhost را می‌پذیرد؛ آن را در فایروال پنل باز نکنید. وب‌هوک LiveKit و کلیدها خصوصی باقی می‌مانند.

## ورود و ساخت حساب

```bash
sudo cat /opt/element-stack/admin-credentials.txt
sudo /opt/element-stack/manage.sh create-user
```

حساب اولیه `admin` رمز تصادفی دارد. برای استفاده روزمره حساب معمولی بسازید. ثبت‌نام عمومی بسته است تا سرور به محل ساخت حساب‌های ناشناس تبدیل نشود.

در Element X گزینه انتخاب سرور / Change server را انتخاب کنید، `https://matrix.example.com` را وارد کنید و با حساب ساخته‌شده وارد شوید. نسخه وب: `https://element.example.com`.

پس از ورود، بازیابی رمزنگاری/Recovery Key را تنظیم و در محل امن ذخیره کنید. ادمین سرور نمی‌تواند پیام‌های رمزنگاری‌شده را بدون کلید کاربر بازیابی کند.

## آزمایش تماس

۱. دو حساب معمولی بسازید و روی دو دستگاه وارد شوید.
۲. یکی را با اینترنت همراه و دیگری را با Wi-Fi وصل کنید.
۳. یک اتاق دونفره بسازید؛ پیام رمزنگاری‌شده، تماس صوتی و سپس تصویر دوطرفه را امتحان کنید.
۴. قطع و وصل شبکه و خروج هر دو نفر از تماس را آزمایش کنید.

عبور از بررسی HTTP به‌تنهایی ثابت نمی‌کند صدا و تصویر روی همه شبکه‌ها کار می‌کند. برای تست مسیر TURN باید ارتباط UDP را در محیط آزمایش کلاینت محدود کنید و تماس را دوباره امتحان کنید.

## مدیریت

```bash
sudo /opt/element-stack/manage.sh status
sudo /opt/element-stack/manage.sh health
sudo /opt/element-stack/manage.sh logs synapse
sudo /opt/element-stack/manage.sh logs livekit
sudo /opt/element-stack/manage.sh restart
sudo /opt/element-stack/manage.sh backup
```

بکاپ، Synapse را موقتاً متوقف می‌کند، از PostgreSQL خروجی می‌گیرد و فایل‌ها/رسانه‌ها/کلیدها را ذخیره می‌کند؛ سپس Synapse را راه می‌اندازد. خروجی خصوصی در `/var/backups/element-stack` است. آن را خارج از سرور کپی کنید. بکاپ‌ها خودکار پاک نمی‌شوند؛ ظرفیت دیسک را بررسی کنید. بکاپ شامل گواهی کپی‌شده است، اما حساب Certbot و تنظیمات سایر سرویس‌های میزبان را شامل نمی‌شود.

برای بازیابی روی میزبان تازه، ابتدا بسته‌ها و زیرساخت HTTPS را آماده کنید. آرشیو بیرونی شامل `database.dump` و `files.tar.gz` است. پس از بازکردن فایل‌ها در `/opt/element-stack`، فقط PostgreSQL را اجرا کنید و dump را با `pg_restore -U synapse -d synapse --clean --if-exists` وارد کنید؛ سپس باقی سرویس‌ها را بالا بیاورید. برای بازیابی، همان نسخه اصلی PostgreSQL و همان دامنه/کلیدهای سرور را نگه دارید. پیش از اتکا به بکاپ، بازیابی را روی یک میزبان آزمایشی امتحان کنید.

گواهی‌ها با `certbot.timer` تمدید می‌شوند. hook تمدید، گواهی TURN را کپی و LiveKit را راه‌اندازی مجدد می‌کند؛ تماس‌های فعال ممکن است هنگام این restart کوتاه قطع شوند.

## نسخه‌ها و به‌روزرسانی

نسخه‌های اصلی در `generate.py` مشخص‌اند: Synapse 1.163.2، Element Web 1.12.32، LiveKit 1.13.11، lk-jwt-service 0.9.0 و PostgreSQL 17. تگ PostgreSQL یعنی `17-alpine` دریافت وصله‌های همان نسخه اصلی را ممکن می‌کند. به‌روزرسانی خودکار کانتینرها فعال نیست. قبل از تغییر نسخه‌ها بکاپ بگیرید، release notes را بررسی کنید و سلامت/تماس را دوباره آزمایش کنید. از تغییر خودکار نسخه اصلی PostgreSQL خودداری کنید.

## نکات ذخیره‌سازی

اگر سرور LVM دارد، می‌توان فضای آزاد VG یا یک دیسک واقعاً خالی را به LV روت اضافه کرد. این عملیات وابسته به ساختار هر سرور است و عمداً در نصب خودکار نیست. گسترش یک LV روی دو دیسک **RAID یا بکاپ نیست**؛ از دست رفتن هر یک از دیسک‌ها می‌تواند کل فایل‌سیستم را خراب کند.

## توسعه و منابع

```bash
python3 -m unittest -v test_config.py
bash -n install.sh && bash -n manage.sh
```

- [Element Call self-hosting](https://github.com/element-hq/element-call/blob/main/docs/self_hosting.md)
- [LiveKit ports](https://docs.livekit.io/transport/self-hosting/ports-firewall/)
- [MatrixRTC authorization service](https://github.com/element-hq/lk-jwt-service)
- [Synapse configuration](https://element-hq.github.io/synapse/latest/usage/configuration/config_documentation.html)

این یک نصاب مستقل جامعه کاربری است و محصول رسمی Element نیست.
