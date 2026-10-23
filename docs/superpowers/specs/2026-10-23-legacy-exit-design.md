# Выход из legacy-шаблона — дизайн (spec #6)

- Статус: draft, на ревью
- Issues: #490 (security), #491 (release, allure, copier-update), #492 (удаление legacy); RFC #393 (закрыт)
- Опирается на: spec #2 (`2026-10-12-modular-ci-migration-design.md`), spec #4 (`2026-10-21-modular-ci-language-modules-design.md`), spec #5 (`2026-10-22-remove-legacy-validate-design.md`), `docs/modules.md`
- Дата: 2026-10-23

## 1. Контекст

После v3.9.0 все 12 потребителей в `hybrid(validate)`: `validate.yml` рендерит `core`, языки — модули `java`/`python`/`node`/`docker`. Остальной CI по-прежнему рендерит legacy-шаблон (`template/`, `module: legacy`, `.copier-answers.yml`):

| Файл | Legacy-вопросы |
|---|---|
| `.github/workflows/codeql.yml` | `codeql`, `codeql_languages`, `components`, `toolkit_version` |
| `.github/workflows/gitleaks.yml` | `gitleaks`, `toolkit_version` |
| `.github/workflows/release.yml`, `.github/release-please/{config,manifest}.json`, `.github/scripts/enrich_release_notes.py` | `release_please`, `release_mode`, `toolkit_version` |
| `.github/workflows/allure-report.yml`, `.github/allure/{allurerc.mjs,safe_extract.py}` | `allure_report`, `allure_source`, `allure_external_*`, `allure_categories_file`, `allure_publish_pages`, `allure_pages_url`, `project_name`, `toolkit_version` |
| `.github/renovate.json` | `renovate`, `renovate_config_repository`, `renovate_presets`, `docker` |
| `.github/workflows/copier-update.yml`, `docs/ci-kit.md`, `README.md`, `.github/pull_request_template.md` | `toolkit_version`, `project_name` и флаги фич (docs) |

От `.copier-answers.yml` и `template/` зависят и инструменты:

- `scripts/update_copier_fleet.py`: legacy update, audit/inventory, primary answers, проверка release helper;
- `scripts/verify_ci_gate.py` (`verify_pilot`, `template_contexts`) и `scripts/fleet_health.py`: читают legacy answers;
- `copier-update.yml`: legacy update, затем `ci-kit update --skip-legacy`, release helper из `template/`;
- `.github/workflows/allure-publisher-core.yml`, `allure-pages.yml`: запускают `.toolkit/template/.github/allure/safe_extract.py.jinja`;
- `scripts/render_codeql_workflows.py`, `scripts/validate.py`, `.github/renovate.json` (regex managers на `template/`).

Ни у одного из перечисленных файлов нет модуля-владельца.

## 2. Цель

1. В каждом потребителе только answers-файлы модулей: `.copier-answers.{core,security,release,allure,renovate,<языки>}.yml`. Нет `.copier-answers.yml` и `module: legacy`.
2. У каждого файла CI есть модуль-владелец; раскатка, `copier-update`, health и pilot gate работают только с модулями.
3. Переход без ручных правок у потребителей, required checks не переименовываются, откат до удаления legacy — revert одного PR.
4. Итог — v4.0.0; `template/` и весь переходный код удалены.

## 3. Не цели

- Автозагрузка `allure-results-<id>` языковыми модулями (#491, остаётся открытым).
- Переделка Renovate на `renovate-presets` и self-hosted runner (#489).
- Решения #493: независимые релизы модулей; единый `toolkit_version` сохраняется.
- Дробление security на отдельные модули: CodeQL и gitleaks — флаги внутри `security`.

## 4. Модули

| Модуль | Файлы | Вопросы |
|---|---|---|
| `core` (обязателен) | `validate.yml`, `copier-update.yml`, `docs/ci-kit.md`; стартовые `README.md`, `.github/pull_request_template.md` | `project_name`, `toolkit_version` |
| `security` | `codeql.yml` (если `codeql`), `gitleaks.yml` (если `gitleaks`) | `codeql` (bool, default true), `gitleaks` (bool, default true), `codeql_languages` (override, default `[]`), `toolkit_version` |
| `release` | `release.yml`, `.github/scripts/enrich_release_notes.py`; стартовые `.github/release-please/{config,manifest}.json` | `release_mode` (`single`/`manifest`), `toolkit_version` |
| `allure` | `allure-report.yml`, `.github/allure/allurerc.mjs`, `.github/allure/safe_extract.py` | `allure_source` (`component`/`external`), `allure_external_workflow_name`, `allure_external_workflow_path`, `allure_external_artifact_prefix`, `allure_external_artifact_min_count`, `allure_external_artifact_max_count`, `allure_categories_file`, `allure_publish_pages`, `allure_pages_url`, `project_name`, `toolkit_version` |
| `renovate` | стартовый `.github/renovate.json` | `renovate_config_repository`, `renovate_presets` |
| `java`, `python`, `node`, `docker` | без изменений | без изменений |

«Стартовый» — `_skip_if_exists`: рендерится при `copy`, потом принадлежит проекту (как сейчас в legacy).

Решения:

| # | Решение | Почему |
|---|---|---|
| D1 | Включение фичи = установка модуля; флаги `allure_report`, `release_please`, `renovate` исчезают. В `security` остаются флаги `codeql` и `gitleaks`. | Модульная схема RFC; CodeQL и gitleaks включают раздельно. |
| D2 | `security`, `release`, `allure` требуют `core` (`MODULES[...].requires`); `renovate` — тоже. | `toolkit_version` и `copier-update.yml` живут в `core`. |
| D3 | `codeql.yml` — тонкий вызов нового reusable `.github/workflows/codeql.yml` ci-kit (`workflow_call`). Он запускает `ci-kit plan`, анализирует `actions` + языки установленных модулей (`java` → `java-kotlin`, `python` → `python`, `node` → `javascript-typescript`); непустой `codeql_languages` заменяет вывод. Имена jobs `Analyze (<lang>)` и check context `CodeQL` сохраняются. | Добавление языкового модуля включает анализ без перерендера; rulesets не меняются. |
| D4 | `allure_source` без `auto`; в режиме `component` источник — `Validate` (`core`). | `auto` зависел от legacy `components`. |
| D5 | `renovate_presets` — явный список; default для новой копии — `[default, github-actions]`, языковые пресеты (`python`, `javascript`, `java`, `docker`) добавляются явно. | Вывод из `components`/`docker` уходит вместе с legacy; переделка — #489. |
| D6 | `docs/ci-kit.md` (core) — один текст для любого набора модулей: схема модулей, ссылки на `docs/modules.md` и `ci-kit doctor`; без таблицы файлов. | Таблица зависела бы от других модулей и давала бы drift при добавлении модуля. |
| D7 | `safe_extract.py.jinja` и `enrich_release_notes.py.jinja` переезжают в `modules/allure/template` и `modules/release/template`; reusable Allure workflows, `copier-update.yml` и `update_copier_fleet.verify_release_helper` читают новые пути. | `template/` удаляется в v4. |
| D8 | `toolkit_version` и `_commit` совпадают во всех answers-файлах (как сейчас; проверка переезжает из `CIK-LEGACY-002` в `CIK-CORE-012`). | #493 не решён. |

## 5. v3.10.0 (minor): модули рядом с legacy

### 5.1 Области hand-off

`tools/ci_kit/handoff.py` `AREAS` и допустимые значения `handed_off` в `copier.yml`:

| Область | Модуль | Legacy-файлы |
|---|---|---|
| `validate` | `core` | `.github/workflows/validate.yml` |
| `base` | `core` | `.github/workflows/copier-update.yml`, `docs/ci-kit.md`, `README.md`, `.github/pull_request_template.md` |
| `security` | `security` | `.github/workflows/codeql.yml`, `.github/workflows/gitleaks.yml` |
| `release` | `release` | `.github/workflows/release.yml`, `.github/release-please/config.json`, `.github/release-please/manifest.json`, `.github/scripts/enrich_release_notes.py` |
| `allure` | `allure` | `.github/workflows/allure-report.yml`, `.github/allure/allurerc.mjs`, `.github/allure/safe_extract.py` |
| `renovate` | `renovate` | `.github/renovate.json` |

Legacy исключает файлы переданных областей через `_exclude` (как `validate`). `CIK-HANDOFF-001/002/003` работают для всех областей без изменений кода проверок. Модули рендерят файл legacy-области, только если область передана; иначе `CIK-CORE-011` (два владельца).

### 5.2 `ci-kit migrate-legacy`

Временная подкоманда (только v3.10.x): `ci-kit migrate-legacy --repo <path> --to <ref> [--json <file>]`.

1. Предусловия: чистое дерево (`CIK-MIGRATE-001`); legacy `_commit == --to` (`CIK-MIGRATE-002`); ни одна из областей `base`/`security`/`release`/`allure`/`renovate` не передана и их модули не стоят (`CIK-MIGRATE-004`); файлы этих областей совпадают с legacy-рендером на `--to` (`CIK-MIGRATE-003`).
2. Перевод ответов:

   | Legacy | Модули |
   |---|---|
   | `project_name`, `toolkit_version` | `core` (уже стоит) |
   | `codeql`, `gitleaks`, `codeql_languages` | `security`, если `codeql` или `gitleaks`; иначе модуль не ставится |
   | `release_please: true`, `release_mode` | `release`; при `false` не ставится |
   | `allure_report: true`, `allure_source` (`auto` → фактический `allure_mode`), `allure_*` | `allure`; при `false` не ставится |
   | `renovate: true`, `renovate_config_repository`, `renovate_presets` (выведенный default → явный список) | `renovate`; при `false` не ставится |
   | `components`, `docker` | не переносятся (уже в языковых модулях) |

3. Коммиты: `copier update` legacy с `handed_off` = все области (одним коммитом), затем `copier copy` каждого нового модуля (коммит на модуль).
4. Эквивалентность (`CIK-MIGRATE-005`): каждый файл, который до миграции рендерил legacy, после неё побайтно тот же, кроме:
   - `codeql.yml`: множество языков CodeQL до (вывод из `components`/`codeql_languages`) равно множеству после (`ci-kit plan` + override);
   - `docs/ci-kit.md`: новый текст (D6);
   - стартовые файлы (`_skip_if_exists`): не меняются вообще.
   И `build_plan` без находок.
5. Любой отказ после первого коммита — откат к исходному HEAD (как `_rollback` в #482), включая ignored-файлы, созданные миграцией.
6. Результат — маркер PR (`migration_marker.py`) с модулями, коммитами и `checks_before`/`checks_after` (ожидаемо равны: required checks не меняются).

`update_copier_fleet.py --migrate-legacy` открывает PR на ветке `ci-kit/migrate-legacy`; `rollout_copier_fleet.py --migrate-legacy` проводит pilot-кольцо.

### 5.3 Инструменты без legacy

В v3.10.0 каждый инструмент работает и на репозитории без `.copier-answers.yml`:

| Инструмент | Изменение |
|---|---|
| `copier-update.yml` (core) | `ci-kit update --repo . --to <ref>` по всем answers-файлам; legacy-шаг только если файл есть; release helper — из `modules/release/template` |
| `ci-kit update` | порядок `legacy` (если есть), `core`, остальные модули; `--trust` для всех (модули теперь рендерят workflows) |
| `fleet_health.py` | `_commit` из `.copier-answers.core.yml`, если legacy нет |
| `verify_ci_gate.py` | `verify_pilot`: core answers без legacy; `template_contexts`: проверки модулей (`ci / gate`, `CodeQL`, `gitleaks` по флагам security) |
| `update_copier_fleet.py` | audit/inventory из ответов модулей; primary answers = core; legacy update только при наличии файла |

### 5.4 Раскатка

1. Dry-run `--migrate-legacy` по всем 12: ни одного отказа или подготовительные PR потребителям.
2. Pilot-кольцо: gh-pages-subdir-action, skills, q4j.
3. Остальные 8, knowledge вручную.
4. Пауза: следующий патч-релиз (v3.10.1 или v4.0.0-rc) проходит fleet `update` по всем 12 без ручных правок.

Откат: revert PR миграции возвращает области legacy.

## 6. v4.0.0 (major): удаление legacy

Удаляется: `template/`, выбор `legacy` в `module`, legacy-вопросы (`components`, `docker`, `handed_off`, `allure_report`, `release_please`, `renovate`, `codeql` в legacy-форме и т. д.), `_external_data`, legacy-элементы `_exclude`/`_skip_if_exists`, `tools/ci_kit/legacy.py`, `handoff.py`, `--skip-legacy`, `migrate-legacy`, коды `CIK-LEGACY-*`, `CIK-HANDOFF-*`, `CIK-MIGRATE-*` и их runbooks, legacy-сценарии `tests/scenarios/*.yml`, regex managers на `template/` в `.github/renovate.json`. `render_codeql_workflows.py` рендерит модули. Default `module` = `core`.

Fleet `update` на v4.0.0:

| `.copier-answers.yml` | Действие |
|---|---|
| нет | обычный update модулей |
| есть, все области переданы | удалить файл одним коммитом `chore(ci-kit): drop empty legacy answers` |
| есть, не все области переданы | отказ `CIK-CORE-030` (`legacy answers still render files; migrate with ci-kit v3.10.x`), файлы не меняются |

`CIK-CORE-030` проверяет и `ci-kit plan`. Runbook `docs/errors/CIK-CORE-030.md` описывает миграцию CLI v3.10.x.

## 7. Тесты

- Рендер каждого нового модуля: набор файлов; для всех legacy-сценариев — файлы модуля равны файлам соответствующей legacy-области на тех же ответах (кроме `codeql.yml`, `docs/ci-kit.md`); actionlint.
- Reusable `codeql.yml`: языки из `plan`, override `codeql_languages`; регрессия — имена jobs `Analyze (<lang>)` и workflow `CodeQL` как у legacy для каждого сценария.
- `migrate-legacy`: все legacy-сценарии мигрируют с эквивалентностью; негативный тест на каждый `CIK-MIGRATE-*`; откат с ignored-файлами; lifecycle migrate → `update` на следующий ref → revert.
- Без legacy: `ci-kit update`, `copier-update.yml`, `fleet_health`, `verify_ci_gate`, fleet audit.
- `CIK-CORE-012` (`toolkit_version` расходится между answers-файлами).
- v4: удаление пустого legacy-файла, отказ `CIK-CORE-030` без изменений файлов, `module` по умолчанию `core`.
- Полная проверка: `scripts/run_test_suites.py`, `scripts/validate.py`, `scripts/validate_modules_fixture.py`.

## 8. Issues после волны

- #490, #492 — закрываются.
- #491 — закрывается в части release/allure/copier-update; автозагрузка Allure переносится в новый issue.
- #489, #493 — остаются.
