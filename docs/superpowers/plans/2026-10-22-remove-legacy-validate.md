# Удаление legacy validate — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Убрать legacy validate из `template/`, вывести код миграции validate и защитить немигрировавшие репозитории отказом `CIK-LEGACY-006`.

**Architecture:** Три задачи в порядке зависимостей: (1) вынести то, что нужно `migration_checks.py` и `verify_ci_gate.py`, в `tools/ci_kit/migration_marker.py` — чистый рефакторинг; (2) удалить `migrate`, `equivalence`, `legacy_jobs` и `--migrate` в скриптах раскатки; (3) удалить шаблон legacy validate, добавить guard в `copier.yml` и находку в `ci-kit plan`, поправить тесты, скрипты и docs.

**Tech Stack:** Python 3 (`unittest`), Copier 9.19.1, Jinja, PyYAML.

**Spec:** `docs/superpowers/specs/2026-10-22-remove-legacy-validate-design.md`

## Global Constraints

- Пины для локальных прогонов: venv с `PyYAML==6.0.3 jsonschema==4.26.0 copier==9.19.1` первым в `PATH`; `pip install -e .` для `ci_kit`.
- Тесты запускать с `GIT_CONFIG_GLOBAL=/dev/null` (глобальный pre-commit hook ломает коммиты фикстур).
- Полная проверка: `GIT_CONFIG_GLOBAL=/dev/null python scripts/run_test_suites.py` и `python scripts/validate.py`.
- Код отказа: `CIK-LEGACY-006`, описание в `codes.py`: `Legacy template no longer renders validate; validate is not handed off to core`.
- Версия: minor (spec §5). Никаких футеров `BREAKING CHANGE:`; описание «Breaking для не-флита» — в теле коммита задачи 3 и в описании PR.
- Репозиторий публичный: без внутренних URL, токенов и путей машины.
- Коммиты — Conventional Commits, с `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. Старый репозиторий, в чьих `.copier-answers.yml` нет ключа `handed_off` (до v3.4): `copier update` с `--defaults` подставит default вопроса. Если default `[validate]`, guard не сработает и `validate.yml` тихо удалится. Тест — задача 3, шаг 1 (`test_update_without_handed_off_key_refuses`).
2. Guard не должен срабатывать на рендере модулей (`module != legacy`), где вопроса `handed_off` нет. Тест — задача 3 (`test_modules_render_without_guard`).
3. Copier при `update` рендерит старую версию шаблона во временный каталог; guard в старой версии отсутствует, в новой — проверяет только текущие ответы. Флит (`handed_off: [validate]`) должен обновляться без отказа. Тест — задача 3 (`test_update_handed_off_repo_passes`).
4. `migration_checks.py --revert --restore` у уже мигрированных репозиториев работает как раньше. Тесты `tests/test_migration_checks.py` проходят без изменения ассертов — задача 1.
5. `ci-kit plan` на репозитории, у которого `handed_off` испорчен (строка, `oops`), продолжает давать `CIK-HANDOFF-003`, а не `CIK-LEGACY-006`. Тест — задача 3 (`test_malformed_handed_off_keeps_handoff_003`).

---

### Task 1: Вынести маркер миграции и контексты проверок в `migration_marker.py`

Чистый рефакторинг: поведение не меняется.

**Files:**
- Create: `tools/ci_kit/migration_marker.py`
- Modify: `tools/ci_kit/migrate.py` (импортирует из нового модуля, пока не удалён в задаче 2), `scripts/migration_checks.py:35`, `scripts/rollout_copier_fleet.py:17`, `scripts/verify_ci_gate.py:166-167`
- Test: `tests/test_migration_marker.py` (create)

**Interfaces:**
- Produces (в `ci_kit.migration_marker`):
  - `MARKER: str = "<!-- ci-kit-migrate -->"`
  - `CHECKS_AFTER: dict[str, list[str]] = {"core": ["ci / gate"]}`
  - `parse_marker(body: str) -> dict` — переносится без изменений из `migrate.py`, те же сообщения `ValueError`
  - `check_contexts(data: dict) -> list[str]` — переносится из `legacy_jobs.py` вместе с `CONTRACT_JOB`, `CONTRACT_CHECK` и приватными хелперами, которые ей нужны (`_jobs`)
  - `legacy_check_names(validate_yml: Path) -> list[str]` — из `migrate.py`
- `migrate.py` и `legacy_jobs.py` реэкспортируют эти имена из `migration_marker` (удаляются в задаче 2).

- [ ] **Step 1: Написать `tests/test_migration_marker.py`**
  Перенести без изменений ассертов: тесты `parse_marker` из `tests/test_ci_kit_migrate.py` (класс с `result()`/`render_marker` — строить тело маркера в тесте вручную: `MARKER + "\n```json\n" + json.dumps(payload) + "\n```"`), тесты `check_contexts` из `tests/test_ci_kit_legacy_jobs.py:170-192` (фикстуры `SKILLS`, `MARKETDESK`, `with_job` перенести), тесты `legacy_check_names` из `tests/test_ci_kit_migrate.py:36-54`. Импорт — только из `ci_kit.migration_marker`.
- [ ] **Step 2: Запустить — FAIL** (`ModuleNotFoundError: ci_kit.migration_marker`)
  `GIT_CONFIG_GLOBAL=/dev/null python -m unittest tests.test_migration_marker -v`
- [ ] **Step 3: Создать `tools/ci_kit/migration_marker.py`**, перенести код, в `migrate.py`/`legacy_jobs.py` заменить определения импортом. Перевести импорты `migration_checks.py`, `rollout_copier_fleet.py`, `verify_ci_gate.py` на `ci_kit.migration_marker`.
- [ ] **Step 4: Запустить — PASS**
  `GIT_CONFIG_GLOBAL=/dev/null python -m unittest tests.test_migration_marker tests.test_migration_checks tests.test_ci_rollout tests.test_ci_kit_migrate tests.test_ci_kit_legacy_jobs -v`
- [ ] **Step 5: Commit** `refactor(ci-kit): move migration marker and check contexts to their own module`

### Task 2: Вывести миграцию validate

**Files:**
- Delete: `tools/ci_kit/migrate.py`, `tools/ci_kit/equivalence.py`, `tools/ci_kit/legacy_jobs.py`, `tests/test_ci_kit_migrate.py`, `tests/test_migration_lifecycle.py`, `tests/test_ci_kit_equivalence.py`, `tests/test_ci_kit_legacy_jobs.py`, `docs/errors/CIK-MIGRATE-00{1..5}.md`
- Modify: `tools/ci_kit/cli.py` (удалить `_migrate` и парсер `migrate`), `tools/ci_kit/codes.py` (удалить `CIK-MIGRATE-*`), `scripts/update_copier_fleet.py` (`--migrate` и всё, что используется только им: `run_ci_kit_migrate`, `read_legacy_jobs`, `migration_modules_section`, `migration_title`, `migrate_repository`, ветки статусов `migrate-rejected`/`would-migrate` в отчёте, импорты `legacy_jobs`/`migrate`), `scripts/rollout_copier_fleet.py` (`--migrate`, `MIGRATE_PILOTS`, ветки `args.migrate`), `scripts/validate_modules_fixture.py:138-143` (шаг migrate и `legacy_repo`, если больше не используется), `scripts/run_test_suites.py` (убрать удалённые файлы), `tests/test_update_copier_fleet.py` и `tests/test_ci_rollout.py` (тесты migrate-пути)
- Docs: в `docs/errors/CIK-UPDATE-001.md`, `docs/errors/CIK-HANDOFF-002.md` и прочих runbook заменить `ci-kit migrate` на ссылку на `docs/errors/CIK-LEGACY-006.md` (создаётся в задаче 3)

**Interfaces:**
- Consumes: `ci_kit.migration_marker` из задачи 1.
- Produces: CLI `ci-kit` без подкоманды `migrate`; `update_copier_fleet.py` и `rollout_copier_fleet.py` без `--migrate`.

- [ ] **Step 1: Тест на отсутствие подкоманды** в `tests/test_validation_entrypoints.py` (или в тестах CLI, где проверяются подкоманды): `test_cli_has_no_migrate` — `python -m ci_kit.cli migrate --help` завершается кодом 2, stderr содержит `invalid choice: 'migrate'`. Тест в `tests/test_update_copier_fleet.py`: `test_migrate_flag_is_gone` — парсер отклоняет `--migrate core` (код 2).
- [ ] **Step 2: Запустить — FAIL.**
- [ ] **Step 3: Удалить код и тесты по списку Files.** Перед удалением каждого кода `CIK-MIGRATE-*` проверить `grep -rn "CIK-MIGRATE" tools scripts tests docs`; ссылка, которая осталась вне удаляемого, — повод остановиться и сообщить, а не удалять молча. Убедиться: `grep -rn "legacy_jobs\|ci_kit.migrate\b\|equivalence" tools scripts tests` пусто.
- [ ] **Step 4: Запустить полный набор — PASS**
  `GIT_CONFIG_GLOBAL=/dev/null python scripts/run_test_suites.py` и `python scripts/validate_modules_fixture.py`
- [ ] **Step 5: Commit** `refactor(ci-kit): retire validate migration now that every consumer is on core` (без `!`: версия minor, Global Constraints)

### Task 3: Удалить legacy validate, guard `CIK-LEGACY-006`

**Files:**
- Delete: `template/.github/workflows/validate.yml.jinja`
- Modify: `copier.yml` (строка `_exclude` для `validate.yml`; guard; `handed_off` default и валидатор), `tools/ci_kit/legacy.py` (`legacy_findings`), `tools/ci_kit/codes.py`, `template/docs/ci-kit.md.jinja:10,34-35`
- Create: `docs/errors/CIK-LEGACY-006.md`
- Test: `tests/test_handoff_registry.py`, `tests/test_ci_kit_legacy.py`, `tests/test_module_selector.py`, `tests/module_test_support.py:96`
- Fix fallout: `tests/test_composite_actions.py:1658,1789-1858`, `tests/test_allure_publisher.py:503-590`, `tests/test_validation_entrypoints.py:185`, `tests/test_checkout.py:80`, `tests/test_ci_kit_drift.py`, `tests/test_ci_kit_update.py:119`, `scripts/validate.py:2150-2156`, `scripts/validate_modules_fixture.py` (`legacy_repo`), `docs/usage.md`, `docs/human-guide.md`, `docs/architecture.md`, `docs/modules.md`, `README.md`

**Interfaces:**
- Consumes: ничего из задач 1–2, кроме того, что `migrate` больше нет.
- Produces: `legacy_findings(repo, module_owned) -> list[Finding]` дополнительно возвращает `Finding("CIK-LEGACY-006", "legacy", "<message>", ".copier-answers.yml")`, когда `handed_off` — корректный список без `validate`.

- [ ] **Step 1: Тесты guard и default** в `tests/test_handoff_registry.py`, рендер через существующий `render_tree`/`subprocess` copier этого файла:
  - `test_copy_without_validate_refuses`: `copier copy` legacy с `--data handed_off=[]` → ненулевой код, вывод содержит `CIK-LEGACY-006`, каталог назначения без `.github/workflows`.
  - `test_default_hands_validate_off`: копия без `handed_off` → в `.copier-answers.yml` `handed_off:\n- validate`, файла `.github/workflows/validate.yml` нет.
  - `test_update_handed_off_repo_passes`: копия с `[validate]` на теге `v99.0.0`, коммит, `copier update --defaults` на `v99.0.1` → код 0.
  - `test_update_without_handed_off_key_refuses`: копия с `[validate]`, затем из `.copier-answers.yml` удаляется строка `handed_off` и коммит; `copier update --defaults` → ненулевой код, `CIK-LEGACY-006` в выводе, рабочее дерево без изменений.
  - `test_modules_render_without_guard`: рендер `module=core` проходит (существующий `test_modules_do_not_ask_handed_off` остаётся).
  - Валидатор: `handed_off=[]` в интерактивной валидации отклоняется с текстом `validate must stay handed off`; удалить `test_handed_off_validate_excludes_validate_yml` и ассерт строки 59 (legacy без handoff рендерит `validate.yml`).
- [ ] **Step 2: Тесты `plan`** в `tests/test_ci_kit_legacy.py`: `test_validate_not_handed_off_reports_legacy_006` (`legacy_repo(handed_off=[])` → среди кодов `CIK-LEGACY-006`); `test_malformed_handed_off_keeps_handoff_003` (`handed_off="oops"` → `CIK-HANDOFF-003`, без `CIK-LEGACY-006`). Фикстура `legacy_repo` в этом файле рендерит legacy с `handed_off=[]` — перевести её на запись `handed_off` в answers после рендера с `[validate]`, чтобы фикстура не упиралась в guard.
- [ ] **Step 3: Запустить — FAIL** (`GIT_CONFIG_GLOBAL=/dev/null python -m unittest tests.test_handoff_registry tests.test_ci_kit_legacy -v`).
- [ ] **Step 4: Реализация.**
  - Удалить `validate.yml.jinja` и строку `_exclude` для `validate.yml`.
  - Guard — новый элемент `_exclude` по образцу `__allure_pin_mismatch__` (`copier.yml:9-14`): при `module == 'legacy'` и `'validate'` не в эффективном `handed_off` → `_copier_conf['__legacy_validate_removed__'] | mandatory('CIK-LEGACY-006: the legacy template no longer renders validate.yml. Hand validate off to the core module first; see docs/errors/CIK-LEGACY-006.md')`.
  - Review Focus 1 решается так: «эффективный `handed_off`» берётся из сохранённых ответов, когда они есть (`_copier_answers`), иначе из текущего значения. Если Copier не даёт отличить отсутствующий ключ от default (проверяется тестом `test_update_without_handed_off_key_refuses`), default `handed_off` остаётся `[]`, а свежая копия требует `--data 'handed_off=[validate]'`; это отклонение от spec D2 — сообщить владельцу в отчёте задачи.
  - `handed_off`: default `[validate]` (при условии выше), валидатор добавляет `validate must stay handed off` для списка без `validate`.
  - `legacy_findings`: находка `CIK-LEGACY-006`, только если `handed_off` — список строк из `AREAS` без `validate`. Сообщение: `validate is not handed off; the legacy template no longer renders validate.yml. Migrate with ci-kit <last release with legacy validate>, see docs/errors/CIK-LEGACY-006.md`.
  - `docs/errors/CIK-LEGACY-006.md` в формате соседних runbook (Cause / Fix / Example). Fix: установить CLI ci-kit с последнего релиза, где legacy validate есть (`vLAST`; на момент записи v3.7.0, впишется при релизе), выполнить `ci-kit update --module legacy --to vLAST`, затем `ci-kit migrate --module core --repo . --to vLAST`, затем обычный update на текущий релиз.
  - `template/docs/ci-kit.md.jinja`: убрать строку `validate.yml` и пункт чек-листа про пути компонентов.
- [ ] **Step 5: Запустить — PASS** для тестов шагов 1–2.
- [ ] **Step 6: Поправить остальные тесты и скрипты.** Правило: тест, проверяющий содержимое legacy `validate.yml` (`template-contract`, legacy component-jobs, Allure-загрузка из legacy jobs), удаляется — эту функциональность покрывают тесты модулей; тест, где `validate.yml` — только фикстура или пример пути, переводится на рендер `core`. `scripts/validate.py:2150-2156` удалить (проверка legacy contract). `module_test_support.normalize_answers` нормализует и `handed_off: [validate]`. `test_checkout.py:80`: убрать `validate.yml` из ожидаемого набора legacy.
- [ ] **Step 7: Docs.** README, `docs/usage.md`, `docs/human-guide.md`, `docs/architecture.md`, `docs/modules.md`: новый репозиторий = legacy + `core` (+ языковые модули); абзацы о legacy validate и `ci-kit migrate` заменить ссылкой на `CIK-LEGACY-006`. `grep -rn "ci-kit migrate\|template-contract" README.md docs/*.md docs/errors template` — только исторические упоминания в specs/plans.
- [ ] **Step 8: Полная проверка — PASS**
  `GIT_CONFIG_GLOBAL=/dev/null python scripts/run_test_suites.py`, `python scripts/validate.py`, `python scripts/validate_modules_fixture.py`. Grep по изменённым файлам на `TODO|skip|only|NotImplemented` — пусто.
- [ ] **Step 9: Commit** `feat(template): remove legacy validate and refuse repositories that still use it`; тело — абзац «Breaking для не-флита»: legacy-копия без `core` больше не получает `validate.yml`; `ci-kit migrate` удалён, путь миграции — `docs/errors/CIK-LEGACY-006.md`.

## После реализации (каждый шаг — с подтверждения владельца)

1. Push ветки, PR в ci-kit (тест-план — команды шага 8), ревью, merge.
2. Релиз release-please; перед ним вписать `vLAST` (последний тег до этого релиза) в `docs/errors/CIK-LEGACY-006.md`. Fleet `update` по 11 + knowledge вручную; критерий: PR зелёные без ручных правок.
3. Отметить пункт в чек-листе #466.
4. Закрытие RFC по spec §9: issues по оставшимся пунктам §4, итоговый комментарий и закрытие #393, закрытие #466, статус заметки в knowledge#73.
