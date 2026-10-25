# Предлагаемый контракт ML v1

Это подготовленный интерфейс backend для согласования с ML-коллегами. Он не предполагает, что их сервис уже реализован. Авторизация: `Authorization: Bearer <service-key>`. Запросы POST используют `Idempotency-Key`. Схемы: `contracts/ml-v1.schema.json`. HTTP 4xx/5xx, timeout или неправильный JSON обрабатываются как недоступность ML.

## POST /v1/answer

```json
{
  "request_id": "max:message_created:mid",
  "employee_id": "internal-opaque-id",
  "question": "Как получить доступ к порталу?",
  "history": [{"role": "user", "text": "Предыдущий вопрос"}],
  "step_context": {"stage": "equipment", "survey_id": "equipment", "question_index": 0},
  "access": {"branch_code": "GrPP", "category": "office"},
  "allowed_documents": [{"id": "document-id", "version": 1, "title": "Порядок доступа"}],
  "address_style": "formal"
}
```

```json
{
  "status": "answered",
  "answer": "Инструкция, подтверждённая документом",
  "sources": [{"document_id": "document-id", "version": 1, "page": 2}],
  "topics": ["access"],
  "needs_hr": false,
  "processor_version": "rag-2026-10-23-v1"
}
```

Статусы: `answered`, `not_found`, `clarification`. Для answered обязательны подтверждённые источники из allowed_documents. Backend не доверяет сторонним URL и сам показывает названия документов. Статус not_found не должен содержать предположительную инструкцию. Clarification — уточняющий вопрос, а не ответ без источника. Ответ по документам без подтверждённого источника из ACL недопустим: возвращайте `not_found`, а не clarification с полным текстом. Контекст опроса сохраняет backend, ML его не меняет.

Ограничения доступа должны применяться **при поиске до генерации ответа**, а не только к итоговому списку источников. Текст чужого филиала не должен попадать в prompt. Проверка backend служит дополнительным барьером. Ограниченная история содержит только текущего сотрудника и передаётся в порядке от старого к новому.

Опционально в ответе `/v1/answer` поле `images`: `[{"media_id","url","page","document_id"}]` — картинки с ML-моста (`GET /v1/media/{id}`), которые backend может вложить в MAX. Тема `manager_support` — явный сигнал для уведомления руководителя (в т.ч. из `/ask`).

## POST /v1/analyze

```json
{
  "request_id": "analyze:answer-id",
  "employee_id": "internal-opaque-id",
  "question": "Как вас встретили коллеги?",
  "text": "Текст ответа или условного комментария",
  "answer": {"kind": "rating", "answer": 5},
  "access": {"branch_code": "GrPP", "category": "office"}
}
```

```json
{
  "sentiment": "negative",
  "risk": "high",
  "topics": ["manager_support"],
  "explanation": "Объяснение сигнала на основе ответа сотрудника",
  "needs_hr": true,
  "processor_version": "sentiment-2026-10-23-v1"
}
```

Sentiment: positive/neutral/negative. Risk: none/low/medium/high. Версия обработчика обязательна и сохраняется вместе с результатом. ML обнаруживает признаки; backend применяет согласованные правила, назначает обращение и предотвращает дубли. Негативная тональность сама по себе не создаёт обращение без needs_hr и выполнения политики.

## POST /v1/documents

Multipart: `file` — оригинал, `metadata` — JSON-строка:

```json
{
  "document_id": "internal-document-id",
  "version": 1,
  "title": "Регламент",
  "sha256": "hex-checksum",
  "branch_codes": ["GrPP"],
  "categories": ["office"]
}
```

Ответ:

```json
{"status": "processing", "external_id": "index-job-id", "processor_version": "indexer-v1"}
```

Для готового синхронного результата status=ready. Для ошибки — failed. Внешний сервис должен идемпотентно принимать ключ `document:<id>:<version>` и не создавать повторный индекс при восстановлении backend worker.

## GET /v1/documents/{external_id}

Возвращает ту же схему статуса. Backend опрашивает раз в 30 секунд, максимум 240 раз. Ответы должны сохранять принадлежность external_id конкретному документу. Когда документ ready, его внутренний document_id и version должны использоваться в sources.

## Что остаётся у ML-команды

OCR обоих сканированных PDF, извлечение SmartArt и изображений презентаций, разбиение/поиск, генерация с источниками, проверка grounding, набор контрольных вопросов, оценка качества тональности и классификации проблем. Backend не заменяет эти функции правилом по ключевым словам. StubML возвращает явно помеченные фикстуры, включая специальный маркер DEMO:RED_FLAG.

Числовая аналитика всегда приходит из backend. Текстовый ML-дайджест можно добавить поверх готовых чисел отдельным контрактом; текущая недельная сводка формируется детерминированно и не выдумывает показатели.
