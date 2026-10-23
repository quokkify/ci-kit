# Выход из legacy-шаблона — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Перевести весь CI потребителей с legacy-шаблона на модули `core`/`security`/`release`/`allure`/`renovate` (v3.10.0), затем удалить legacy (v4.0.0).

**Architecture:** Каждая legacy-область получает модуль-владельца и запись в реестре hand-off; legacy исключает переданные области, временная `ci-kit migrate-legacy` переводит ответы и проверяет эквивалентность; инструменты учатся работать без `.copier-answers.yml`; v4 удаляет `template/` и переходный код.

**Tech Stack:** Python 3 (`unittest`), Copier 9.19.1, Jinja, PyYAML, jsonschema, GitHub Actions (reusable workflows).

**Spec:** `docs/superpowers/specs/2026-10-23-legacy-exit-design.md`

## Global Constraints

- Два PR: **PR 1** = задачи 1–7 → релиз v3.10.0 (minor, без `!`); **PR 2** = задачи 8–9 → v4.0.0 (`feat!:` с футером `BREAKING CHANGE:`), начинается только после раскатки v3.10.0 и паузы (spec §5.4).
- Required contexts не меняются: `CodeQL`, `gitleaks`/`Gitleaks`, `ci / gate`.
- Новые модули требуют `core` (`MODULES[m].requires == ("core",)`); все answers-файлы имеют одинаковые `toolkit_version` и `_commit`.
- Области и файлы — ровно таблица spec §5.1; модули и вопросы — ровно таблица spec §4.
- Новые коды: `CIK-CORE-012` «Answers files disagree on toolkit_version», `CIK-CORE-030` «Legacy answers still render files», `CIK-MIGRATE-001..005` (тексты — spec §5.2). Каждый код — `tools/ci_kit/codes.py` + `docs/errors/<code>.md` по образцу `docs/errors/CIK-LEGACY-005.md`.
- Env: `.venv/bin` первым в `PATH` (copier 9.19.1, PyYAML 6.0.3, jsonschema 4.26.0, `ci_kit` editable); тесты с `GIT_CONFIG_GLOBAL=/dev/null`; полная проверка `python scripts/run_test_suites.py`, `python scripts/validate.py`, `python scripts/validate_modules_fixture.py`. На macOS заранее падают `test_allure_publisher::test_legacy_allure_update_rejects_retained_pin_before_writes` и `test_composite_actions::test_switches_external_and_component_modes_without_conflicts` (`/var` vs `/private/var`); доказательство — CI.
- Публичный репозиторий: без внутренних URL, токенов, путей машины. Коммиты — Conventional Commits с `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. Ручные правки потребителя в файле legacy-области (например, своя строка в `release.yml`) — `migrate-legacy` должен отказать `CIK-MIGRATE-003` до любых коммитов, а не перезаписать правку. Тест — задача 6.
2. `copier update` модуля `security`/`release`/`allure` на репозитории, где legacy ещё рендерит ту же область (не передана) — должно ловиться `CIK-CORE-011` в `plan`, а не тихо давать два владельца. Тест — задача 1.
3. Стартовые файлы (`README.md`, `renovate.json`, release-please `config.json`/`manifest.json`, PR template), уже изменённые проектом — миграция и последующий `update` их не трогают. Тест — задачи 4 и 6.
4. Потребитель с `codeql: false` и `gitleaks: true` (или наоборот) — `security` рендерит только включённый файл, required checks совпадают. Тест — задача 3.
5. `copier-update.yml` у репозитория, где legacy уже удалён (после миграции и до v4) и где он ещё есть — оба пути проходят. Тест — задача 4.

---

### Task 1: Реестр областей и модуль `release` (образец для остальных)

**Files:**
- Modify: `tools/ci_kit/handoff.py` (`AREAS`: все 6 областей spec §5.1), `copier.yml` (`module.choices` += `security, release, allure, renovate`; `handed_off` validator: допустимые области = `AREAS`; `_exclude`: по строке на область, как для `validate`; вопрос `release_mode` получает `when: module == 'release' or (module == 'legacy' and release_please)`), `tools/ci_kit/modules.py` (`release`: `requires=("core",)`, без job), `tools/ci_kit/schemas/release.v1.json`
- Create: `modules/release/template/` — `{{ _copier_conf.answers_file }}.jinja`, `.ci/modules/release.yml.jinja` (`schema_version: 1`), копии `template/.github/workflows/release.yml.jinja`, `template/.github/release-please/{config,manifest}.json.jinja`, `template/.github/scripts/enrich_release_notes.py.jinja`
- Modify: `copier.yml` `_skip_if_exists` — release-please config/manifest действуют и для модуля (пути одинаковые)
- Test: `tests/test_handoff_registry.py`, `tests/test_modules_render.py`, `tests/test_legacy_module_parity.py` (create)

**Interfaces:**
- Produces: `AREAS: dict[str, Area]` с ключами `validate, base, security, release, allure, renovate`; `tests/test_legacy_module_parity.py::assert_parity(area: str, module: str, scenario: str, exclude: set[str] = set())` — рендерит legacy-сценарий `tests/scenarios/<scenario>.yml` без hand-off и модуль с соответствующими ответами, сравнивает байты файлов области. Используется задачами 2–4.
- Produces: `tools/ci_kit/legacy_answers.py::module_answers(legacy: dict) -> dict[str, dict]` — перевод ответов по таблице spec §5.2 (используется parity-тестами и задачей 6). Здесь — только ветка `release`; задачи 2–4 добавляют свои.

- [ ] **Step 1:** Тесты: `test_registry_matches_spec` (ключи и пути `AREAS` = spec §5.1), `test_handed_off_accepts_every_area`, `test_release_area_excluded_when_handed_off` (legacy-рендер с `handed_off: [validate, release]` не содержит 4 release-файла), `test_release_module_renders_area_files`, `test_release_parity` (`assert_parity("release", "release", s)` для каждого legacy-сценария с `release_please: true`), `test_double_owner_reported` (legacy без hand-off `release` + модуль `release` → `build_plan` содержит `CIK-CORE-011` или `CIK-HANDOFF-002` для `release`).
- [ ] **Step 2:** Запустить — FAIL.
- [ ] **Step 3:** Реализация по списку Files; `module_answers` для release: `{"release": {"release_mode": legacy["release_mode"]}}` если `release_please`, иначе нет ключа.
- [ ] **Step 4:** Тесты задачи + `tests.test_ci_kit_legacy tests.test_module_selector tests.test_modules_render` — PASS.
- [ ] **Step 5:** Commit `feat(modules): add the release module and hand-off areas for every legacy file`.

### Task 2: Модули `allure` и `renovate`

**Files:**
- Create: `modules/allure/template/` (answers, `.ci/modules/allure.yml.jinja`, копии `allure-report.yml.jinja`, `.github/allure/allurerc.mjs.jinja`, `.github/allure/safe_extract.py.jinja`), `modules/renovate/template/` (answers, `.ci/modules/renovate.yml.jinja`, копия `.github/renovate.json.jinja`), схемы `allure.v1.json`, `renovate.v1.json`, записи `MODULES`
- Modify: `copier.yml` — allure-вопросы и renovate-вопросы получают `when` для своего модуля; `allure_source` для модуля: `choices: [component, external]`, default `external`; `allure_mode` для модуля = `allure_source`; `renovate_presets` для модуля: default `['default', 'github-actions']`; legacy-поведение не меняется. `.github/workflows/allure-publisher-core.yml`, `allure-pages.yml`: путь `safe_extract` → `.toolkit/modules/allure/template/.github/allure/safe_extract.py.jinja`; `scripts/validate.py:1684,2133` — тот же путь.
- Test: parity-тесты, `tests/test_safe_extract.py`, `tests/test_allure_publisher.py`

**Interfaces:**
- Consumes: `assert_parity`, `module_answers` (задача 1).
- Produces: ветки `module_answers` для `allure` (`allure_source: auto` → `component` если `components` непусто, иначе `external`; остальные `allure_*` как есть, `project_name` из legacy) и `renovate` (`renovate_presets`: если в legacy задан явно — он, иначе вычисленный legacy-default из `components`/`docker` по `copier.yml:350-360`).

- [ ] **Step 1:** Тесты: `test_allure_parity` (все legacy-сценарии с `allure_report: true`), `test_renovate_parity` (все с `renovate: true`), `test_safe_extract_copies_identical` (байты `template/...safe_extract.py.jinja` == `modules/allure/...` — дубль живёт до v4), то же для `enrich_release_notes.py.jinja` (release), `test_module_answers_allure_auto_resolves` (оба случая auto), `test_module_answers_renovate_explicit_presets`.
- [ ] **Step 2:** FAIL. **Step 3:** реализация. **Step 4:** тесты задачи + `tests.test_allure_publisher tests.test_safe_extract tests.test_renovate_config` — PASS (кроме известной macOS-ошибки).
- [ ] **Step 5:** Commit `feat(modules): add allure and renovate modules`.

### Task 3: Модуль `security` и reusable CodeQL

**Files:**
- Create: `.github/workflows/codeql-analysis.yml` (`on: workflow_call`, inputs `languages-override: string` (JSON-список, default `"[]"`), `toolkit-ref: string`); job `plan` ставит ci-kit на `toolkit-ref` и выполняет `ci-kit plan --repo . --github-output`, job `analyze` (`name: Analyze (${{ matrix.language }})`, matrix из output `codeql`) — шаги, `permissions`, `timeout-minutes`, `category: "/language:<lang>"` и пины `github/codeql-action` 1:1 из `template/.github/workflows/codeql.yml.jinja`. `modules/security/template/` (answers, `.ci/modules/security.yml.jinja`, `.github/workflows/codeql.yml.jinja` — `name: CodeQL`, триггеры и `concurrency` как в legacy, один job `codeql` с `uses: quokkify/ci-kit/.github/workflows/codeql-analysis.yml@{{ toolkit_version }}`, `with: languages-override: {{ codeql_languages | to_json | tojson }}`; копия `gitleaks.yml.jinja`). Схема `security.v1.json`, `MODULES["security"]`.
- Modify: `tools/ci_kit/cli.py` `_plan` — новый output `codeql`: `["actions"] + [{"java": "java-kotlin", "python": "python", "node": "javascript-typescript"}[m] for m in modules if language module]`, порядок `actions, python, javascript-typescript, java-kotlin` как в legacy-итерации компонентов; `copier.yml` — `codeql`, `gitleaks`, `codeql_languages` получают `when` для `security`; `_exclude` модуля: `codeql.yml` если не `codeql`, `gitleaks.yml` если не `gitleaks`.
- Test: `tests/test_codeql_module.py` (create), `tests/test_ci_kit_plan.py`

**Interfaces:**
- Produces: plan output `codeql: list[str]`; ветка `module_answers` для `security` (`codeql`, `gitleaks`, `codeql_languages` как есть; модуль ставится, если `codeql or gitleaks`).

- [ ] **Step 1:** Тесты: `test_plan_outputs_codeql_languages` (core+java+node → `["actions","javascript-typescript","java-kotlin"]` в порядке legacy для тех же компонентов), `test_codeql_language_parity` (для каждого legacy-сценария: множество языков legacy `codeql.yml` == `plan` на мигрированных модулях этого сценария, и `category` совпадает), `test_override_wins`, `test_gitleaks_parity` (байты), `test_security_flags` (`codeql: false, gitleaks: true` → только `gitleaks.yml`; наоборот), `test_reusable_codeql_actionlint` (actionlint `.github/workflows/codeql-analysis.yml` и рендера модуля).
- [ ] **Step 2:** FAIL. **Step 3:** реализация. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat(modules): add the security module with plan-derived CodeQL languages`.

### Task 4: `core` получает базовые файлы; `ci-kit update` без legacy

**Files:**
- Modify: `modules/core/template/` += `.github/workflows/copier-update.yml.jinja`, `docs/ci-kit.md.jinja` (D6: один текст, без таблицы файлов, ссылки на `docs/modules.md` и `ci-kit doctor`), `README.md.jinja`, `.github/pull_request_template.md.jinja` (копии legacy; README без legacy-ветвлений по `module`); `copier.yml` `_skip_if_exists` уже покрывает README/PR template
- Core `copier-update.yml.jinja`: legacy-шаг (`copier update` + проверки `.copier-answers.yml`) выполняется только при наличии `.copier-answers.yml`; модульный шаг — `ci-kit update --repo . --to "$ref"` без `--skip-legacy`, когда legacy нет, и с `--skip-legacy` после legacy-шага, когда он есть; release helper сравнивается с `modules/release/template/.github/scripts/enrich_release_notes.py.jinja`, а проверка «helper обязателен» читает наличие `.copier-answers.release.yml` (или `release_please: true` у legacy).
- Modify: `tools/ci_kit/update.py` — `trust=True` для всех; порядок `legacy` (если есть), `core`, затем `MODULES` в фиксированном порядке `security, release, allure, renovate, java, python, node, docker`. `tools/ci_kit/plan.py` — `CIK-CORE-012`: `toolkit_version`/`_commit` расходятся между answers-файлами (legacy включительно).
- Test: `tests/test_copier_update_workflow.py`, `tests/test_ci_kit_update.py`, `tests/test_ci_kit_plan.py`, parity для `base` (кроме `docs/ci-kit.md`)

**Interfaces:**
- Produces: core-рендер владеет областью `base`; `update_order(modules: set[str]) -> list[str]` в `update.py` (порядок выше); ветка `module_answers` для `core` — ничего нового (core уже стоит).

- [ ] **Step 1:** Тесты: `test_base_parity` (`copier-update.yml`, README, PR template против legacy с учётом нового модульного шага — для `copier-update.yml` проверять не байты, а: legacy-шаг под условием наличия файла, `ci-kit update` присутствует, путь helper новый), `test_copier_update_without_legacy` (рендер + исполнение скрипта шага в фикстуре без `.copier-answers.yml`: вызывает `ci-kit update` без `--skip-legacy`), `test_copier_update_with_legacy` (как сейчас), `test_starter_files_not_overwritten` (изменённые README/PR template переживают `ci-kit update`), `test_update_order`, `test_core_012_toolkit_mismatch`.
- [ ] **Step 2:** FAIL. **Step 3:** реализация. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat(core): own copier-update, docs and starter files`.

### Task 5: Инструменты флита без legacy

**Files:**
- Modify: `scripts/fleet_health.py:159` (fallback на `.copier-answers.core.yml`), `scripts/verify_ci_gate.py` (`verify_pilot`: answers = legacy, иначе core; `_src_path`/`_commit` из него; `template_contexts`: `("ci / gate",)` + `"CodeQL"` если у `security` `codeql`, + `"gitleaks"` если `gitleaks` — для модульного репозитория; legacy-ветка остаётся), `scripts/update_copier_fleet.py` (audit/inventory из module answers при отсутствии legacy — `modules_inventory` уже есть, расширить на новые модули; `merge_ready_repository`/primary = core без legacy; `update_template` пропускает legacy update, если файла нет; `verify_release_helper` — новый путь helper)
- Test: `tests/test_fleet_health.py`, `tests/test_ci_rollout.py`, `tests/test_update_copier_fleet.py`

**Interfaces:**
- Consumes: модули задач 1–4.
- Produces: все три скрипта принимают репозиторий только с module answers.

- [ ] **Step 1:** Тесты по одному на скрипт: `test_health_reads_core_answers_without_legacy`, `test_pilot_without_legacy_uses_core_answers`, `test_template_contexts_for_module_repo` (security codeql+gitleaks → `{"ci / gate","CodeQL","gitleaks"}`), `test_fleet_update_without_legacy_answers` (реальный copier на фикстуре только из модулей: update проходит, PR-путь мокается как в существующих тестах).
- [ ] **Step 2:** FAIL. **Step 3:** реализация. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat(fleet): support repositories without legacy answers`.

### Task 6: `ci-kit migrate-legacy`

**Files:**
- Create: `tools/ci_kit/migrate_legacy.py`, `docs/errors/CIK-MIGRATE-00{1..5}.md`, `tests/test_migrate_legacy.py`
- Modify: `tools/ci_kit/cli.py` (подкоманда `migrate-legacy --repo --to [--template-src] [--json]`), `tools/ci_kit/codes.py`, `tools/ci_kit/legacy_answers.py` (полная таблица spec §5.2)

**Interfaces:**
- Consumes: `module_answers` (задачи 1–3), `AREAS`, `render_module` (`drift.py`), `commit_all`/`_git`/`_rejects` (`update.py`), `migration_marker.MARKER`/`parse_marker`, `build_plan`.
- Produces: `migrate_legacy(repo: Path, to: str, *, src_override: str | None = None) -> MigrateLegacyResult` (`modules: list[str]`, `commits: list[str]`, `checks_before: list[str]`, `checks_after: list[str]`, `findings: list[Finding]`); `render_marker(result) -> str` в формате `migration_marker` (`module: "legacy-exit"`, `area: "base,security,release,allure,renovate"`).

Алгоритм — spec §5.2 шаги 1–6. `checks_before`/`checks_after` — required contexts, которые дают legacy и модули (`CodeQL` при `codeql`, `gitleaks` при `gitleaks`, `ci / gate`); неравенство → `CIK-MIGRATE-005`.

- [ ] **Step 1:** Тесты: `test_every_legacy_scenario_migrates` (subTest на каждый `tests/scenarios/*.yml`, поверх hybrid(validate)-фикстуры из `tests/test_hybrid_update_lifecycle.py`: нет находок, `.copier-answers.yml` содержит `handed_off` со всеми 6 областями и не рендерит файлов, файлы областей байтово равны кроме `codeql.yml`/`docs/ci-kit.md`, `build_plan` чист, `checks_before == checks_after`), `test_hand_edit_refuses_before_commits` (правка `release.yml` → `CIK-MIGRATE-003`, HEAD и дерево не изменились), `test_dirty_tree_001`, `test_wrong_ref_002`, `test_already_handed_off_004`, `test_equivalence_failure_rolls_back_005` (подмена рендера модуля → откат, включая созданные ignored-файлы), `test_starter_files_kept` (изменённые `README.md`/`renovate.json` не меняются), `test_lifecycle_migrate_update_revert` (миграция → `ci-kit update` на `v99.1.0` чист → `git revert` коммитов миграции возвращает legacy-рендер), `test_cli_json_and_marker`.
- [ ] **Step 2:** FAIL. **Step 3:** реализация. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat(ci-kit): migrate-legacy hands every remaining area to modules`.

### Task 7: Раскатка `--migrate-legacy` и документация v3.10

**Files:**
- Modify: `scripts/update_copier_fleet.py` (`--migrate-legacy`: ветка `ci-kit/migrate-legacy`, запускает `ci-kit migrate-legacy`, PR с маркером и таблицей модулей; статусы `would-migrate`/`migrate-rejected`; несовместим с `--auto-merge`/`--merge-ready`), `scripts/rollout_copier_fleet.py` (`--migrate-legacy` по pilot-кольцу `quokkify/gh-pages-subdir-action, quokkify/skills, quokkify/q4j`, ожидаемый head = маркер), `docs/modules.md`, `docs/usage.md`, `README.md`, `docs/human-guide.md`, `docs/architecture.md`, `template/README.md.jinja` не трогать (уходит в v4)
- Test: `tests/test_update_copier_fleet.py`, `tests/test_ci_rollout.py`

**Interfaces:**
- Consumes: `migrate_legacy`, `render_marker` (задача 6).

- [ ] **Step 1:** Тесты: `test_migrate_legacy_flag_opens_marked_pr`, `test_migrate_legacy_rejection_reports_codes`, `test_migrate_legacy_rejects_auto_merge`, `test_rollout_migrate_legacy_pilots`.
- [ ] **Step 2:** FAIL. **Step 3:** реализация + docs: новый репозиторий = `core` + нужные из `security/release/allure/renovate` + языки; legacy описан как переходный до v4.
- [ ] **Step 4:** Полная проверка (Global Constraints) — PASS кроме известных macOS-ошибок; grep изменённых файлов на `TODO|skip|only|NotImplemented` — пусто.
- [ ] **Step 5:** Commit `feat(fleet): roll out migrate-legacy`.

**Конец PR 1.** После мёржа (с подтверждения владельца): релиз v3.10.0, dry-run `--migrate-legacy` по 12, pilot-кольцо, остальные, knowledge вручную, пауза в один релиз (spec §5.4).

### Task 8 (PR 2): Поведение v4 для legacy answers

**Files:**
- Modify: `tools/ci_kit/plan.py` (`CIK-CORE-030`, если `.copier-answers.yml` есть и в `handed_off` нет хотя бы одной области), `scripts/update_copier_fleet.py` (`.copier-answers.yml` со всеми областями → коммит `chore(ci-kit): drop empty legacy answers` с удалением файла; иначе отказ `CIK-CORE-030` без изменений), `copier-update.yml.jinja` (core) — то же для самообновления, `docs/errors/CIK-CORE-030.md` (миграция CLI v3.10.x)
- Test: `tests/test_ci_kit_plan.py`, `tests/test_update_copier_fleet.py`, `tests/test_copier_update_workflow.py`

- [ ] **Step 1:** Тесты: `test_core_030_when_area_not_handed_off`, `test_fleet_drops_empty_legacy_answers` (один коммит, только удаление файла), `test_fleet_refuses_partial_legacy` (дерево не изменилось), `test_copier_update_drops_empty_legacy`.
- [ ] **Step 2–4:** FAIL → реализация → PASS.
- [ ] **Step 5:** Commit `feat(fleet): drop empty legacy answers and refuse partial ones`.

### Task 9 (PR 2): Удаление legacy

**Files:**
- Delete: `template/`, `tools/ci_kit/legacy.py`, `handoff.py`, `legacy_answers.py`, `migrate_legacy.py`, коды и runbooks `CIK-LEGACY-*`, `CIK-HANDOFF-*`, `CIK-MIGRATE-*`, `tests/scenarios/*.yml` (legacy; `tests/scenarios/modules/` остаются), тесты legacy/hand-off/parity/migrate-legacy, `--skip-legacy`, legacy-ветки `update.py`, `update_copier_fleet.py`, `verify_ci_gate.py`, `fleet_health.py`, core `copier-update.yml`
- Modify: `copier.yml` (`_subdirectory` → `modules/{{ module }}/template`; `module` без `legacy`, default `core`; удалить legacy-вопросы, `handed_off`, `_external_data`, legacy-`_exclude`), `.github/renovate.json` (regex managers на `template/` → `modules/*/template/`), `scripts/render_codeql_workflows.py` (рендер модулей из `tests/scenarios/modules/`), `scripts/validate.py` (legacy-проверки), Allure-workflows уже на модульном пути (задача 2), docs
- Test: весь набор

- [ ] **Step 1:** Тесты: `test_module_defaults_to_core` (`copier copy` без `-d module` → core), `test_no_legacy_choice` (`-d module=legacy` → ошибка выбора), `test_no_template_dir_references` (grep `template/` в `scripts`, `tools`, `.github` пуст, кроме `modules/*/template`).
- [ ] **Step 2–3:** FAIL → удаление и правки; убедиться, что `grep -rn "legacy" tools scripts .github copier.yml` содержит только исторические упоминания в docs/CHANGELOG.
- [ ] **Step 4:** Полная проверка — PASS кроме известных macOS-ошибок.
- [ ] **Step 5:** Commit `feat!: remove the legacy template` с футером `BREAKING CHANGE: module: legacy, template/ and .copier-answers.yml are gone; migrate with ci-kit v3.10.x (docs/errors/CIK-CORE-030.md).`

**Конец PR 2.** После мёржа (с подтверждения владельца): релиз v4.0.0, fleet `update` удаляет пустые `.copier-answers.yml` у всех 12; закрыть #490, #492, обновить #491.
