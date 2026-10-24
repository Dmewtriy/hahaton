# Корсик: техническая архитектура

Модульное приложение Python 3.12 / FastAPI / SQLAlchemy 2 / PostgreSQL 17. Alembic 0005. Процессы API, worker и poll используют одни use cases и БД; их можно запускать в отдельных контейнерах. Локально допускается SQLite для тестов. PostgreSQL — рабочий вариант для нескольких процессов.

```mermaid
flowchart TB
  employee[Сотрудник в MAX] --> max[MAX Bot API]
  hr[HR / руководитель в Mini App] --> bridge[MAX Bridge + signed initData]
  bridge --> api[FastAPI / API routers: подпись MAX и права]
  max --> poll[MAX poll adapter: normalize + ingest]
  poll --> jobs[(PostgreSQL Job: dedupe / leases)]
  api --> access[Доступ: 4 роли + manage_access + scope]
  access --> services[Общие application services]
  jobs --> worker[Worker: schedule / process / deliver]
  worker --> services
  services --> org[Сотрудники / площадки / подразделения / назначения]
  services --> flow[Scenario + RouteRun: календарь и действия]
  flow --> surveys[SurveyRun + SurveyAnswer / условия и дедлайн]
  services --> campaigns[HR campaigns / audience snapshot]
  campaigns --> surveys
  services --> support[Ticket / HR Inbox / HelpSession]
  services --> docs[Документы / версии / реестр материалов]
  services --> reports[DB analytics / CSV / XLSX / digests]
  services --> ml[ML port: StubML или HTTPML]
  ml --> rag[Внешний RAG/NLP коллег]
  rag -. кандидат callback .-> nlp[NLP event adapter / idempotency]
  nlp --> signals[Backend policy / HR case / meeting signal]
  signals --> services
  services --> db[(PostgreSQL: state, answers, grants, audit)]
  services --> out[(PostgreSQL Outbox)]
  worker --> out
  out --> guard[Повторная проверка прав, адресата и материалов]
  guard --> max
  docs --> files[(Private app_data: документы и медиа)]
  reports --> exports[(Private temporary exports)]
```

```mermaid
sequenceDiagram
  participant E as Сотрудник / MAX
  participant B as Backend + worker
  participant D as PostgreSQL
  participant ML as Внешний ML
  participant H as HR Mini App
  E->>B: incoming event_id / callback
  B->>D: Job unique(event_id)
  B->>D: lock Employee + сохранить состояние
  alt Ответ на опрос
    B->>D: SurveyAnswer / comment / position
    B->>D: Outbox следующего вопроса
  else /ask
    B->>D: HelpSession + точка возврата
    B->>ML: вопрос + история + разрешённые документы
    ML-->>B: answered / clarification / not_found
    B->>B: проверить источники и права
    alt Нет подтверждённого ответа
      B->>D: Ticket + HRMessage + уведомление HR
    else Ответ есть
      B->>D: Outbox «Удалось решить вопрос?»
    end
  else /hr текст
    B->>D: выбранный Ticket / сообщение, опрос не блокируется
  end
  H->>B: решение конкретного Ticket + Idempotency-Key
  B->>D: awaiting_confirmation / resolution_revision
  B-->>E: Ваш вопрос + Ответ HR + Да/Нет
  E->>B: подтверждение Ticket + актуальная ревизия
  B->>D: Да → closed; Нет → тот же Ticket в работе
```

```mermaid
erDiagram
  IDENTITY ||--o| EMPLOYEE : account
  BRANCH ||--o{ DEPARTMENT : contains
  BRANCH ||--o{ EMPLOYEE : site
  DEPARTMENT ||--o{ EMPLOYEE : membership
  IDENTITY ||--o{ EMPLOYEE : recruiter_or_manager
  SCENARIO ||--o{ SCHEDULED_STEP : snapshot
  EMPLOYEE ||--o{ SCHEDULED_STEP : assignment
  SCHEDULED_STEP ||--o| ROUTE_RUN : flow_state
  SCHEDULED_STEP ||--o| SURVEY_RUN : survey_state
  SURVEY_RUN ||--o{ SURVEY_ANSWER : responses
  SURVEY_ANSWER ||--o| FEEDBACK_ANALYSIS : latest_projection
  SURVEY_ANSWER ||--o{ NLP_EVENT : versions
  FEEDBACK_ANALYSIS ||--o| MEETING_SIGNAL : recommendation
  EMPLOYEE ||--o{ TICKET : cases
  TICKET ||--o{ TICKET_PARTICIPANT : scoped_grants
  EMPLOYEE ||--o{ HR_MESSAGE : history
  TICKET ||--o{ HR_MESSAGE : context
  EMPLOYEE ||--o{ HELP_SESSION : ai_help
  DOCUMENT ||--o{ MATERIAL : resource
  CAMPAIGN ||--o{ CAMPAIGN_RECIPIENT : fixed_audience
  CAMPAIGN_RECIPIENT ||--|| SCHEDULED_STEP : delivery
  IDENTITY ||--o{ EXPORT_FILE : authorised_snapshot
```

## Границы ответственности

- `access`, `employees`, `admin_service`: авторизация, область данных, назначения, приглашения и конфигурация без изменения Python-кода.
- `calendar`, `scenarios`, `routes`, `surveys`, `campaigns`: расписание, упорядоченные действия, снимки версий, условия, паузы, ответы, дедлайны и очередь сценариев.
- `support`, `tickets`, `inbox`, `helpflow`: обращения с ID, получатели, цитирование вопроса, ревизии решения и подтверждение сотрудником; состояние ИИ-помощи отдельно от опроса.
- `documents`, `profile_v04`, `Material`: оригиналы, публикация и индексация, реестр обязательных вложений; исходные строки привязаны через source_ref.
- `reporting`, `analytics`, `digests`, `feedback`, `meetings`: числа из БД, срезы, выгрузки, политика HR и приватные рекомендации встречи.
- `ml.py`, `nlp_api`: граница с внешним сервисом, а не реализация моделей. MAX-адаптер — `max_client`, `commands`, `bot`.

## Надёжность и ограничения

Состояние бизнес-операции и Outbox сохраняются одной транзакцией. Job/Outbox имеют уникальные ключи; несколько worker используют leases и PostgreSQL `FOR UPDATE SKIP LOCKED`. MAX является внешней системой: универсальной exactly-once доставки нет. При неизвестном результате отправки сохраняется `uncertain`; автоматический повтор может дублировать сообщение, поэтому требует отдельного решения. Перед доставкой заново проверяются доступ, назначенный получатель и обязательные файлы.

API не доверяет скрытию кнопок: проверяются роль, область площадок и конкретные данные. Руководитель не получает исходные ответы и NLP-объяснения. Download-ссылки подписаны и ограничены по времени, права проверяются повторно. В технических логах — route template, status, request ID, тип ошибки и внутренние ID; токены, query strings и пользовательские тексты не логируются.

Для роста: отделять worker по видам заданий; переносить приватные файлы в объектное хранилище через отдельный порт; добавлять индексы по реальным нагрузочным замерам; выносить отчёты в фоновые задания при больших выборках. Доменная логика и модель доступа при этом сохраняются. Redis/Celery и микросервисы сейчас не являются зависимостями проекта.
