# Модуль `labels` — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Перенести вызов синхронизации меток из legacy-шаблона в модуль `labels` и дать общий способ поставить модуль в существующие репозитории (`ci-kit add`, fleet `--add-module`).

**Architecture:** `modules/labels/template` рендерит `labels.yml`; реестр `MODULES` получает job-less модули, `gate` их пропускает. `ci-kit add` делает `copier copy` модуля одним коммитом с проверками и откатом; fleet-скрипт вызывает его как subprocess и открывает PR, rollout использует существующее pilot-кольцо.

**Tech Stack:** Python 3 (`unittest`), Copier 9.19.1, Jinja, PyYAML, jsonschema, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-23-labels-module-design.md`

## Global Constraints

- Ветка `feat/label-sync` (worktree `~/Code/quokkify/.worktrees/ci-kit/feat-label-sync`), PR #467, база `main`.
- Модуль `labels`: `requires=("core",)`, `job=None`, вопросы — только `toolkit_version`; файлы — ровно таблица spec §4.
- Форма реестра совпадает с веткой spec #6: `ModuleInfo.job: str | None = None` (поле остаётся вторым позиционным).
- Коды `CIK-ADD-001..005` — тексты spec §5; каждый в `tools/ci_kit/codes.py` + `docs/errors/<code>.md` по образцу `docs/errors/CIK-UPDATE-001.md`.
- Коммит установки: `chore(ci-kit): add <m> module`; PR-ветка `ci-kit/add-<m>`; метка PR `area: template` через `create_pull_request`.
- Required checks не меняются.
- Env: `uv run`; тесты с `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/dev/null` (глобальные хуки машины ломают фикстурные коммиты). Полная проверка: `scripts/run_test_suites.py`, `scripts/validate.py`, `scripts/validate_modules_fixture.py`; `validate.py --static` ждёт renovate-presets#63 (известная ошибка покрытия пресета).
- Публичный репозиторий: без внутренних URL, токенов, путей машины. Коммиты — Conventional Commits с `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. У потребителя уже есть свой `.github/workflows/labels.yml` — `ci-kit add` отказывает `CIK-ADD-004`, дерево и HEAD не меняются. Тест — задача 2.
2. Репозиторий только на legacy (нет `.copier-answers.core.yml`) — `CIK-ADD-003`, а не модуль без `core`. Тест — задача 2.
3. Copier падает посреди `copy` и оставляет неотслеживаемые файлы — откат возвращает чистое дерево и исходный HEAD. Тест — задача 2.
4. Повторный fleet-запуск при открытом PR на `ci-kit/add-labels` — PR обновляется, второй не создаётся. Тест — задача 3.
5. Core отстал от целевого ref (template update не смёржен) — `needs-update-first`, клон не пушится. Тест — задача 3.

---

### Task 1: Модуль `labels` и job-less модули в реестре

**Files:**
- Create: `modules/labels/template/{{ _copier_conf.answers_file }}.jinja` (копия core), `modules/labels/template/.ci/modules/labels.yml.jinja` (`schema_version: 1`), `modules/labels/template/.github/workflows/labels.yml.jinja` (`git mv` из `template/.github/workflows/labels.yml.jinja`), `tools/ci_kit/schemas/labels.v1.json` (`schema_version` const 1, `additionalProperties: false`)
- Modify: `copier.yml` (`module.choices` += `labels`; `toolkit_version.when` → `module in ['legacy', 'core', 'labels']`), `tools/ci_kit/modules.py`, `tools/ci_kit/gate.py:82-85`, `template/docs/ci-kit.md.jinja` (убрать строку `labels.yml`), `scripts/validate.py` (проверка `labels-sync.yml@` уходит из legacy-цикла), `docs/modules.md` (строка `labels` в «Available modules»: декларация `.ci/modules/labels.yml`, вопроса нет)
- Test: `tests/test_labels_workflow.py`, `tests/test_ci_kit_gate.py`, `tests/test_ci_kit_plan.py`

**Interfaces:**
- Produces: `ModuleInfo(requires, job: str | None = None, language=False, fields=None)`; `MODULES["labels"] = ModuleInfo(("core",), None)`; `evaluate` пропускает модули с `job is None` (нет строки, не влияет на `ok`).

- [ ] **Step 1:** Тесты.
  - `test_labels_workflow.py`: рендер легаси-сценариев больше не содержит `.github/workflows/labels.yml`; `rendered_repo("core", "labels")` (или `render(...)` с `module=labels`) даёт ровно `{.copier-answers.labels.yml, .ci/modules/labels.yml, .github/workflows/labels.yml}`, и workflow содержит `uses: quokkify/ci-kit/.github/workflows/labels-sync.yml@<toolkit_version>`; существующие проверки триггеров/permissions/concurrency переводятся на рендер модуля.
  - `test_ci_kit_gate.py::test_jobless_module_is_not_evaluated`: `evaluate(["core", "labels"], {"plan": success, "core-checks": success}, None, "v1")` → `ok is True`, строк с `labels` нет.
  - `test_ci_kit_plan.py::test_labels_requires_core`: репозиторий с одним `labels` → `CIK-CORE-003`; `core`+`labels` → без находок, `plan.modules == ["core", "labels"]`, `"labels" not in plan.matrices`.
- [ ] **Step 2:** Запустить — FAIL: `uv run python scripts/run_test_suites.py --suite test_labels_workflow.py --suite test_ci_kit_gate.py --suite test_ci_kit_plan.py`.
- [ ] **Step 3:** Реализация по списку Files.
- [ ] **Step 4:** Те же наборы + `test_module_selector.py test_modules_render.py test_modules_lifecycle.py` — PASS; `actionlint` на отрендеренном `labels.yml`.
- [ ] **Step 5:** Commit `feat(modules): move label sync into the labels module`.

### Task 2: `ci-kit add`

**Files:**
- Create: `tools/ci_kit/add.py`, `docs/errors/CIK-ADD-00{1..5}.md`, `tests/test_ci_kit_add.py`
- Modify: `tools/ci_kit/cli.py` (подкоманда `add --module --repo --to [--template-src] [--json]`, exit 1 при находках), `tools/ci_kit/codes.py`, `docs/modules.md` (раздел «Adding a module»)

**Interfaces:**
- Consumes: `MODULES` (задача 1); `build_plan` (`plan.py`); `_copier`, `TRUSTED_SRC_RE` (`drift.py`); `_git`, `commit_all`, `_answers_files` (`update.py`).
- Produces: `add_module(repo: Path, module: str, to: str, *, src_override: str | None = None) -> AddResult`; `AddResult(module: str, ref: str, installed: bool, commits: list[str], findings: list[Finding])`; JSON-отчёт — эти поля (`findings` как `{code, module, message, path}`).

Алгоритм — spec §5. Источник — `_src_path` из `.copier-answers.core.yml`, если не задан `--template-src` (проверка `TRUSTED_SRC_RE` как в `render_module`). Список файлов модуля для `CIK-ADD-004` — рендер во временный каталог тем же `copy` (без answers-файла); откат: `git reset --hard <head>` + `git clean -fdx` только по путям, которых не было до запуска.

- [ ] **Step 1:** Тесты (фикстура: `lifecycle_template_repo()`/`rendered_from(template, "core", ref="v99.0.0")` из `tests/module_test_support.py`):
  - `test_adds_module_in_one_commit`: `installed is True`, одна новая запись в `git log` с темой `chore(ci-kit): add labels module`, есть `.copier-answers.labels.yml` с `_commit: v99.0.0`, `build_plan` без находок.
  - `test_already_installed_is_noop`: второй запуск → `installed is False`, `findings == []`, HEAD тот же.
  - `test_dirty_tree_001`, `test_unknown_or_legacy_module_002` (`"nope"`, `"legacy"`), `test_missing_core_003` (legacy-only репозиторий), `test_core_commit_mismatch_003` (`--to v99.1.0`), `test_existing_file_004` (свой `labels.yml` закоммичен) — в каждом HEAD и `git status --porcelain` не изменились.
  - `test_plan_findings_roll_back_005`: `build_plan` подменён на находку → `CIK-ADD-005`, HEAD исходный, неотслеживаемых файлов нет.
  - `test_copier_failure_rolls_back`: подменённый `_copier` создаёт файл и завершается с кодом 1 → находка `CIK-ADD-005`, дерево чистое.
  - `test_cli_json`: `ci-kit add ... --json out.json` → поля `module, ref, installed, commits, findings`; exit 0; с находкой — exit 1.
  - `test_codes_documented`: каждый `CIK-ADD-*` есть в `CODES` и `docs/errors/`.
- [ ] **Step 2:** FAIL: `--suite test_ci_kit_add.py --suite test_ci_kit_codes.py`.
- [ ] **Step 3:** Реализация.
- [ ] **Step 4:** PASS; плюс `test_ci_kit_update.py test_ci_kit_plan.py`.
- [ ] **Step 5:** Commit `feat(ci-kit): add command installs a module into an existing repository`.

### Task 3: Fleet `--add-module` и rollout

**Files:**
- Modify: `scripts/update_copier_fleet.py` (флаг `--add-module MODULE`; `run_ci_kit_add(repository_path, module, target_ref, *, env) -> AddResult`-подобный dict по образцу `run_ci_kit_update`; `add_module_repository(repository, *, module, target_ref, dry_run, env, workspace, expected_template) -> Result`; `add_module_title(module) -> str` = `chore(ci-kit): add <m> module`; ветка в `main` вместо `process_repository`, когда флаг задан), `scripts/rollout_copier_fleet.py` (пропускает `--add-module` в стадии без изменений), `docs/usage.md`, `README.md` (строка про `--add-module`)
- Test: `tests/test_update_copier_fleet.py` (`AddModuleTests`), `tests/test_ci_rollout.py`

**Interfaces:**
- Consumes: CLI `ci-kit add --json` (задача 2); `fetch_answers_files`, `clone_repository`, `push_automation_branch`, `create_pull_request`, `gh_json`, `ci_kit_environment`.
- Produces: статусы `up-to-date`, `needs-update-first`, `would-add`, `add-rejected`, `pull-request` в JSON/Markdown-отчёте (новые статусы попадают в счётчики и в Markdown без отдельной секции).

- [ ] **Step 1:** Тесты (`AddModuleTests`, моки как в `PullRequestLabelTests`):
  - `test_installed_module_is_up_to_date` — без клона.
  - `test_core_behind_target_needs_update_first` — `_commit` core `v3.8.0`, target `v3.9.0`; `clone_repository` и `push_automation_branch` не вызваны.
  - `test_dry_run_would_add` — `run_ci_kit_add` вызван, push нет, `gh pr` нет.
  - `test_findings_reject_with_codes` — detail `CIK-ADD-004`.
  - `test_write_opens_labelled_pr_on_add_branch` — push на `ci-kit/add-labels` с сообщением `chore(ci-kit): add labels module`; `gh pr create` с `--label area: template`, `--base main`.
  - `test_existing_pr_is_reused` — `gh pr list` вернул URL → `gh pr create` не вызван, detail = URL.
  - `test_add_module_rejects_auto_merge_and_merge_ready` — `main([...,"--add-module","labels","--write","--auto-merge"])` → exit 2/ошибка парсера.
  - `test_ci_rollout.py::test_rollout_passes_add_module_to_every_stage` — аргументы пилотов и fanout содержат `--add-module labels`; пилот со статусом `up-to-date` принимается.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** Реализация.
- [ ] **Step 4:** PASS; затем полная проверка (Global Constraints). Grep изменённых файлов на `TODO|skip|only|NotImplemented` — пусто.
- [ ] **Step 5:** Commit `feat(fleet): add-module installs a module across the fleet`.

### Task 4: PR #467 и передача раскатки

- [ ] **Step 1:** `git push --force-with-lease` ветки; описание PR #467 переписать под модуль: что рендерит `labels`, `ci-kit add`, `--add-module`, раскатка (renovate-presets#63 → релиз → `--add-module labels` dry-run → пилоты → остальные), test plan с результатами задач 1–3. Убрать упоминание migration PR.
- [ ] **Step 2:** Дождаться CI; единственная ожидаемая ошибка — покрытие пресета до renovate-presets#63.
- [ ] **Step 3:** `cmux agent message surface:1000030046` (вкладка «RFC ci-kit, PR #73»): #467 готов к мёржу, команды раскатки, условия (смёржен #63, релиз после v3.10.0).
