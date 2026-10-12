# Модульные CI-шаблоны и центральный validate — дизайн (spec #1)

- Статус: draft, на ревью
- RFC: [#393](https://github.com/quokkify/ci-kit/issues/393)
- Дата: 2026-10-12

## 1. Контекст и проблема

Сейчас ci-kit поставляет один Copier-шаблон (`template/`, один `.copier-answers.yml`). Языки задаются списком `components` (`python|node|java`), остальное — флагами (`docker`, `codeql`, `allure_report`, `release_please`, `renovate`, `q4j_tests`). `template/.github/workflows/validate.yml.jinja` генерирует jobs целиком и содержит инлайновый Python-контракт, который требует единый `toolkit_version` для `_commit` и всех `uses:`.

Главные боли при обновлении шаблонов:

1. **Конфликты `copier update`.** Шаблон и проект правят одни и те же файлы; при конфликте непонятно, какой вопрос или файл сломался и почему.
2. **Долгий цикл фикса.** Любое исправление CI проходит путь «релиз ci-kit → fleet `copier update` → PR в каждом репозитории» без canary и быстрого отката.

## 2. Цели

- Подключать и обновлять CI по разделам (core, java, python, node, bash, docker, release, …) независимо.
- ci-kit — единственный сборщик validate: потребитель хранит только декларации и тонкий caller.
- `copier update` модуля по построению не может конфликтовать с проектом.
- Фикс CI доезжает до потребителей без Copier — одним bump `@vX`.
- Причина любого падения validate видна из одной таблицы: модуль, версия, категория, код ошибки, runbook.

## 3. Не цели (вне spec #1)

- Миграция существующих потребителей и переписывание fleet automation (`scripts/update_copier_fleet.py`) — spec #2.
- Перенос Renovate (пресеты, модуль `renovate`, self-hosted runner, правила колец) в `renovate-presets` — spec #3.
- Модули python/node/bash/docker/release/codeql/allure/q4j — добавляются после go по образцу `java`.
- Генерация исходников проектов. q4j starter остаётся project-owned.

## 4. Решения

| # | Решение | Причина |
|---|---|---|
| D1 | Один репозиторий ci-kit, один `copier.yml` в корне, модули — entry points `modules/<name>/template`, выбор через вопрос `module` и шаблонизированный `_subdirectory`. | Copier разрешает старую/новую ревизию по корню VCS (см. `docs/architecture.md`); отдельные репозитории на модуль — избыточный overhead. |
| D2 | Каждый модуль — свой answers-файл `.copier-answers.<module>.yml` с собственным `_commit`. | Независимое обновление и откат модуля. |
| D3 | Строгое владение: файлы, которые генерирует шаблон, проект не редактирует; drift проверяется в CI. Кастомизация — только через answers и project-owned файлы. | Убирает конфликты `copier update` как класс. |
| D4 | Модули генерируют **декларации** (`.ci/modules/<module>.yml`), а не jobs. Сборка validate происходит в runtime в reusable `validate.yml` ci-kit. | Логика CI меняется только в ci-kit; фикс = bump ref. |
| D5 | Reusable validate запускает **фиксированные** jobs по типу модуля с `if:` и matrix; динамического `uses:` по строке нет. | Безопасность и предсказуемость, требование RFC. |
| D6 | Единый `toolkit_version` заменяется проверяемым правилом совместимости `schema_version`. | Модули могут иметь разные `_commit`. |
| D7 | Один стабильный required check: `ci / gate`. | Branch protection не зависит от набора модулей. |

**Замещение решений в `docs/architecture.md`:** D4 замещает решение 2 («Caller composition»): композиция jobs переезжает из потребителя в reusable validate ci-kit, при этом потребитель по-прежнему владеет триггерами caller'а. Решение 1 (атомарные языковые workflows) сохраняется: reusable validate вызывает существующие `java-ci.yml`, `python-ci.yml`, `node-ci.yml` без изменения их API. Решение 3 уточняется: Copier создаёт декларации и caller'ы, а не jobs. После go `docs/architecture.md` обновляется отдельным пунктом.

## 5. Слои и владение

| Слой | Где | Владелец | Канал доставки |
|---|---|---|---|
| Логика проверок: reusable workflows, actions, CLI `ci-kit` | `ci-kit/.github/workflows`, `actions/`, `tools/` | ci-kit | bump `@vX` через Renovate |
| Декларация модуля | `.ci/modules/<module>.yml` | Copier, модуль `<module>` | `copier update -a .copier-answers.<module>.yml` |
| Тонкие caller'ы | `.github/workflows/validate.yml` (модуль `core`), `release.yml` (модуль `release`) | Copier | то же; меняются редко |
| Проектные файлы | README, `build.gradle`, `.github/renovate.json`, release-please config | проект | Copier создаёт один раз (`_skip_if_exists`) |

Правило: у каждого пути ровно один владелец. Модуль объявляет свои пути в `modules/<name>/module.yml`; пересечение путей между модулями — ошибка сборки ci-kit и ошибка `plan` у потребителя.

## 6. Структура ci-kit

```text
copier.yml                      # вопрос module, _subdirectory: modules/{{ module }}/template
modules/
  core/
    module.yml                  # name, schema_version, requires: [], paths
    template/
      {{ _copier_conf.answers_file }}.jinja
      .ci/modules/core.yml.jinja
      .github/workflows/validate.yml.jinja
  java/
    module.yml                  # requires: [core]
    template/
      {{ _copier_conf.answers_file }}.jinja
      .ci/modules/java.yml.jinja
schemas/
  core.v1.json
  java.v1.json
tools/ci_kit/                   # CLI: plan, doctor
docs/errors/CIK-*.md            # runbook на каждый код
.github/workflows/
  validate.yml                  # reusable: plan → jobs → gate
  java-ci.yml python-ci.yml node-ci.yml docker-build.yml ...
```

Вопросы конкретного модуля в `copier.yml` закрыты `when: "{{ module == 'java' }}"`. Существующий `template/` остаётся до миграции (spec #2).

## 7. Пример потребителя (core + java)

```text
.copier-answers.core.yml
.copier-answers.java.yml
.ci/modules/core.yml
.ci/modules/java.yml
.github/workflows/validate.yml
```

`.ci/modules/java.yml`:

```yaml
schema_version: 1
components:
  - id: backend-java
    name: Backend Java
    path: backend
    java_version: "21"
```

`.github/workflows/validate.yml`:

```yaml
name: Validate
on:
  pull_request:
  push:
    branches: [main]
  workflow_dispatch:
permissions:
  contents: read
jobs:
  ci:
    uses: quokkify/ci-kit/.github/workflows/validate.yml@v3.0.0
```

Подключение и обновление:

```bash
copier copy  --trust -a .copier-answers.java.yml -d module=java gh:quokkify/ci-kit .
copier update --trust -a .copier-answers.java.yml
```

## 8. Reusable validate

### 8.1 `plan`

Checkout потребителя и ci-kit в `.toolkit` на `job.workflow_sha` (как в `java-ci.yml`), затем `ci-kit plan`:

1. Находит `.copier-answers.*.yml` и `.ci/modules/*.yml`; каждой декларации должен соответствовать answers-файл и наоборот.
2. Валидирует декларацию по `schemas/<module>.v<schema_version>.json`.
3. Проверяет совместимость: `schema_version` поддерживается ref'ом ci-kit, на который указывает caller.
4. Проверяет `requires` (например, `java` требует `core`).
5. **Drift-check:** рендерит каждый модуль на его `_commit` с его answers во временный каталог и сравнивает с template-owned путями в репозитории.
6. Проверяет, что у каждого пути один владелец.
7. Outputs: `modules` (JSON-список включённых) и matrix на каждый тип (`java`, `python`, …; `[]` если выключен).

### 8.2 Jobs

- `core-checks`: actionlint, whitespace по diff, JSON/YAML parse. Код переезжает из инлайнового Python в `ci-kit` с тестами.
- По одному job на тип модуля: `if: needs.plan.outputs.java != '[]'`, `strategy.matrix.component: ${{ fromJSON(needs.plan.outputs.java) }}`, `uses: ./.github/workflows/java-ci.yml`. API существующих reusable workflows не меняется.

### 8.3 `gate`

`if: always()`, `needs` — все jobs.

- `failure` или `cancelled` у любой job → красный.
- `skipped` допустим только для модуля, который `plan` пометил выключенным. Skip включённого модуля → `CIK-CORE-020`.
- Пишет summary (раздел 9.3).

## 9. Диагностика

### 9.1 Категории

Каждый шаг reusable workflows размечен категорией; gate выводит категорию первого упавшего шага.

| Категория | Примеры | Кто чинит |
|---|---|---|
| `template` | drift, несовместимая схема, конфликт владельцев, нарушен `requires` | `copier update` модуля или откат ручной правки |
| `toolkit` | падение setup/action ci-kit, baseline lint ci-kit | релиз ci-kit |
| `project` | компиляция, тесты, линтеры проекта | проект |

### 9.2 Коды ошибок

Формат `CIK-<MODULE>-<NNN>`, вывод через `::error title=CIK-…::<сообщение>`. Для каждого кода есть `docs/errors/CIK-<MODULE>-<NNN>.md`: причина, точная команда исправления, пример. Тест ci-kit падает, если код используется без runbook.

Начальный набор:

| Код | Условие |
|---|---|
| `CIK-CORE-001` | неизвестная или неподдерживаемая `schema_version` |
| `CIK-CORE-002` | декларация без answers-файла или наоборот |
| `CIK-CORE-003` | нарушен `requires` |
| `CIK-CORE-010` | drift в template-owned файле |
| `CIK-CORE-011` | два модуля владеют одним путём |
| `CIK-CORE-020` | включённый модуль пропущен (skipped) |

### 9.3 Gate summary

```text
| module | template @ | ci-kit @ | result | category | failing step     | code                  |
| core   | v3.0.0     | v3.0.2   | ok     |          |                  |                       |
| java   | v3.0.0     | v3.0.2   | fail   | template | drift: java.yml  | CIK-CORE-010 (runbook) |
```

### 9.4 `ci-kit doctor`

```bash
uvx --from git+https://github.com/quokkify/ci-kit@v3.0.2 ci-kit doctor
```

Тот же код, что `plan`: схема, совместимость, `requires`, drift, владельцы. Выводит те же коды.

### 9.5 Rollout и откат

- **Ring 0:** fixtures и сценарии ci-kit (`tests/scenarios`) на каждый PR в ci-kit.
- **Ring 1 (canary):** `renovate-presets`, `java-test-automation-template`. Bump `@vX` для patch/minor сразу, automerge на зелёном `ci / gate`.
- **Ring 2:** остальные потребители, тот же bump с `minimumReleaseAge`.
- **Откат = forward fix:** revert в ci-kit → patch-релиз → ring 1 за минуты; ring 2 не получает сломанную версию из-за задержки. Аварийный ручной откат — одна строка `@v` в caller'е.

Spec #1 фиксирует только контракт колец (immutable теги ci-kit и список canary). Конкретные Renovate-правила — spec #3.

## 10. Тестирование

- **Unit:** `ci-kit plan` и `doctor` — каждый код `CIK-*` покрыт негативным тестом; классификация gate.
- **Lifecycle шаблонов** (локально, без GitHub) для `core+java` и `core+java+docker` после добавления docker:
  - copy;
  - update одного модуля → diff только в его путях;
  - совместный update;
  - повторный update → пустой diff;
  - ручная правка → `CIK-CORE-010`, а не конфликт Copier;
  - откат `_commit`;
  - удаление модуля по документированной процедуре.
- **Runtime:** `validate-toolkit.yml` прогоняет сгенерированные fixtures через reusable validate. С учётом решения 8 в `docs/architecture.md` (reusable workflow из PR нельзя вызвать удалённо) удалённая runtime-проверка выполняется на опубликованном тестовом ref; локальная генерация не считается доказательством.

## 11. Прототип: `core` + `java`

1. `copier.yml` с вопросом `module` и шаблонизированным `_subdirectory`; модули `core` и `java`; схемы `core.v1`, `java.v1`.
2. Reusable `validate.yml` (plan → core-checks → java → gate), `ci-kit plan/doctor`, коды `CIK-CORE-001/002/003/010/011/020` с runbook.
3. Проверка в тестовом GitHub-репозитории.
4. Перевод одного canary (`java-test-automation-template`).

### Технические вопросы, которые прототип обязан закрыть

| Вопрос | Ожидание | Если не подтвердится |
|---|---|---|
| Шаблонизированный `_subdirectory` + `-a <file>` при `copier update` корректно разрешают старый `_commit` | да, по документации Copier | отдельный `copier.yml` на модуль невозможен в одном репо → пересмотр D1 |
| `./.github/workflows/...` во вложенном reusable workflow резолвится в ci-kit на том же sha | да | ссылки `quokkify/ci-kit/...@<sha>`, проставляемые при релизе |
| matrix + reusable `uses` + `if` дают читаемые имена checks | `ci / java (Backend Java) / …` | явные jobs на компонент через генерацию |
| Лимит вложенности reusable workflows не превышен | caller → validate → java-ci | уплощение |
| Drift-рендер укладывается примерно в 15 секунд | да | кэш копий шаблона или хеш-манифест |

## 12. Критерии go / no-go

Go, если выполнено всё:

1. Update одного модуля меняет только пути этого модуля.
2. Update при ручной правке шаблонного файла даёт `CIK-CORE-010` с diff, а не конфликт Copier.
3. Фикс в ci-kit доезжает до canary без Copier, одним bump PR.
4. По gate summary причина понятна без открытия логов на трёх искусственных поломках: `template`, `toolkit`, `project`.

No-go → fallback (подход C из RFC): один шаблон со строгим владением, drift-check и диагностикой из раздела 9, без модульного разделения.

## 13. Дальнейшие спецификации

- **Spec #2 — миграция и fleet.** Переход с `template/` на модули без слепого `copier copy`; сохранение job IDs, required checks и release policy; fleet обнаруживает модули по answers-файлам, обновляет модуль или согласованный набор одним PR, показывает old → new `_commit` и вывод `doctor`; поддержка legacy-потребителей.
- **Spec #3 — Renovate в `renovate-presets`.** Пресеты; Copier-модуль `renovate` (генерирует `.github/renovate.json` по подключённым модулям); self-hosted runner как reusable workflow; regex-менеджер для `quokkify/ci-kit` вместо `quokkify/project-toolkit`; правила колец из раздела 9.5. ci-kit перестаёт знать о Renovate, цикл зависимостей ci-kit ↔ renovate-presets разрывается.
