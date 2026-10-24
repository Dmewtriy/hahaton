# Развёртывание у коллег

Стенд для хакатона: FastAPI + PostgreSQL 17 + один worker; MAX через polling, ML через заглушку или внешний HTTP-сервис. Node.js, Redis, Celery и установленный клиент MAX для запуска backend не нужны.

## 1. Чистый запуск только через Docker

Нужны Git, работающий Docker Desktop/Engine с Compose и доступ к репозиторию. Команды выполняются **из корня клона, где лежит compose.yaml**.

```powershell
git clone https://github.com/Dmewtriy/hahaton.git
cd hahaton
docker build -t korsik-backend:local .
```

Создать новый `.env` со случайными ключами. В существующем стенде этот шаг пропустить: команда не перезаписывает имеющийся `.env`.

Windows PowerShell:

```powershell
$projectPath = (Get-Location).Path
docker run --rm --network none --mount "type=bind,source=$projectPath,target=/workspace" --workdir /workspace korsik-backend:local python -m korsik.cli init --demo
```

Linux/macOS, вместо предыдущих двух строк:

```bash
docker run --rm --network none --user "$(id -u):$(id -g)" --mount "type=bind,source=$PWD,target=/workspace" --workdir /workspace korsik-backend:local python -m korsik.cli init --demo
```

Запуск с mock MAX и ML-заглушкой:

```powershell
docker compose up -d --build
docker compose exec api python -m korsik.cli seed-demo
docker compose ps
```

`seed-demo` выполняется только для новой учебной БД. Миграции запускаются автоматически перед API/worker; текущая последняя — `0003`. БД и файлы сохраняются в volumes `korsik_postgres_data` и `korsik_app_data`.

- Кабинет HR: http://127.0.0.1:8000/dashboard
- Swagger/API: http://127.0.0.1:8000/docs
- Проверка: http://127.0.0.1:8000/health/ready — ожидается `ready`, `transport=mock`, `ml_mode=stub`.

Получить созданные учебные ключи в своём локальном терминале:

```powershell
docker compose exec api python -c "from pathlib import Path; print(Path('/app/data/demo_credentials.json').read_text())"
```

Для кабинета использовать `hr.api_token`. Bootstrap — отдельный ключ управления доступом из `.env`; он не открывает личные данные сотрудников. Вывод с ключами не прикладывать к публичным логам или задачам.

## 2. Подключить настоящий MAX с mock RAG

В **своём** `.env` изменить только нужные строки:

```dotenv
KORSIK_ENVIRONMENT=development
KORSIK_TRANSPORT=max
KORSIK_MAX_TOKEN=получить-приватно-у-владельца-бота
KORSIK_MAX_BOT_NAME=имя_бота_без_собаки
KORSIK_MAX_CA_FILE=certs/russian_trusted_root_ca.pem
KORSIK_ML_MODE=stub
```

Сохранить имеющиеся `POSTGRES_PASSWORD`, bootstrap-ключ и остальные настройки. Для уже созданного PostgreSQL-volume смена пароля в `.env` сама пароль базы не меняет. Ошибка `POSTGRES_PASSWORD is missing` означает, что `.env` не создан/не в текущей папке/не содержит эту переменную.

**У одного MAX-бота должен быть один polling-процесс на всех ноутбуках.** Согласовать переключение: на старом стенде выполнить `docker compose stop poll`, затем запускать новый. Polling и webhook одновременно не включать.

```powershell
docker compose --profile max-poll up -d --build
docker compose exec api python -m korsik.cli sync-commands
docker compose logs -f poll worker
```

В свежей БД создать сотрудников и HR в кабинете и выдать приглашения. Существующие привязки MAX из чужой БД в чистом клоне не появятся. Клиент MAX нужен проверяющим коллегам; разработчик может проверять backend без него.

Быстрый ручной путь: приглашение сотрудника → `/menu` → опрос/ответ номером → `/hr сообщение` → ответ HR из кабинета → следующий ответ на опрос. HR-переписка не блокирует опросы; ответ HR содержит исходное сообщение. `/resume` показывает текущий вопрос, `/cancel` отменяет однократный ввод HR/ИИ. Список команд при `/` сохраняется на стороне MAX; после регистрации иногда нужно переоткрыть чат.

## 3. Проверки, обновление и остановка

```powershell
docker compose logs --tail 50 api worker
docker compose exec api python -m pytest -q -p no:cacheprovider --basetemp=/tmp/korsik-check
```

Тесты используют временные БД и mock MAX/ML. Последний полный прогон: **95 passed, 1 skipped**; PostgreSQL-тест запускается отдельно. Для собственного изолированного development-стенда: `docker compose exec api python scripts/check_postgres.py` — создаст схему `korsik_validation`, проверит миграции и очередь. Не запускать этот скрипт на production.

Обновить существующий стенд:

```powershell
git pull --ff-only
docker compose --profile max-poll up -d --build
docker compose ps
```

Для mock-стенда не добавлять профиль `max-poll`. Перед миграциями рабочей БД сделать резервную копию по следующему разделу. На текущем ноутбуке есть копии в `back/data/backups`; они не входят в Git.

Остановка: `docker compose --profile max-poll stop`. Возобновление — прежняя команда `up`. `docker compose down` удаляет контейнеры, сохраняя volumes; `down -v` удаляет также БД и документы. Для совместной работы держать один worker доставки.

## 4. Продолжить текущий стенд с прежними пользователями

Git переносит код. Текущие профили, MAX-привязки, приглашения, сообщения, документы и ключи находятся в БД/volume и **не входят в коммит**. Для продолжения с ними нужно приватно передать свежий дамп PostgreSQL и архив `app_data`; токен MAX передать отдельно. Для чистого демо этот раздел не нужен.

На исходном стенде, PowerShell:

```powershell
New-Item -ItemType Directory -Force handoff-private | Out-Null
docker compose --profile max-poll stop api worker poll
docker compose exec -T db pg_dump -U korsik -d korsik -Fc -f /tmp/korsik.dump
docker compose cp db:/tmp/korsik.dump ./handoff-private/database.dump
$handoffPath = (Resolve-Path ./handoff-private).Path
docker run --rm --network none --mount type=volume,source=korsik_app_data,target=/source,readonly --mount "type=bind,source=$handoffPath,target=/backup" postgres:17-alpine tar -czf /backup/app_data.tar.gz -C /source .
```

Передать оба файла защищённым способом. `handoff-private/` исключён из Git. Он содержит личные данные и ключи учебных аккаунтов. Если перенос пока откладывается, можно возобновить прежний стенд командой `up`; перед окончательным переключением сделать свежий согласованный снимок и оставить старый poll остановленным.

На **новом пустом стенде**: создать свой `.env` по разделу 1, положить два файла в `handoff-private/`. До восстановления не запускать `seed-demo` и полный Compose.

```powershell
docker compose up -d db
docker compose exec -T db pg_isready -U korsik -d korsik
docker compose cp ./handoff-private/database.dump db:/tmp/korsik.dump
docker compose exec -T db pg_restore -U korsik -d korsik --exit-on-error --no-owner --no-privileges /tmp/korsik.dump
docker volume create korsik_app_data
$handoffPath = (Resolve-Path ./handoff-private).Path
docker run --rm --network none --mount type=volume,source=korsik_app_data,target=/target --mount "type=bind,source=$handoffPath,target=/backup,readonly" postgres:17-alpine tar -xzf /backup/app_data.tar.gz -C /target
```

Дождаться готовности PostgreSQL перед восстановлением; при неготовности повторить `pg_isready`. Затем задать полученный приватно MAX-токен, проверить остановку старого poll и запустить `docker compose --profile max-poll up -d --build`.

Новый PostgreSQL использует пароль, с которым создан его новый volume. Дамп схемы/данных не требует повторения старого пароля. Для уже заполненного стенда не применять команды восстановления поверх данных — сначала нужен отдельный план переноса.

## 5. Внешний ML и Mini App

ML-контракт: `docs/ml-contract.md`, `contracts/ml-v1.schema.json`; ожидаются `/v1/answer`, `/v1/analyze`, `/v1/documents`, `/v1/documents/{id}`. В `.env`: `KORSIK_ML_MODE=http`, URL и ключ. В контейнере `localhost` — сам контейнер; сервис на Windows/macOS-хосте обычно доступен через `http://host.docker.internal:9000`, в одной Docker-сети — по имени сервиса. Linux требует соответствующей сетевой настройки. При недоступности ML бот честно отказывает и передаёт вопрос HR.

Для Mini App нужен публичный доверенный HTTPS: зарегистрировать `/app` в настройках бота и задать `KORSIK_WEBAPP_PUBLIC_URL=https://ваш-домен/app`. Пока работает локальный кабинет. Вход в Mini App разрешается только привязанному администратору/HR после проверки подписи; одной ссылки недостаточно.

Production overlay требует реальных MAX/HTTP ML, PostgreSQL, сильного webhook-секрета, ML-токена и отключённого demo. Сначала настроить HTTPS reverse proxy, затем `python -m korsik.cli subscribe https://ваш-домен/webhooks/max`. В production polling запрещён. Текущий локальный стенд с заглушкой не следует объявлять production.

Python без Docker: инструкция в корневом README. Детали HR-процессов — `docs/hr-guide.md`; архитектура — `docs/architecture-v2.md`. Сверки с кейсом и системным анализом передаются отдельно и не хранятся в Git.
