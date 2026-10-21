# Языковые модули и миграция компонентов — дизайн (spec #4, волна 2)

- Статус: draft, на ревью
- RFC: [#393](https://github.com/quokkify/ci-kit/issues/393)
- Опирается на: spec #1 (`2026-10-12-modular-ci-templates-design.md`), spec #2 (`2026-10-12-modular-ci-migration-design.md`), `docs/modules.md`
- Дата: 2026-10-21

## 1. Контекст

Волна 1 перевела 7 потребителей в `hybrid(validate)` (модуль `core`, required check `ci / gate`); итоги §11 spec #2 — в #393. На legacy validate остались 4 репозитория: у каждого есть языковые `components`, поэтому `migrate` отказывает (`CIK-MIGRATE-005`), а fleet помечает их `not-applicable`.

| Репозиторий | legacy `components` / флаги | Примечание |
|---|---|---|
| skills | python | свой `test-command`; Allure (component), Pages |
| knowledge | `[]` после knowledge#49 | в `validate.yml` только `template-contract`; приватный, rulesets недоступны (403) |
| marketdesk | node, `docker: true` | node: `node-version: 24`, свои lint/test/build; ручной job `java-test-automation` (`java-ci`, Java 21, `test-automation/`); ручной агрегатор `complete` в required checks; Allure external через `workflow_run` на `Validate` |
| q4j | java, node, python | все три — заглушки (`true`, `gradlew help`); реальные тесты в `test.yml`; Allure external, медленный pilot |

Потребители правят `with:` legacy-jobs руками, поэтому поведение CI определяется фактическим `validate.yml`, а не answers.

Сейчас в ci-kit есть модули `core` и `java`; `java` не использует ни один потребитель. Reusable `.github/workflows/validate.yml` знает только job `java`, `gate.needs` захардкожен, `tools/ci_kit/cli.py` отдаёт только матрицу `java`. `migrate` копирует ровно один модуль.

Область `validate` — один файл. Legacy `validate.yml` содержит все jobs компонентов, поэтому репозиторий с компонентами мигрирует атомарно: `core` и все его языковые модули в одном PR.

## 2. Цель

Ни один потребитель не использует legacy validate. Для этого:

- модули `python`, `node`, `docker` по образцу `java`;
- `migrate`, который выводит набор модулей из legacy answers и ставит его одним PR;
- раскатка подволнами 2a (python), 2b (node + docker), 2c (q4j).

## 3. Не цели

- Удаление legacy validate из `template/` — отдельный follow-up после волны 2.
- Модули `security`, `release`, `allure`, `copier-update`; spec #3 (Renovate).
- Публикация docker-образов: модуль `docker` воспроизводит legacy (`push: false`, без secrets).
- Изменение API `java-ci.yml`, `python-ci.yml`, `node-ci.yml`, `docker-build.yml`.
- #450 (Allure external у car-service-platform) — своим путём.

## 4. Решения

| # | Решение | Причина |
|---|---|---|
| L1 | Отдельный модуль на язык (`python`, `node`) и `docker`, по образцу `java`. | Spec #1: независимое обновление языков — цель RFC. |
| L2 | `migrate` выводит набор модулей и данные компонентов из фактических jobs legacy `validate.yml` (`uses:` + `with:`); оператор по-прежнему запускает `--migrate core`. | Ручные правки `with:` и ручные `uses:`-jobs — реальное поведение CI, answers его не содержат; fleet почти не меняется. |
| L3 | Один migrate-PR, по commit на модуль, фиксированный порядок `core → java → python → node → docker`. | Атомарная передача файла `validate.yml`; читаемая история. |
| L4 | Загрузка Allure-артефакта определяется наличием `allure_results_path` у компонента; имя артефакта — `allure_artifact_name`, по умолчанию `allure-results-<id>`. | Миграция переносит путь и имя ровно там, где legacy загружает артефакт; имена, на которые подписан Allure external, сохраняются. |
| L5 | `CIK-MIGRATE-005` меняет смысл: «в legacy `validate.yml` есть job, который нельзя сопоставить» — без `uses:` (например, агрегатор `complete`) или с `uses:` неизвестного workflow. | Компоненты и ручные `uses:`-jobs известных типов переносятся; job с собственными steps модуль воспроизвести не может. |
| L9 | Для `validate.yml` `CIK-MIGRATE-003` сужается: с legacy-рендером сравниваются всё, кроме jobs, и job `template-contract`; остальные jobs проверяет сопоставление (§7.1) и эквивалентность (§7.2 шаг 4). Для прочих файлов области — побайтно, как сейчас. | Сейчас 003 требует побайтного совпадения всего файла, и ручные `with:` (skills, marketdesk, q4j) — то, что волна 2 переносит, — блокировали бы миграцию. Триггеры, permissions и контракт по-прежнему защищены: их ручные правки модули потеряли бы. |
| L8 | Декларации языковых модулей зеркалят входы вызываемого workflow (snake_case); незаданное поле не передаётся. | Сохраняет ручные команды без собственной семантики полей; API workflows не меняется. |
| L6 | Runtime-доказательство — CI самого migrate-PR у потребителя; отдельного sandbox нет. | Новый reusable validate исполняется до merge; красный PR не мёржится, откатывать нечего. |
| L7 | Добавление необязательных полей в существующую схему `java.v1` без смены версии. | Поле необязательное; уже валидные декларации остаются валидными. |

## 5. Модули

Каждый модуль: шаблон `modules/<name>/template`, декларация `.ci/modules/<name>.yml`, схема `tools/ci_kit/schemas/<name>.v1.json`, запись в `MODULES` (`tools/ci_kit/modules.py`), answers-файл `.copier-answers.<name>.yml`.

| Модуль | Декларация `components[]` | `requires` | Job в gate | Copier-вопрос |
|---|---|---|---|---|
| `python` | `id`, `name`, `path`, `python_version?` + общие поля | `core` | `python` | `python_components` |
| `node` | `id`, `name`, `path`, `node_version?` + общие поля | `core` | `node` | `node_components` |
| `docker` | `id`, `context?`, `dockerfile?`, `target?` | `core` | `docker` | `docker_images` |
| `java` (есть) | + общие поля, `build_tool?`, `distribution?` | `core` | `java` | `java_components` |

Общие поля языковых модулей (L8), каждое необязательно и соответствует входу workflow с тем же именем в kebab-case: `package_manager`, `install_command`, `lint_command`, `test_command`, `build_command`, `run_lint`, `run_test`, `run_build`, `allure_results_path`, `allure_artifact_name`.

- Незаданное поле не передаётся в вызываемый workflow — действуют его умолчания.
- Новые проекты получают только `id`, `name`, `path` и версию; остальное нужно для переноса ручных правок.
- Зарезервированные `id` общие для всех модулей: `plan`, `core-checks`, `gate`, `java`, `python`, `node`, `docker`. `id` уникальны во всех декларациях проекта; конфликт — ошибка `ci-kit plan`.
- `copier.yml`: в `module` добавляются `python`, `node`, `docker`; новые вопросы валидируются так же, как `java_components`.

## 6. Reusable validate

- `ci-kit plan` (`cli.py`) отдаёт матрицы `java`, `python`, `node`, `docker`; выключенный модуль — `[]`.
- Новые jobs `python`, `node`, `docker`: `if: needs.plan.outputs.<type> != '[]'`, matrix по компонентам, фиксированный `uses: ./.github/workflows/<type>-ci.yml` (`docker-build.yml` для docker), без динамического `uses` (D5).
- Входы: `working-directory: path`; каждое заданное поле декларации — в одноимённый kebab-case вход; при `allure_results_path` — `upload-test-artifacts: true`, `test-artifact-path`, `test-artifact-name: allure_artifact_name || allure-results-<id>`. Docker: заданные `context`, `dockerfile`, `target` и `push: false`.
- GitHub Actions не умеет «не передавать» вход условно, поэтому незаданное поле передаётся как пустая строка/`null`, а умолчания workflows переносятся в выражение (`matrix.component.test_command || '<умолчание>'`). Тест сверяет эти умолчания с `inputs.*.default` вызываемых workflows, чтобы копии не разошлись.
- Отображаемое имя job — `name` компонента, как в legacy.
- `gate.needs` += `python`, `node`, `docker`. Skipped (модуль выключен) допустим; failure и cancelled проваливают gate, как сейчас.
- Имя caller'а `Validate` не меняется (spec #2 §7.4).

## 7. `ci-kit migrate`

### 7.1 Сопоставление

`legacy_to_modules(validate_yml) -> list[(module, data)]` в `tools/ci_kit/migrate.py` — чистая функция над разобранным legacy `validate.yml`:

- `template-contract` пропускается (его заменяет `core`);
- job с `uses: quokkify/ci-kit/.github/workflows/<type>-ci.yml@…` → компонент `<type>_components`: `id` = id job, `name` = `name` job (или id), `path` = `working-directory`, прочие `with:` → поля L8 (`<type>-version` → `<type>_version`);
- `upload-test-artifacts: true` → `allure_results_path` = `test-artifact-path`, `allure_artifact_name` = `test-artifact-name`, если оно отличается от `allure-results-<id>`;
- java без `java-version` → `java_version: "17"` (умолчание `java-ci`, которое legacy использует неявно);
- job с `uses: …/docker-build.yml@…` → `docker_images` с `id` job и его `context`/`dockerfile`/`target`; `with:`, кроме этих и `push: false`, → `CIK-MIGRATE-005`;
- вход `with:`, которого нет среди полей L8, → `CIK-MIGRATE-005` с именем входа;
- любой другой job → `CIK-MIGRATE-005` (L5);
- результат всегда начинается с `core`; языковые модули — только непустые.

Ручные `uses:`-jobs известных типов (marketdesk `java-test-automation`) переносятся как компоненты; answers для сопоставления не нужны.

### 7.2 Шаги (один PR, commit на шаг)

1. `copier update` legacy с `handed_off += [validate]`, удаление `validate.yml` (как сейчас).
2. `copier copy` модуля `core`.
3. `copier copy` каждого модуля из сопоставления в порядке L3, данные через `-d <question>=<json>`.
4. Проверка эквивалентности: для каждого legacy-job набор `(workflow, name, with:)`, который отрендерит reusable validate по декларациям, равен legacy-набору (с поправкой на умолчания §6). Расхождение — отказ до push.

### 7.3 Отказы

- `CIK-MIGRATE-005` (новый смысл, L5): job или вход, который нельзя сопоставить; вывод называет job/вход. Примеры: агрегатор `complete` в marketdesk (и в car-service-platform в волне 1). Исправление — подготовительный PR в потребителе (удалить job, сменить required check), как car-service-platform#275.
- `CIK-MIGRATE-003` по L9: для `validate.yml` — расхождение с legacy-рендером вне component-jobs (триггеры, `permissions`, `concurrency`, `template-contract`); для прочих файлов области — как сейчас.
- Остальные предусловия spec #2 §7.1 без изменений (`needs-update-first` и т.д.).

### 7.4 Required checks (`scripts/migration_checks.py`)

- `checks_after` = `["ci / gate"]` для любого набора модулей.
- `checks_before` выводится из legacy: `Shared template contract` плюс контексты ruleset, начинающиеся с `<name job> / ` для каждого сопоставленного job (компоненты, ручные `uses:`-jobs, docker). Сейчас скрипт падает на `uses:`-jobs.
- Контексты, не порождённые legacy validate, не трогаются (#446).

## 8. Fleet

- `scripts/update_copier_fleet.py`: удалить фильтр `language components` и правило «одиночный `CIK-MIGRATE-005` → `not-applicable`»; 005 — отказ с кодом в отчёте.
- Тело migrate-PR: список устанавливаемых модулей и таблица legacy job → module/component.
- Режим `update` для гибридных репозиториев с несколькими модулями уже поддержан (`ci-kit update`, порядок по `language`); проверяется тестом.

## 9. Порядок работ и раскатка

**ci-kit:**

1. PR 1 «модули»: §5, §6, сценарии `tests/scenarios/modules/`. Релиз. Legacy-потребителей не затрагивает.
2. PR 2 «миграция»: §7, §8, `docs/modules.md`, `docs/errors/CIK-MIGRATE-005.md`. Релиз.

**Потребители** — каждый внешний шаг только с подтверждения владельца. На каждый PR: dry-run → зелёный `ci / gate` → `migration_checks.py --dry-run` → без `--dry-run` → merge.

| Подволна | Репозитории | Что проверяется |
|---|---|---|
| 2a | skills; knowledge | python, ручной `test-command`, Allure-артефакт и Pages (skills). knowledge: если dry-run подтверждает отсутствие компонентов — обычная миграция волны 1 (можно сразу, до PR 1); rulesets нет |
| 2b | marketdesk | подготовительный PR: удалить `complete`, required check → `Shared template contract`; затем migrate: node с ручными командами, ручной `java-test-automation` → `java`, docker, Allure external через `workflow_run` на `Validate` |
| 2c | q4j | первый реальный `java`, три языка-заглушки, медленный pilot. Владелец может вместо этого удалить заглушки из answers и мигрировать как волна 1 — решение перед 2c |

Между подволнами: следующий релиз ci-kit проходит fleet `update` по мигрированным без ручных правок.

## 10. Тестирование

- Unit: `legacy_to_modules` на фикстурах legacy `validate.yml` в формате skills / marketdesk / q4j: ручные команды, ручной `uses:`-job, docker, Allure on/off и нестандартное имя артефакта, неизвестный вход и job без `uses:` → `CIK-MIGRATE-005`.
- Умолчания §6 совпадают с `inputs.*.default` в `java-ci.yml`, `python-ci.yml`, `node-ci.yml`, `docker-build.yml`.
- Схемы и `ci-kit plan`: валидные и невалидные декларации каждого нового модуля, конфликт `id` между модулями, `requires`.
- `tests/test_modular_validate_workflow.py`: jobs `python`/`node`/`docker`, их `if`, `needs` gate, входы Allure.
- Render: `tests/scenarios/modules/` — `core-python`, `core-node-docker`, `core-polyglot` (java + node + python); существующий `core-java` не меняется.
- Lifecycle (`tests/test_migration_lifecycle.py`): legacy-проект с компонентами и ручными правками `with:` → `migrate` → проверка эквивалентности §7.2 → повторное применение без diff → откат по `docs/modules.md`.
- Отказы: job без `uses:` → `CIK-MIGRATE-005`; ручная правка триггеров или `template-contract` → `CIK-MIGRATE-003`; ручная правка `with:` component-job → не отказ, переносится.
- `migration_checks.py`: вывод `checks_before` для компонентов и docker; чужие контексты сохраняются.
- Fleet: репозиторий с компонентами получает `would-migrate`, ручной job — отказ с кодом.
- Runtime — CI migrate-PR у потребителя (L6).

## 11. Критерии готовности волны 2

1. skills, knowledge, marketdesk, q4j не используют legacy validate.
2. Один релиз ci-kit проходит fleet `update` по всем 11 потребителям без ручных правок.
3. Ручное вмешательство в migrate-PR — только отказы `migrate` с кодом.
4. Allure-отчёты и Pages skills, marketdesk, q4j публикуются после миграции так же, как до неё.

Итоги фиксируются комментарием в #393.
