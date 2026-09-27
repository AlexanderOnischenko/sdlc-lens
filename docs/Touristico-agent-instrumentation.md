# Инструментация агентов Touristico: план изменений

Статус: проект реализации · 27 сентября 2026  
Связанные документы: [архитектура](SDLC-observability-architecture.md), [ТЗ консоли](SDLC-observability-plane.md).

## 1. Главный принцип

Оставить LLM **семантические решения**: сформулировать MG, выделить LS/LP, оценить LC, классифицировать RV, решить маршрут NF, признать commit и объявить convergence. Детерминированную работу выполнить кодом: назначить технические ID, снять hashes и Git baseline, обнаружить изменённые артефакты, извлечь таблицы, посчитать дельты и счётчики, отправить события, связать runtime usage и проверить инварианты. Не добавлять в каждый skill длинное требование «заполни телеметрию».

Пилот допускает два режима. **Passive**: индексатор читает Git/Markdown, фиксирует наблюдаемые изменения и честно помечает неизвестные pass timings, no-op и usage. **Instrumented**: управляемый запуск через `sdlc-agent run` добавляет точные execution/pass boundaries. Оба режима дают один формат в control plane, но разный уровень полноты. Не надо запускать новый Codex процесс для каждого внутреннего шага только ради метрик: это может увеличить входной контекст и токены. Один процесс может содержать несколько pass; его границы тогда фиксируются в compact controller ledger или коротким вызовом helper.

## 2. Что уже есть и что менять

В `../Touristico/.codex/skills` владелец каждого артефакта определён: `self-healing-development-orchestrator` пишет `<name>.development-cycle.state.md`; `atomic-decomposer` — MG; `law-suite-curator` — LS/LP; `law-test-matrix-builder` — LC matrix; `reverse-law-auditor` — reverse audit; `delivery-orchestrator` — FI/WI и delivery state; `atomic-updater`/matrix builder — BG; `use-case-law-tracer` при необходимости пишет trace note и обновляет matrix/bugs. Реальные `offline_download` и `location-context` показывают: текущие документы уже содержат fingerprint, pass counters, NF/RV/PG, matrix status и commit links, но формат и глубина истории неоднородны.

Изменения сгруппированы так:

| Слой | Что сделать | Нужно ли менять prompt/skill |
| --- | --- | --- |
| `sdlc-agent` CLI/библиотека | обнаружение feature, capture baseline, UUID/ID, file hashing, snapshot, parser, diff, spool, отправка | нет |
| Запуск Codex | wrapper вокруг `codex exec --json` там, где запуск управляемый; локальный spool сырого JSONL и нормализатор | нет |
| Git/artifact watcher | passive snapshots committed и dirty files; reconcile после пропуска hook/краша | нет |
| Оркестратор | короткая ссылка на controller ledger и правило объявления/отзыва convergence | да, несколько строк |
| Owning skills | стабилизировать только обязательные машиночитаемые заголовки/ID уже создаваемых файлов | да, точечные правки, без общего event boilerplate |
| Codex hooks | необязательный session/turn correlation и асинхронный wakeup indexer | в skills нет; hook code/config отдельно |
| Control plane | парсинг, проверка provenance, проекции, coverage | нет |

## 3. Разделение ответственности по конкретным файлам

| Skill | Пишет как сейчас | Минимальная правка в skill | Что делает код после записи |
| --- | --- | --- | --- |
| `self-healing-development-orchestrator` | `<name>.development-cycle.state.md` | Добавить компактный `Observability` header: `cycle_id`, `current_generation_id`, `active_pass_id` при наличии, `baseline_id`, `format_version`. Записывать в существующий ledger только semantic trigger, status/route, точный safe resume point. | Сверить header с Git, выделить изменения статусов, поколений, NF/PG, проверить convergence и посчитать counters. |
| `atomic-decomposer` | `<name>.micro-guarantees.md` | Сохранить стабильные MG ID и явный `SUPERSEDED by`; не писать телеметрию. | Diff MG по версиям, определить добавленные/изменённые/superseded; hash нормативного текста. |
| `atomic-reviewer` | правит/проверяет MG; verdict в результате | Закрепить короткий verdict anchor в MG Contract Seal или controller ledger, если менялась семантика. | Извлечь verdict и связать с MG hash. Не считать сам факт запуска проверкой, если verdict не записан. |
| `law-suite-curator` | `<name>.law-suites.md` | Стабильные LS/LP ID и явные связи с MG; никаких event блоков. | Diff LS/LP и список затронутых LC. |
| `law-test-matrix-builder` | `<name>.law-test-matrix.md`; иногда BG | Для каждого LC сохранять status, evidence anchor, baseline/implementation fingerprint и обе независимые stale причины. Это содержательные факты, которые код не может вывести из Git diff. | Парсить LC rows, валидировать ID/ссылки и evidence, вычислить дельту статусов; не назначать green по MG `DONE`. |
| `reverse-law-auditor` | `<name>.reverse-law-audit.md` | Сохранять один pass ID, mode, input fingerprint и ровно один terminal outcome; явные RV IDs/class, включая `NOOP_SAME_INPUT`. | Extract pass/RV, проверить уникальность terminal outcome и сопоставить no-op с прежним input hash. |
| `architecture-review-guard` | решение обычно в controller/review state и отчёте | У решения оставить stable decision ID/anchor, outcome, мотив и owner-route. | Соединить raw finding → decision → NF и проверить, что новое semantic finding не пошло прямо в code. |
| `delivery-orchestrator` | `<name>.delivery-orchestration.md` | FI/WI→BG/LC/MG и accepted commit записывать явными ID, candidate и accepted различать. | Сверить commit SHA с Git, извлечь work unit history и candidates. |
| `atomic-committer`, `commit-reviewer` | код, тесты, commits | В commit message или delivery ledger дать стабильный WI/BG/LC anchor; без записи LC green. | Git scanner фиксирует parent/base/head, touched files, test logs по явным refs; matrix builder позже подтверждает LC. |
| `atomic-updater` | `<name>.guarantee-bugs.md` | Стабильные BG ID, explicit open/closed/reopened и MG links. | Считать уникальные BG и историю состояний. |
| `review-fix-orchestrator` | `<name>.review-fix-orchestration.md` | RF→FI/NF/decision связи явными ID; остальное без изменений. | Не объединять namespaces автоматически. |
| `use-case-law-tracer` | optional `<name>.use-case-law-trace.md`, matrix/bugs | Если самостоятельный запуск: одна короткая мета-строка `Trace run ID`, target scope, verdict. Если вызван внутри pass — ссылка на parent pass. | Считать только реально трассированные LC/LP по target registry; no-op/ошибка отдельно. |
| `exploratory-tester` | результат проверки среды | Evidence anchor и условия среды, без права менять LC status. | Индексировать witness; LC applicability ждёт matrix builder. |

Остальные skills (`feature-refiner`, `story-refiner`, `tdd_writer`, `behavioral-closure-refiner`, `guarantee-reconstructor`, `architecture-decision-maker`, `ux-refiner`, `write-runbook`, `use-case`) не требуют массовой правки. Их документы индексируются по обычным правилам; при изменении нормативного source оркестратор реагирует на hash/semantic review. Это сокращает prompt drift и затраты на сопровождение.

### Минимальный текст для правок skills

Не вставлять в каждый skill полную схему `sdlc.event.v1`. Достаточно следующих контрактных фраз в соответствующих местах:

```text
self-healing-development-orchestrator:
Keep the Observability header IDs provided by sdlc-agent. When declaring or
revoking CONVERGED, persist the baseline and exact reason in controller state.
Record a semantic generation trigger; do not infer it from a file hash alone.

law-test-matrix-builder:
For each assessed LC persist evidence anchor, checked baseline, status, and
independent SURFACE_STALE / SEMANTIC_STALE reasons when applicable.

reverse-law-auditor:
Persist one pass ID, mode, input fingerprint, and terminal outcome. For a
same-input no-op persist the previous equivalent pass ID.

delivery-orchestrator / atomic-committer:
Persist explicit WI/BG/LC IDs and accepted commit SHA; a candidate commit
and a green LC are separate decisions.

use-case-law-tracer:
For a standalone run persist target IDs, actually traced LC/LP IDs, verdict,
and run ID; nested work references its parent pass.
```

Технические UUID, timestamp, SHA-256, перечень изменённых файлов, counters и экономику модель не вычисляет и в ответе не переписывает. `sdlc-agent` хранит эти значения в собственном sidecar/spool; оркестратор как единственный writer controller state сохраняет лишь нужные ID-ссылки. Нормативные поля owning artifacts записывают их skills. Если ID в sidecar и controller state расходятся, helper оставляет diagnostic и не перезаписывает файл.

## 4. Код в Touristico и граница с control plane

Предлагаемая структура, до реализации подстроить под фактические conventions репозитория:

```text
Touristico/
  tools/sdlc_agent/             # stdlib-first Python package/CLI
    cli.py                      # run, snapshot, phase, reconcile, doctor
    identity.py                 # IDs, context, idempotency keys
    artifacts.py                # manifest, hashes, safe path discovery
    parsers/                    # versioned Markdown parsers by artifact type
    events.py                   # schema validation, canonical JSON, spool
    codex_jsonl.py              # versioned Codex exec event adapter
    git.py                      # committed/dirty snapshots, accepted commits
  .codex/hooks/                 # optional thin scripts; no business rules
  .sdlc/                       # ignored local spool/cache, never committed
```

`tools/sdlc_agent` — открытый, тестируемый код рядом с агентами; SDLC ingestion сервер может использовать те же versioned parsers как пакет или запускать идентичную версию в своём контейнере. `Touristico/.sdlc` добавляется в `.gitignore`; там хранятся raw JSONL, spool, local snapshots, locks и offsets с ограничением размера/retention. Если репозиторий запускается в среде без разрешения на `.sdlc`, использовать task-specific каталог данных вне repo, заданный config. Не хранить в Git prompts, модельные ответы, токены доступа и сырые tool outputs.

CLI команды и контракты:

```text
sdlc-agent snapshot --source docs/features/X/spec.md
sdlc-agent run --source docs/features/X/spec.md --skill law-test-matrix-builder -- codex exec --json ...
sdlc-agent phase start|finish|noop --skill <name> --scope-file <small-json>
sdlc-agent reconcile --source docs/features/X/spec.md
sdlc-agent doctor --source docs/features/X/spec.md
```

`snapshot` и `reconcile` ничего не пишут в canonical Markdown. `run` создаёт `execution_id`, фиксирует входной manifest, запускает Codex как дочерний процесс, tee-ит JSONL в bounded spool, сохраняет exit code и выходной manifest и создаёт технические execution events. `phase` нужен лишь если несколько skill passes идут в одной сессии и точные start/no-op/time действительно требуются; ID, timestamps и hashes вычисляет CLI. Модель передаёт только `skill`, scope и semantic outcome, которые она и так знает. При отсутствии `phase` indexer создаёт `artifact_change` и `pass_observed_partial`, не притворяясь, что знает время начала или число no-op.

Pre-processing `run`: resolve source/artifact neighborhood → validate IDs and current controller baseline → capture immutable manifest → allocate execution/optional pass IDs → pass модели только короткий context reference (например, путь к source и `pass_id`), без большого JSON/таблиц. Post-processing `run`: wait for process exit → capture immutable output snapshot → parse changed artifacts → derive factual deltas → compare controller decisions with owning files → append event batch → report короткий diagnostic path. Ни один post-processor не исправляет продуктовые решения за модель. При разногласии сохраняются обе версии и `source_conflict`.

### Безопасный commit/snapshot protocol

1. На старте `run` снять Git HEAD, tree, branch, dirty paths и **хеши нужных файлов**. Не читать весь репозиторий и не помещать manifest в prompt.
2. Каждую новую версию canonical артефакта снять из Git blob или из атомарной копии dirty file; ссылка на worktree path без content snapshot недостаточна — файл может измениться до ingestion.
3. После завершения снять выходной manifest, парсить только изменённые артефакты и связанный LC/LS neighborhood. Прежде чем принять `commit.accepted`, проверить, что SHA существует и controller/delivery ledger действительно признаёт его принятым; обычный `git commit` ещё не доказательство принятия в cycle.
4. События писать локально в атомарные сегменты: временный файл → flush/fsync → rename; один writer lock на spool segment. Отправка at-least-once, dedupe по стабильному `event_id`/`idempotency_key`, ack offset после серверного commit. При падении или offline — retry; несовпадение payload для того же key — quarantine.
5. При рестарте `reconcile` сравнивает Git/worktree versions, controller counters и event offsets. Он восстанавливает подтверждённые факты из файлов, но не синтезирует не наблюдавшийся `pass.started` или `LLM call`.

Для параллельных агентов каждый `run` имеет собственный `execution_id`, входной manifest и worktree/sandbox ID. Нельзя считать общий текущий HEAD базой обоих результатов после их завершения. Принятие результата делается через сравнение с controller baseline; stale outcome сохраняется в истории и не обновляет current readiness. Конкурентная запись одного controller state файла требует одного writer: оркестратор сериализует изменения либо интегрирует результаты из отдельных worktrees после проверки базовой версии.

## 5. Где применять Codex hooks

Официальные Codex hooks имеют `SessionStart`, `PostToolUse`, `Stop`, `SubagentStart/Stop` и другие события. `PostToolUse` охватывает Bash, `apply_patch` и MCP, но post-hook уже не может откатить выполненный tool; `Stop` запускается на конец **turn**, а не на конец SDLC pass, и может продолжить агента. `SessionEnd` может произойти позже закрытия работы. Не использовать их как единственный источник pass lifecycle. Hooks для project `.codex` работают только в trusted project; изменённые non-managed hooks требуют review/trust. В hook-конфигурации `prompt`/`agent` handlers не исполняются, поэтому здесь нужны только command handlers. [OpenAI Docs: Hooks](https://learn.chatgpt.com/docs/hooks).

| Hook | Допустимое использование | Ограничение |
| --- | --- | --- |
| `SessionStart` | записать `session_id`/cwd в локальный correlation map; максимум одна короткая строка контекста при необходимости | Не начинать cycle/pass по факту входа в Codex |
| `SubagentStart/Stop` | зафиксировать parent/child actor IDs и технический runtime interval | Не считать тип subagent равным skill или успешно завершённой проверке |
| `PostToolUse` | асинхронно разбудить watcher при изменении relevant path; без stdout для модели | Не парсить каждый shell output и не запускать полный scan после каждого tool |
| `Stop` | flush/checkpoint spool, сохранить turn ID; вернуть нейтральный JSON | Не выдавать `decision:block`, не объявлять convergence/pass complete |
| `SessionEnd` | best-effort flush с коротким timeout | Не зависеть от него для сохранности данных |

Рекомендуемый MVP — **вообще без repo hooks**: wrapper + файловый/Git watcher. Добавлять hook лишь если измеренная потеря корреляции в интерактивных сессиях оправдывает его стоимость и сложность. Если hook включён, обрабатывать вход как недоверенный JSON, читать только allowlisted поля, не логировать `prompt`, `last_assistant_message` или полный `tool_output`, никогда не передавать сырой hook payload в модель и не делать сетевой запрос в синхронном hook. Конфигурация Codex OTel берётся из user/managed config: проектный `.codex/config.toml` игнорирует `otel` [OpenAI Docs: Configuration Reference](https://developers.openai.com/codex/config-reference).

## 6. Точность pass, no-op и usage

Различать `execution` (один запуск Codex), `attempt` (попытка этапа), `pass` (измеримая работа stage на baseline), `no-op` (решение пропустить идентичный input) и `turn` (единица Codex runtime). Один execution может включать много turns и passes. Один pass может пережить compaction или несколько turns. Число LLM calls не равно ни одному из этих чисел.

| Источник | Что можно утверждать | Что нельзя утверждать |
| --- | --- | --- |
| `sdlc-agent run` + `phase` | точные boundaries, ID, parent links, exit/outcome, input/output baseline | semantic green без owning artifact |
| `sdlc-agent run` без `phase` | runtime execution, JSONL turns, changed artifacts, итог процесса | точный разрез общего usage между внутренними skills |
| Только artifact watcher | versions/дельта MG, LS, LC, NF, BG, commits; observed time | начало pass, no-op, реальный исполнитель, нулевой расход |
| Codex OTel/JSONL | trace/turn/tool signals, reported usage где оно есть | доказанное число provider calls и окончательный счёт |

`codex exec --json` официально выдаёт JSONL с `thread.started`, `turn.started/completed/failed`, `item.*`, `error`; пример `turn.completed` содержит input/cached/output/reasoning usage [OpenAI Docs: Non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode). Адаптер хранит `codex_version` и raw line hash, нормализует только известные типы; неизвестные события не ломают запуск. Usage из `turn.completed` — `turn_reported`, а не `call_exact`. Разделение input/cached/output должно избегать двойного счёта. Для subscription без per-call тарифа показывать токены и неизвестную денежную стоимость. Время и токены общего execution без точного `phase` остаются overhead или unattributed, а не распределяются эвристикой по последнему изменённому MG.

Для no-op есть две надёжные возможности: оркестратор пишет `NOOP_SAME_INPUT` с input fingerprint в reverse artifact либо управляемый launcher/`phase noop` фиксирует совпадение manifest и ссылку на прошлый pass. Отсутствие файловой дельты **не равно** no-op: агент мог исследовать код, не сохранить результат или упасть. Historical counters из controller state импортируются как агрегаты без фиктивных per-pass rows.

## 7. Валидация без токенов модели

`sdlc-agent doctor` выдаёт машинный diagnostic JSON и короткий human summary. Проверки:

- дублирующиеся ID внутри namespace/feature, битые ссылки MG→LS→LC, RV→NF→BG, `superseded_by` на несуществующий MG;
- controller generation и нормативный hash: generation не растёт от обычного commit; изменённый hash при неизменном generation требует semantic review, поскольку код не определяет смысл текста;
- LC `green` с отсутствующим evidence, старым implementation/semantic baseline или открытой stale причиной;
- convergence при нерешённом NF/RV/BG, stale LC, отсутствии final full reverse или изменённом baseline;
- accepted commit без существующего Git SHA или без WI/LC anchor;
- pass terminal без start в instrumented режиме, два terminal для одного pass, no-op без fingerprint, counters не равны известным pass rows (с учётом historical_partial);
- потеря event spool, parse warning, unknown Codex event, usage без надёжной attribution, доля неоценённого расхода;
- две конкурентные записи одного controller state на разных base hashes.

`doctor` **не переписывает** нормативные файлы и не меняет статус. Он публикует diagnostic с source anchors. Блокировать текущую работу можно только на существующих продуктовых/процессных gates skills; неисправный observability pipeline создаёт `data_quality=partial` и alert оператору, а не ложный `BLOCKED_ENVIRONMENT` фичи.

## 8. Порядок внедрения и критерии

1. Написать `snapshot`, парсеры и `doctor` на реально существующих `offline_download` и `location-context` без правок skills. Проверить, что исторические агрегаты не превращаются в выдуманные passes.
2. Добавить `run` wrapper, durable spool и Codex JSONL normalizer. Один контролируемый запуск должен давать execution, входной/выходной baseline, известные artifact changes и `turn_reported` usage без участия модели в сборе.
3. Добавить компактный `Observability` header/ledger в controller state и точечные поля в matrix/reverse/delivery skills. Проверить round-trip parser и старые файлы без новых полей.
4. Добавить `phase` только для внутренних проходов, по которым консоли требуются точные времена/no-op/стоимость. Измерить стоимость дополнительного tool call и оставить passive режим там, где точность не окупается.
5. Подключить ingestion и Phoenix; затем, при необходимости, минимальные hooks для interactive correlation. Не включать сбор содержимого prompts/tool outputs по умолчанию.

Приёмка: после падения и повтора запуска нет потери/дублирования событий; два параллельных execution не закрывают устаревший baseline; no-op отделён от отсутствия изменений; одна BG, связанная с несколькими MG, считается один раз; матрица остаётся единственным владельцем LC status; usage показывается с уровнем покрытия; общий расход не «размазывается» по MG без подтверждённого scope. Объём новых обязательных инструкций для модели ограничен несколькими полями в owning artifacts и коротким правилом orchestration; остальная логика проверяется unit/fixture tests обычного кода.
