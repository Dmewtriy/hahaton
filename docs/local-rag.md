# Локальный RAG для свободных вопросов

Скриптовые шаги адаптации, опросы и рассылки Корсика не меняются. Внешний ML подключается только к свободным вопросам (`/ask`) и к анализу текстовых ответов через контракт `docs/ml-contract.md`.

Соседний репозиторий с GPU RAG: `../hakaton` (этот стенд — `hahaton`).

## Запуск

1. В окружении `torch-cuda` из папки `hakaton`:

```powershell
conda activate torch-cuda
cd D:\python_projects\hakaton
python -u scripts\ml_service.py
```

Сервис слушает `http://127.0.0.1:9000` и отвечает по маршрутам `/v1/answer`, `/v1/analyze`, `/v1/documents`.

2. В `.env` этого бота:

```env
KORSIK_ML_MODE=http
KORSIK_ML_BASE_URL=http://127.0.0.1:9000
KORSIK_ML_TOKEN=local-rag-token
KORSIK_ML_TIMEOUT_SECONDS=120
```

3. Перезапустить API и worker Корсика как обычно.

Пока `KORSIK_ML_MODE=stub`, бот работает как раньше со встроенной заглушкой.

## Поведение

| Ситуация | Ответ ML | Эффект в боте |
| --- | --- | --- |
| Вопрос по локальной базе знаний | `clarification` или `answered` с источниками из `allowed_documents` | Текст RAG уходит сотруднику |
| Зарплата / увольнение / кадровое | `not_found` + `needs_hr=true` | Текст «нет информации, к HR» и обращение к HR |
| Конфликт с коллегами | `clarification` + тема `manager_support` | Текст с пометкой к руководителю |
| Обычный чат | `clarification` | Короткий ответ модели без эскалации |

Индексация загрузок из кабинета HR через этот мост помечается как `ready` без повторной нарезки: локальный индекс по-прежнему наполняется `scripts/add_files.py` в `hakaton`.
