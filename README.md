# SDLC Lens

Пассивный сбор состояния Codex/SDLC-процесса из **существующих** артефактов Touristico. Сервис только читает исходный репозиторий. Он сохраняет снимки файлов, извлекает MG/LS/LP/LC/BG/NF/RV/PG/FI/WI и статусы их владельцев, показывает изменения между снимками через read-only API и простую web-консоль. Агенты и их skills не меняются.

## Быстрый запуск

Из каталога `sdlc-lens`:

```bash
python3 -m pip install -e '.[test]'
sdlc-lens --repo ../Touristico scan
sdlc-lens --repo ../Touristico serve
```

Откройте `http://127.0.0.1:8080`. `serve` делает начальный scan и затем проверяет файлы каждые 10 секунд. Локальная БД по умолчанию — `.local/sdlc-lens.db` (SQLite, игнорируется Git). Чтобы использовать PostgreSQL, установите extra `postgres` и передайте `--database-url postgresql+psycopg://...` или `SDLC_LENS_DATABASE_URL`. Артефакты в source repo должны быть доступны на чтение; запись туда сервису не нужна.

Для контейнерного запуска укажите путь к Touristico и поднимите сервисы:

```bash
TOURISTICO_PATH=../Touristico docker compose up --build -d
```

Compose использует PostgreSQL для SDLC-снимков и монтирует Touristico как `:ro`. По умолчанию HTTP доступен только на `127.0.0.1:8080`; локальный пример пароля БД нужно заменить перед доступом извне. Контейнерный запуск не включает Phoenix автоматически.

## Опциональные трассировки

```bash
TOURISTICO_PATH=../Touristico docker compose --profile tracing up --build -d
```

Это поднимает Phoenix (`127.0.0.1:6006`) и OTLP/HTTP Collector (`127.0.0.1:4318`). Подключение телеметрии Codex выполняется в **пользовательском или управляемом** Codex config, а не в Touristico skills: проектный `.codex/config.toml` игнорирует `otel` [OpenAI Docs: Configuration Reference](https://developers.openai.com/codex/config-reference). Настройка OTLP trace exporter на `http://127.0.0.1:4318/v1/traces` позволяет смотреть runtime traces в Phoenix. Файловый коллектор не выдумывает трассировки и не привязывает их к pass по одному времени события. В текущем релизе связь trace→MG/LC и точная стоимость LLM-вызовов не реализованы: в API они остаются `unknown`.

## API

| Endpoint | Данные |
| --- | --- |
| `GET /api/health` | Последний scan и время проверки |
| `GET /api/features` | Список фич, объявленный controller status, счётчики и полнота |
| `GET /api/feature?key=...` | Артефакты и сущности одной фичи |
| `GET /api/entities?key=...&kind=LC&status=PARTIAL` | Фильтрованный список с `limit`/`offset` |
| `GET /api/entity?key=...&kind=LC&entity_id=LC-001` | Сущность, поля и ссылка на исходный снимок |
| `GET /api/changes?key=...` | Изменения между сканами |
| `GET /api/scans` | История снимков |
| `GET /api/search?q=LC-001` | Поиск по ID и краткому описанию |
| `GET /api/data-quality` | Предупреждения, dirty files, покрытие проходов и usage |
| `GET /api/artifacts/{id}` | Неизменяемое содержимое версии файла |

Для `/api/features`, `/api/feature`, `/api/entities` и `/api/entity` можно передать `scan_id` из `/api/scans`, чтобы увидеть состояние на момент сохранённого снимка. По умолчанию возвращается последний снимок.

Все значения имеют `artifact_id`, `path`, `line`, `content_hash` и признак `dirty`. `dirty=true` означает содержимое текущего worktree, не подтверждённое Git HEAD. Парсер сохраняет ссылки из текста с `confidence=text_reference`: это навигационные кандидаты, а не доказанные causal edges. Сервис сравнивает SHA-256 source/MG/LS/matrix/reverse с fingerprint в controller state и показывает расхождения; релевантную реализацию он этим не проверяет. Статус `CONVERGED` отображается как **заявление контроллера**, readiness остаётся `not_independently_verified`. Исторические проходы, no-op и токены нельзя восстановить из текущего Markdown, поэтому полнота явно отмечена как частичная.

Коллектор создаёт новый снимок только при изменении содержимого, Git HEAD или parser version. Повторный scan без изменений обновляет `last_checked_at`. Непрочитанный/меняющийся во время чтения файл не публикует неполный снимок; watcher попробует снова. Первое чтение уже существующего репозитория помечает сущности `baseline_import` — оно **не** утверждает, что они появились в момент первого scan. При обновлении parser version изменения помечаются `parser_reindex`. Последующие сканы показывают реальные наблюдённые дельты. Незаписанные раньше промежуточные состояния остаются неизвестными.

## Проверка

```bash
python3 -m pytest -q
docker compose config
```

Архитектурные границы и план дальнейшей инструментации описаны в [архитектуре](docs/SDLC-observability-architecture.md) и [плане изменений агентов](docs/Touristico-agent-instrumentation.md). Текущая реализация намеренно использует только пассивный режим этого плана.
