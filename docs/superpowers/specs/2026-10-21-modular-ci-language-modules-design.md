# Языковые модули и миграция компонентов — дизайн (spec #4, волна 2)

- Статус: draft, на ревью (ревизия 2 после critic-ревью)
- RFC: [#393](https://github.com/quokkify/ci-kit/issues/393)
- Опирается на: spec #1 (`2026-10-12-modular-ci-templates-design.md`), spec #2 (`2026-10-12-modular-ci-migration-design.md`), `docs/modules.md`
- Дата: 2026-10-21

## 1. Контекст

Волна 1 перевела 7 потребителей в `hybrid(validate)` (модуль `core`, required check `ci / gate`); итоги §11 spec #2 — в #393. На legacy validate остались 4 репозитория.

| Репозиторий | legacy `components` / флаги | Ручные правки `validate.yml` | Required checks (validate) |
|---|---|---|---|
| skills | python | `test-command` у `app-python` | `App Python / ci` |
| knowledge | `[]` после knowledge#49 | нет; только `template-contract` | rulesets недоступны (приватный, 403) |
| marketdesk | node, `docker: true` | node: `node-version: 24`, свои lint/test/build; ручной job `java-test-automation` (`java-ci`, Java 21, `test-automation/`, `run-build: false`); ручной агрегатор `complete`; шаг `Verify component changelog history and enrichment` в `template-contract` | `Validation pipeline complete` |
| q4j | java, node, python | все три компонента — заглушки (`true`, `gradlew help`, `run-lint/run-build: false`); шаги `Validate release and publication contracts`, `Test Gradle retry recovery contract` и правка inline-скрипта в `template-contract` | нет: `Tests pipeline complete` из `test.yml`, CodeQL, gitleaks |

Потребители правят `with:` legacy-jobs и сам `template-contract`, поэтому поведение CI определяется фактическим `validate.yml`, а не answers.

В ci-kit есть модули `core` и `java`; `java` не использует ни один потребитель. Reusable `.github/workflows/validate.yml` знает только job `java`, `gate.needs` захардкожен, `tools/ci_kit/cli.py` отдаёт только матрицу `java`. `migrate` копирует ровно один модуль, а `CIK-MIGRATE-003` требует побайтного совпадения `validate.yml` с legacy-рендером.

Область `validate` — один файл, поэтому репозиторий с компонентами мигрирует атомарно: `core` и все его языковые модули в одном PR.

## 2. Цель

Ни один потребитель не использует legacy validate. Для этого:

- модули `python`, `node`, `docker` по образцу `java`;
- `migrate`, который выводит набор модулей из фактических jobs legacy `validate.yml` и ставит его одним PR;
- подготовительные PR у потребителей там, где legacy содержит то, чего модули не воспроизводят;
- раскатка подволнами 2a (skills, knowledge), 2b (marketdesk), 2c (q4j).

## 3. Не цели

- Удаление legacy validate из `template/` — отдельный follow-up.
- Точка расширения `core` для проектных шагов: проектные проверки живут в project-owned workflows (§9).
- Модули `security`, `release`, `allure`, `copier-update`; spec #3 (Renovate).
- Публикация docker-образов: модуль `docker` воспроизводит legacy (`push: false`, без secrets).
- Изменение API `java-ci.yml`, `python-ci.yml`, `node-ci.yml`, `docker-build.yml`.
- Удаление legacy-ответов `components`/`docker` из `.copier-answers.yml` после миграции: их продолжает читать legacy CodeQL; дублирование принимается до волны `security`.
- #450 — своим путём.

## 4. Решения

| # | Решение | Причина |
|---|---|---|
| L1 | Отдельный модуль на язык (`python`, `node`) и `docker`, по образцу `java`. | Spec #1: независимое обновление языков — цель RFC. |
| L2 | `migrate` выводит набор модулей и данные компонентов из фактических jobs legacy `validate.yml`; оператор по-прежнему запускает `--migrate core`. | Ручные правки — реальное поведение CI, answers его не содержат; fleet почти не меняется. |
| L3 | Один migrate-PR, commit на модуль, порядок `core → java → python → node → docker`. | Атомарная передача `validate.yml`; читаемая история. |
| L4 | Декларации языковых модулей зеркалят входы вызываемого workflow в snake_case; все поля, кроме `id`, `name`, `path`, необязательны. | Переносит ручные правки без собственной семантики полей; API workflows не меняется. |
| L5 | `ci-kit plan` заполняет каждую запись матрицы полностью: умолчания читаются из `inputs.*.default` вызываемого workflow в `.toolkit`, поверх — поля декларации; типы — JSON boolean/string. `with:` передаёт `matrix.component.<field>` без выражений с `\|\|`. | `false \|\| true` = `true`, `'' \|\| x` = `x`: выражения с умолчаниями молча меняют поведение. Один источник умолчаний — сами workflows. |
| L6 | `CIK-MIGRATE-005` — job или ключ, который нельзя сопоставить (§7.1). | Модуль не может воспроизвести собственные steps, `if`, `needs` и т.д.; молча терять их нельзя. |
| L7 | Для `validate.yml` `CIK-MIGRATE-003` сужается: с legacy-рендером сравниваются всё, кроме component-jobs (`template-contract`, триггеры, `permissions`, `concurrency`); component-jobs проверяет эквивалентность (§7.2). Прочие файлы области — побайтно. | Ручные `with:` переносятся; всё, что модули потеряли бы, по-прежнему защищено. |
| L8 | Ручные проектные шаги в `template-contract` и ручные jobs без `uses:` выносятся подготовительным PR в project-owned workflow с собственным required check. | `core` не имеет точки расширения; так проектные проверки не теряются и защита не ослабевает. |
| L9 | Runtime-доказательство — CI самого migrate-PR у потребителя (первый — skills). Отдельного sandbox нет. | Новый reusable validate исполняется до merge; красный PR не мёржится. Поведенческие расхождения ловит §7.2, а не CI. |
| L10 | Необязательные поля добавляются в схему `java.v1` без смены версии. | Уже валидные декларации остаются валидными. |

## 5. Модули

Каждый модуль: шаблон `modules/<name>/template`, декларация `.ci/modules/<name>.yml`, схема `tools/ci_kit/schemas/<name>.v1.json`, запись в `MODULES` (`tools/ci_kit/modules.py`), answers-файл `.copier-answers.<name>.yml`.

| Модуль | Декларация `components[]` | `requires` | Job в gate | Copier-вопрос | Внутренний job |
|---|---|---|---|---|---|
| `python` | `id`, `name`, `path`, `python_version?` + общие | `core` | `python` | `python_components` | `ci` |
| `node` | `id`, `name`, `path`, `node_version?` + общие | `core` | `node` | `node_components` | `ci` |
| `java` (есть) | `id`, `name`, `path`, `java_version`, `build_tool?`, `distribution?` + общие | `core` | `java` | `java_components` | `ci` |
| `docker` | `id`, `name?`, `context?`, `dockerfile?`, `target?` | `core` | `docker` | `docker_images` | `build` |

Общие поля (L4), каждое соответствует одноимённому входу в kebab-case: `package_manager`, `install_command`, `lint_command`, `test_command`, `build_command`, `run_lint`, `run_tests`, `run_build`, `upload_test_artifacts`, `test_artifact_path`, `test_artifact_name`.

- Версии (`*_version`) — строки; схема отклоняет числа.
- Новые проекты получают только `id`, `name`, `path` и версию.
- `id` уникальны во всех декларациях проекта; конфликт — ошибка `ci-kit plan`. Имена jobs reusable validate (`plan`, `core-checks`, `gate`, `java`, `python`, `node`, `docker`) не резервируют `id` компонентов: компонент — элемент матрицы, а не job.

## 6. Reusable validate

- `ci-kit plan` (`cli.py`) отдаёт матрицы `java`, `python`, `node`, `docker`; выключенный модуль — `[]`. Каждая запись заполнена полностью (L5).
- Jobs `python`, `node`, `docker`: `if: needs.plan.outputs.<type> != '[]'`, matrix по записям, фиксированный `uses: ./.github/workflows/<type>-ci.yml` (`docker-build.yml`), без динамического `uses` (D5).
- `with:` — `working-directory: ${{ matrix.component.path }}` и `<input>: ${{ matrix.component.<field> }}` для каждого входа workflow; docker — `context`, `dockerfile`, `target`, `push: false`.
- Имя job — `<type> (<name>)`, как у существующего `java`; `gate.py` `_matches` продолжает находить failing step. Контексты checks — `ci / <type> (<name>) / <внутренний job>`.
- `gate.needs` += `python`, `node`, `docker`. Skipped (модуль выключен) допустим; failure и cancelled проваливают gate.
- Имя caller'а `Validate` и путь `.github/workflows/validate.yml` не меняются (spec #2 §7.4); артефакты вложенных reusable workflows принадлежат run caller'а, Allure external через `workflow_run` их видит.

## 7. `ci-kit migrate`

### 7.1 Сопоставление

`legacy_to_modules(validate_yml, toolkit_version) -> list[(module, data)]` в `tools/ci_kit/migrate.py` — чистая функция над разобранным legacy `validate.yml`:

- `template-contract` пропускается (его заменяет `core`; защищён L7);
- job с ключами только из `{name, uses, with}` и `uses: quokkify/ci-kit/.github/workflows/<type>-ci.yml@<toolkit_version>` → компонент `<type>_components`: `id` = id job, `name` = `name` job (или id), `path` = `working-directory`, каждый вход `with:` → одноимённое поле L4 (`<type>-version` → `<type>_version`, приведённое к строке);
- java без `java-version` → `java_version: "17"`;
- job `uses: …/docker-build.yml@<toolkit_version>` → `docker_images` (`id`, `name`, `context`, `dockerfile`, `target`); прочие входы, кроме `push: false`, → отказ;
- `CIK-MIGRATE-005` с именем job/ключа/входа, если:
  - у job есть ключи вне `{name, uses, with}` (`if`, `needs`, `secrets`, `permissions`, `strategy`, `concurrency`, `steps`, `runs-on`, …);
  - `uses:` ведёт не на известный workflow ci-kit или ref ≠ `toolkit_version`;
  - вход `with:` не входит в поля модуля;
  - версия — число с плавающей точкой (YAML исказил бы `3.10`);
- результат всегда начинается с `core`; языковые модули — только непустые.

Ручные `uses:`-jobs известных типов (marketdesk `java-test-automation`) переносятся как компоненты; answers для сопоставления не нужны.

### 7.2 Шаги (один PR, commit на шаг)

1. `copier update` legacy с `handed_off += [validate]`, удаление `validate.yml`.
2. `copier copy` модуля `core`.
3. `copier copy` каждого модуля из сопоставления в порядке L3, данные через `-d <question>=<json>`.
4. Эквивалентность: для каждого legacy component-job `effective(legacy) == effective(plan)`, где `effective(x)` = `inputs.*.default` вызываемого workflow, перекрытые входами `x`; `legacy` — его `with:`, `plan` — запись матрицы, которую выдаёт `ci-kit plan` на результате. Набор jobs совпадает один к одному. Расхождение — отказ до push.

### 7.3 Отказы

- `CIK-MIGRATE-005` — §7.1. Исправление — подготовительный PR (L8, §9).
- `CIK-MIGRATE-003` — L7: расхождение с legacy-рендером вне component-jobs или в прочих файлах области.
- Остальные предусловия spec #2 §7.1 без изменений.

### 7.4 Required checks (`scripts/migration_checks.py`)

- `checks_after` = `["ci / gate"]`.
- `checks_before` выводится из `validate.yml` на base SHA (как сейчас проверяет marker, `migration_checks.py:281`): `Shared template contract` плюс точные контексты `<name job или id> / <внутренний job>` для каждого сопоставленного job (`ci` для языков, `build` для docker). Префиксы и чтение ruleset в `migrate` не используются.
- Контексты вне `checks_before` не трогаются (#446).

## 8. Fleet

- `scripts/update_copier_fleet.py`: удалить фильтр `language components` и правило «одиночный `CIK-MIGRATE-005` → `not-applicable`»; 005 и 003 — отказы с кодом в отчёте.
- Тело migrate-PR: устанавливаемые модули и таблица legacy job → module/component.
- Режим `update` для гибридных репозиториев с несколькими модулями уже поддержан (`ci-kit update`, порядок по `language`); проверяется тестом.

## 9. Порядок работ и раскатка

**ci-kit:**

1. PR 1 «модули»: §5, §6, сценарии `tests/scenarios/modules/`. Релиз. Legacy-потребителей не затрагивает.
2. PR 2 «миграция»: §7, §8, `docs/modules.md`, `docs/errors/CIK-MIGRATE-003.md`, `docs/errors/CIK-MIGRATE-005.md`. Релиз.

**Потребители** — каждый внешний шаг только с подтверждения владельца. На каждый migrate-PR: dry-run → зелёный `ci / gate` → `migration_checks.py --dry-run` → без `--dry-run` → merge.

| Подволна | Репозиторий | Подготовительный PR (L8) | Migrate |
|---|---|---|---|
| 2a | knowledge | — | если dry-run подтверждает отсутствие компонентов — обычная миграция волны 1, можно до PR 1 |
| 2a | skills | — | python, ручной `test-command`, Allure (component) и Pages. Первое runtime-исполнение новых jobs |
| 2b | marketdesk | шаг changelog → свой workflow со своим required check; удалить `complete`; required checks → `Shared template contract`, `MarketDesk / ci`, `Java test automation / ci`, `docker / build` | node с ручными командами, `java-test-automation` → `java`, docker, Allure external |
| 2c | q4j | шаги release/publication и Gradle retry → свой workflow со своим required check; правку inline-скрипта — вернуть к рендеру или вынести туда же | первый реальный `java`, три заглушки. Альтернатива: удалить заглушки из answers и мигрировать как волна 1 — решение владельца перед 2c |

Подготовительный PR legacy `copier update` не затрагивает: удалённые ручные правки исчезают из diff с рендером, а project-owned workflow Copier не владеет.

Между подволнами: следующий релиз ci-kit проходит fleet `update` по мигрированным без ручных правок.

## 10. Тестирование

- Unit `legacy_to_modules` на фикстурах legacy `validate.yml` формата skills / marketdesk / q4j: ручные команды, ручной `uses:`-job, docker с id `docker`, `run-build: false`, явный пустой `build-command`, нестандартное `test-artifact-name`, int-версия `24`, float-версия → 005, ключи `if`/`needs`/`secrets` → 005, чужой ref → 005, неизвестный вход → 005, job без `uses:` → 005.
- `ci-kit plan`: полное заполнение записи из `inputs.*.default` фикстурного workflow; `false` и `""` из декларации не заменяются умолчанием; валидные/невалидные декларации новых модулей; конфликт `id`; `requires`.
- Эквивалентность §7.2: тест, который падает, если запись матрицы превращает `run-build: false` в `true`.
- `tests/test_modular_validate_workflow.py`: jobs `python`/`node`/`docker`, `if`, `gate.needs`, `with:` без `||`, имена `<type> (<name>)`.
- `gate.py`: `_matches` находит failing step для `ci / python (App Python) / ci`.
- Render: `tests/scenarios/modules/` — `core-python`, `core-node-docker`, `core-polyglot`; `core-java` не меняется.
- Lifecycle (`tests/test_migration_lifecycle.py`): legacy-проект с компонентами и ручными `with:` → `migrate` → эквивалентность → повторное применение без diff → откат по `docs/modules.md`; имена Allure-артефактов начинаются с `allure_external_artifact_prefix`.
- Отказы: ручной шаг в `template-contract` → 003; ручная правка `with:` → переносится.
- `migration_checks.py`: точные `checks_before` для компонентов, ручного `uses:`-job и docker; чужие контексты сохраняются.
- Fleet: репозиторий с компонентами — `would-migrate`; с ручным job — отказ с кодом.

## 11. Критерии готовности волны 2

1. skills, knowledge, marketdesk, q4j не используют legacy validate.
2. Один релиз ci-kit проходит fleet `update` по всем 11 потребителям без ручных правок.
3. Ручное вмешательство — только подготовительные PR из §9 и отказы `migrate` с кодом.
4. Allure-отчёты и Pages skills, marketdesk, q4j публикуются после миграции так же, как до неё; проектные проверки из подготовительных PR остаются required.

Итоги фиксируются комментарием в #393.
