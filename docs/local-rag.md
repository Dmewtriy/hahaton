# Локальный RAG для свободных вопросов

Скриптовые шаги адаптации, опросы и рассылки Корсика не меняются. Внешний ML подключается только к свободным вопросам (`/ask`) и к анализу текстовых ответов через контракт `docs/ml-contract.md`.

Соседний репозиторий с GPU RAG: `../hakaton` (этот стенд — `hahaton`).

## Запуск

1. В окружении `torch-cuda` из папки `hakaton`:

```powershell
conda activate torch-cuda
cd D:\python_projects\hakaton
# 0.0.0.0 — доступ из Docker; для только localhost: $env:ML_HOST="127.0.0.1"
python -u scripts\ml_service.py
```

Сервис слушает `http://0.0.0.0:9000` (`/v1/answer`, `/v1/analyze`, `/v1/documents`, `/v1/media/...`).

2. В `.env` этого бота:

```env
KORSIK_ML_MODE=http
# С хоста Windows:
KORSIK_ML_BASE_URL=http://127.0.0.1:9000
# Из контейнера Docker Compose на Windows/Mac:
# KORSIK_ML_BASE_URL=http://host.docker.internal:9000
KORSIK_ML_TOKEN=local-rag-token
KORSIK_ML_TIMEOUT_SECONDS=120
```

На стороне ML для корректных URL картинок из Docker задайте:

```powershell
$env:ML_PUBLIC_HOST="host.docker.internal"
# или $env:ML_PUBLIC_BASE_URL="http://host.docker.internal:9000"
```

3. Перезапустить API и worker Корсика как обычно.

Пока `KORSIK_ML_MODE=stub`, бот работает как раньше со встроенной заглушкой.

## Поведение

| Ситуация | Ответ ML | Эффект в боте |
| --- | --- | --- |
| Вопрос по разрешённым документам | `answered` + `sources` из `allowed_documents` | Текст RAG и при наличии — картинки |
| Нет совпадения в ACL / нет источника | `not_found` + `needs_hr=true` | Честный отказ и обращение к HR |
| Зарплата / увольнение / кадровое | `not_found` + `needs_hr=true` | Текст к HR и тикет |
| Конфликт с коллегами | `clarification` + `manager_support` | Текст сотруднику + уведомление руководителю |
| Обычный чат | `clarification` | Ответ без эскалации |

Поиск выполняется **только** по документам из `allowed_documents` (до генерации). Сопоставление источников — по `korsik_document_id` или точному нормализованному заголовку, без «одного общего слова».

Загрузки из кабинета HR идут в `POST /v1/documents`, файл индексируется в локальный RAG; статус `processing` → `ready`/`failed`.
