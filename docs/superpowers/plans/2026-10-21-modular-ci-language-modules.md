# Языковые модули и миграция компонентов — план внедрения

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Модули `python`, `node`, `docker` и `ci-kit migrate`, который переводит репозиторий с компонентами из legacy validate в модули одним PR без потери поведения CI.

**Architecture:** Таблица входов reusable workflows (package data) — единственный источник умолчаний; `ci-kit plan` выдаёт полностью заполненные матрицы, reusable validate передаёт их в `with:` без выражений. `migrate` разбирает фактический legacy `validate.yml` в декларации модулей, ставит `core` и языковые модули, затем сверяет эффективные входы до и после.

**Tech Stack:** Python 3 (`tools/ci_kit`, unittest), Copier 9.19.1, GitHub Actions reusable workflows, jsonschema, PyYAML.

**Spec:** `docs/superpowers/specs/2026-10-21-modular-ci-language-modules-design.md`

## Global Constraints

- Два PR и два релиза: PR 1 — Tasks 1–5, PR 2 — Tasks 6–10. Task 11 — внешние шаги, каждый только с подтверждения владельца.
- Ветка PR 1 — `feat/modules-wave-2` (уже содержит spec и этот план); PR 2 — `feat/migrate-components` от `main` после merge PR 1. Merge в ci-kit — только squash; Conventional Commits.
- API `java-ci.yml`, `python-ci.yml`, `node-ci.yml`, `docker-build.yml` не меняется.
- В `with:` reusable validate нет `||`; каждое значение — `${{ matrix.component.<field> }}`.
- Версии (`*_version`) — строки; схемы отклоняют числа.
- Caller остаётся `name: Validate`, путь `.github/workflows/validate.yml`.
- Модульный порядок миграции: `core → java → python → node → docker`.
- Тесты не зависят от `GITHUB_ACTIONS`. Локально: `PROJECT_TOOLKIT_NESTED_VALIDATION=1 python scripts/validate.py --static`, наборы — `python -m unittest tests.<module> -v`. Авторитетный прогон — CI.
- Код без комментариев, кроме пояснения regexp.

## Review Focus

1. Legacy job с явным `run-build: false` или `build-command: ""` — после миграции поведение то же, а не умолчание workflow (Task 2, Task 8).
2. Legacy job с ключом `if:`/`needs:`/`secrets:` или с ref ≠ `toolkit_version` — отказ `CIK-MIGRATE-005`, а не молчаливая потеря (Task 6).
3. Ручной шаг в `template-contract` или ручной триггер — `CIK-MIGRATE-003`; ручная правка только `with:` component-job — переносится (Task 7).
4. Одинаковый `id` компонента в двух модулях — `CIK-CORE-005` до запуска jobs (Task 2).
5. Версия `24` (int) или `3.10` (float) в legacy `with:` — int приводится к `"24"`, float → `CIK-MIGRATE-005` (Task 6).

---

## PR 1 — модули

### Task 1: Таблица входов reusable workflows

**Files:**
- Create: `tools/ci_kit/inputs.py`, `tools/ci_kit/workflow_inputs.json`
- Modify: `pyproject.toml:22-23` (package-data += `workflow_inputs.json`)
- Test: `tests/test_ci_kit_inputs.py`

**Interfaces:**
- Produces:
  - `WORKFLOWS: dict[str, str] = {"java": "java-ci", "python": "python-ci", "node": "node-ci", "docker": "docker-build"}`
  - `extract_inputs(workflow_yml: Path) -> dict[str, dict]` — `{input: {"type": str, "default": object}}` из `on.workflow_call.inputs`; вход без `default` → `default` = `""` для string, `False` для boolean.
  - `load_inputs() -> dict[str, dict[str, dict]]` — JSON-ресурс, ключ — имя workflow (`"python-ci"`).
  - `field_name(input_name: str) -> str` — `"run-build"` → `"run_build"`.
  - `input_name(field: str) -> str` — обратное.

- [ ] **Step 1: Write the failing tests**

```python
def test_table_matches_workflows(self):
    root = Path(__file__).resolve().parents[1] / ".github" / "workflows"
    self.assertEqual(load_inputs(), {name: extract_inputs(root / f"{name}.yml") for name in WORKFLOWS.values()})

def test_python_defaults(self):
    table = load_inputs()["python-ci"]
    self.assertEqual(table["python-version"], {"type": "string", "default": "3.14"})
    self.assertEqual(table["run-build"], {"type": "boolean", "default": False})

def test_names_round_trip(self):
    self.assertEqual(field_name("run-build"), "run_build")
    self.assertEqual(input_name("run_build"), "run-build")
```

- [ ] **Step 2: Run** `python -m unittest tests.test_ci_kit_inputs -v` — FAIL (`ModuleNotFoundError: ci_kit.inputs`).
- [ ] **Step 3: Implement** `inputs.py`; сгенерируй JSON: `python -c "import json,sys; sys.path.insert(0,'tools'); from pathlib import Path; from ci_kit.inputs import WORKFLOWS, extract_inputs; print(json.dumps({n: extract_inputs(Path('.github/workflows')/f'{n}.yml') for n in WORKFLOWS.values()}, indent=2, sort_keys=True))" > tools/ci_kit/workflow_inputs.json`. Добавь эту команду в сообщение `assertEqual` первого теста (`msg=`), чтобы при рассинхроне было видно, как перегенерировать.
- [ ] **Step 4: Run** тесты — PASS.
- [ ] **Step 5: Commit** `feat(ci-kit): add reusable workflow inputs table`

### Task 2: Модули в реестре, схемы и заполнение матриц в `ci-kit plan`

**Files:**
- Modify: `tools/ci_kit/modules.py`, `tools/ci_kit/plan.py:57-130`, `tools/ci_kit/codes.py`, `tools/ci_kit/schemas/java.v1.json`
- Create: `tools/ci_kit/schemas/{python,node,docker}.v1.json`, `docs/errors/CIK-CORE-005.md`
- Test: `tests/test_ci_kit_plan.py`, `tests/test_ci_kit_inputs.py`

**Interfaces:**
- Consumes: Task 1 `load_inputs`, `WORKFLOWS`, `field_name`.
- Produces:
  - `ModuleInfo(requires, job, language=False, workflow: str | None = None, fields: tuple[str, ...] | None = None)`; `fields is None` — все входы workflow, кроме `working-directory`.
  - `MODULES` += `"python": ModuleInfo(("core",), "python", True, "python-ci")`, `"node": ModuleInfo(("core",), "node", True, "node-ci")`, `"docker": ModuleInfo(("core",), "docker", False, "docker-build", ("context", "dockerfile", "target"))`; `java` получает `workflow="java-ci"`.
  - `module_fields(module: str) -> tuple[str, ...]` в `inputs.py` — snake-имена полей модуля.
  - `fill(module: str, component: dict) -> dict` в `inputs.py` — `id`, `name` (docker: по умолчанию `id`), `path` (docker: нет) + каждое поле из `module_fields` со значением из декларации либо `default` таблицы.
  - `Plan.matrices[module]` — список результатов `fill` для модулей с `workflow`.
  - Код `CIK-CORE-005`: «Component id is declared by more than one module».

Схемы: `python`/`node` — `required: [id, name, path]`; `java` — без изменений в `required`; `docker` — `required: [id]`, плюс `name`. Свойства каждой схемы — ровно `id`, `name`, `path` (кроме docker) и `module_fields(module)`; тип каждого поля — `string` или `boolean` по таблице; `*_version` — `string`.

- [ ] **Step 1: Write the failing tests**

```python
def test_schema_properties_match_workflow_inputs(self):
    for module in ("java", "python", "node", "docker"):
        schema = json.loads((SCHEMAS / f"{module}.v1.json").read_text())
        props = set(schema["properties"]["components"]["items"]["properties"])
        base = {"id", "name"} | (set() if module == "docker" else {"path"})
        self.assertEqual(props, base | set(module_fields(module)), module)

def test_plan_fills_defaults_and_keeps_explicit_false_and_empty(self):
    repo = modules_repo({"python": [{"id": "app", "name": "App", "path": ".", "run_build": False, "build_command": ""}]})
    entry = build_plan(repo, drift=False).matrices["python"][0]
    self.assertIs(entry["run_build"], False)
    self.assertEqual(entry["build_command"], "")
    self.assertEqual(entry["python_version"], "3.14")
    self.assertEqual(set(entry), {"id", "name", "path", *module_fields("python")})

def test_duplicate_component_id_across_modules_is_core_005(self):
    repo = modules_repo({"python": [{"id": "app", "name": "A", "path": "."}], "node": [{"id": "app", "name": "B", "path": "."}]})
    self.assertIn("CIK-CORE-005", [f.code for f in build_plan(repo, drift=False).findings])

def test_numeric_version_is_core_004(self):
    repo = modules_repo({"node": [{"id": "web", "name": "Web", "path": ".", "node_version": 24}]})
    self.assertEqual([f.code for f in build_plan(repo, drift=False).findings], ["CIK-CORE-004"])
```

`modules_repo(declarations: dict[str, list[dict]]) -> Path` — новый helper в `tests/module_test_support.py`: временный каталог с `.copier-answers.core.yml`, `.ci/modules/core.yml` и для каждого модуля `.copier-answers.<m>.yml` + `.ci/modules/<m>.yml` (`schema_version: 1`, `components`; для docker — тот же ключ `components`).

- [ ] **Step 2: Run** `python -m unittest tests.test_ci_kit_plan tests.test_ci_kit_inputs -v` — FAIL.
- [ ] **Step 3: Implement** реестр, `module_fields`, `fill`, схемы, проверку уникальности `id` (после загрузки деклараций, finding на второй модуль, `path` — его декларация), код и `docs/errors/CIK-CORE-005.md` в формате соседних файлов.
- [ ] **Step 4: Run** тесты — PASS; плюс `python -m unittest tests.test_ci_kit_plan tests.test_validate_helpers -v` без регрессий.
- [ ] **Step 5: Commit** `feat(ci-kit): add python, node and docker module declarations`

### Task 3: Выходы `ci-kit plan` для всех матриц

**Files:**
- Modify: `tools/ci_kit/cli.py:52`
- Test: `tests/test_ci_kit_plan.py`

**Interfaces:**
- Consumes: Task 2 `Plan.matrices`, `MODULES`.
- Produces: выходы `modules`, `java`, `python`, `node`, `docker` (компактный JSON, `[]` для выключенного), `codes`, `commits`.

- [ ] **Step 1: Write the failing test** `test_plan_outputs_every_matrix`: `main(["plan", "--repo", str(modules_repo({"python": [...]})), "--github-output", out])`; в файле есть `python=[{...}]`, `node=[]`, `docker=[]`, `java=[]`.
- [ ] **Step 2: Run** — FAIL (`node=` отсутствует).
- [ ] **Step 3: Implement**: ключи — модули из `MODULES` с `workflow`, в порядке `java, python, node, docker`.
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(ci-kit): expose language and docker matrices from plan`

### Task 4: Copier-шаблоны модулей

**Files:**
- Create: `modules/{python,node,docker}/template/.ci/modules/<m>.yml.jinja`, `modules/{python,node,docker}/template/{{ _copier_conf.answers_file }}.jinja` (копия из `modules/java/template`)
- Modify: `copier.yml:40-105` (`module.choices` += `python, node, docker`; вопросы `python_components`, `node_components`, `docker_images`), `modules/java/template/.ci/modules/java.yml.jinja`
- Create: `tests/scenarios/modules/{core-python,core-node-docker,core-polyglot}.yml`
- Modify: `docs/modules.md` (раздел о модулях: поля, пример декларации)
- Test: `tests/test_modules_render.py`

**Interfaces:**
- Consumes: Task 2 схемы.
- Produces: декларация модуля рендерится как `schema_version: 1` + `components:` со строкой `  - {{ component | to_json(ensure_ascii=False) }}` на каждый элемент; `java` рендерит четыре поля как сейчас и затем остальные заданные ключи в том же JSON-виде (существующий `core-java` остаётся байт-в-байт).

Валидаторы новых вопросов — по образцу `java_components` (`copier.yml:85-105`): непустой список mapping, `id` regexp, уникальность внутри модуля, непустые `name`/`path` (docker: только `id`), path без `..` и glob, каждая версия — строка; без списка зарезервированных id.

- [ ] **Step 1: Write the failing tests** `test_python_renders_exact_file_set`, `test_node_docker_renders_exact_file_set`, `test_polyglot_declarations_match_schemas` (каждая декларация проходит свою схему и `build_plan(...).findings == []`), `test_core_java_unchanged` (байт-в-байт с текущим рендером сценария `core-java`), `test_python_component_rejects_numeric_version`.
- [ ] **Step 2: Run** `python -m unittest tests.test_modules_render -v` — FAIL.
- [ ] **Step 3: Implement** шаблоны, вопросы, сценарии, документацию.
- [ ] **Step 4: Run** `tests.test_modules_render`, `tests.test_module_selector`, `tests.test_modules_lifecycle` — PASS.
- [ ] **Step 5: Commit** `feat(template): add python, node and docker Copier modules`

### Task 5: Jobs в reusable validate

**Files:**
- Modify: `.github/workflows/validate.yml:11-17,78-92`
- Test: `tests/test_modular_validate_workflow.py`, `tests/test_ci_kit_gate.py` (или файл, где сейчас тесты `gate.py`; найти `grep -l "_matches\|evaluate(" tests/`)

**Interfaces:**
- Consumes: Task 3 выходы; Task 1 таблица.
- Produces: jobs `python`, `node`, `docker`; `java` переведён на полную передачу входов.

Форма каждого job (пример `python`):

```yaml
  python:
    name: python (${{ matrix.component.name }})
    needs: plan
    if: needs.plan.outputs.python != '[]'
    strategy:
      fail-fast: false
      matrix:
        component: ${{ fromJSON(needs.plan.outputs.python) }}
    uses: ./.github/workflows/python-ci.yml
    with:
      working-directory: ${{ matrix.component.path }}
      python-version: ${{ matrix.component.python_version }}
      # … по одной строке на каждый вход из module_fields("python")
```

Docker: `uses: ./.github/workflows/docker-build.yml`, `with:` — `context`, `dockerfile`, `target` из матрицы и `push: false`. `gate.needs: [plan, core-checks, java, python, node, docker]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_language_jobs_pass_every_field_without_fallbacks(self):
    for job_id, module in (("java", "java"), ("python", "python"), ("node", "node")):
        job = WORKFLOW["jobs"][job_id]
        expected = {"working-directory": "${{ matrix.component.path }}"} | {input_name(f): f"${{{{ matrix.component.{f} }}}}" for f in module_fields(module)}
        self.assertEqual(job["with"], expected, job_id)
        self.assertEqual(job["name"], f"{job_id} (${{{{ matrix.component.name }}}})")

def test_docker_job_shape(self):
    job = WORKFLOW["jobs"]["docker"]
    self.assertEqual(job["with"], {"context": "${{ matrix.component.context }}", "dockerfile": "${{ matrix.component.dockerfile }}", "target": "${{ matrix.component.target }}", "push": False})

def test_no_or_fallback_in_with(self):
    for job in WORKFLOW["jobs"].values():
        for value in (job.get("with") or {}).values():
            self.assertNotIn("||", str(value))

def test_gate_matches_language_job_name(self):
    jobs = [{"name": "ci / python (App Python) / ci", "conclusion": "failure", "steps": [{"name": "Run tests", "conclusion": "failure"}]}]
    self.assertEqual(_failing_step(jobs, "python"), "Run tests")
```

Существующий `test_gate_needs_cover_every_other_job` должен остаться зелёным без правки.

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** jobs и `needs`.
- [ ] **Step 4: Run** `tests.test_modular_validate_workflow` и тесты gate — PASS; `actionlint .github/workflows/validate.yml` — без ошибок (если установлен; иначе это проверит CI).
- [ ] **Step 5: Commit** `feat(ci): run python, node and docker modules in reusable validate`

### PR 1: проверка и публикация

- [ ] `PROJECT_TOOLKIT_NESTED_VALIDATION=1 python scripts/validate.py --static` — PASS.
- [ ] Ревью всей ветки отдельным reviewer (superpowers:requesting-code-review).
- [ ] Push `feat/modules-wave-2`, PR `feat(modules): add python, node and docker modules` со ссылкой на #393 и spec; дождаться зелёного CI; squash-merge — после подтверждения владельца. Релиз — через release PR как обычно.

---

## PR 2 — миграция

### Task 6: Разбор legacy jobs и точные имена checks

**Files:**
- Create: `tools/ci_kit/legacy_jobs.py`
- Modify: `tools/ci_kit/migrate.py:77-88` (`legacy_check_names` делегирует в `legacy_jobs`)
- Test: `tests/test_ci_kit_legacy_jobs.py`, `tests/test_ci_kit_migrate.py` (`CheckNamesTests`), `tests/test_migration_checks.py`

**Interfaces:**
- Consumes: Task 1 `WORKFLOWS`, `field_name`, `load_inputs`; Task 2 `module_fields`.
- Produces:
  - `@dataclass(frozen=True) class LegacyJob: job_id: str; name: str; module: str; with_: dict[str, object]`
  - `class MappingError(ValueError)` с атрибутом `job_id: str`.
  - `parse_jobs(data: dict, toolkit_version: str) -> list[LegacyJob]` — все jobs, кроме `template-contract`; правила и отказы — spec §7.1.
  - `legacy_to_modules(jobs: list[LegacyJob]) -> list[tuple[str, dict]]` — `[("core", {}), (module, {"<question>": [component, ...]}), ...]` в порядке `core, java, python, node, docker`; вопрос — `java_components`, `python_components`, `node_components`, `docker_images`; компонент — `id`, `name`, `path` (не для docker) и `field_name` каждого входа `with_` (`working-directory` → `path`); java без `java-version` → `java_version: "17"`; int-версия → строка.
  - `check_contexts(data: dict) -> list[str]` — `["Shared template contract"]` для `template-contract` и `f"{name} / {inner}"` для каждого `LegacyJob`, где `inner` = `"build"` для docker, иначе `"ci"`; `name` = `name` job или его id.
  - `legacy_check_names(validate_yml: Path) -> list[str]` = `check_contexts(yaml.safe_load(...))`; `MappingError` пробрасывается.

`ref` в `uses:` сравнивается с `toolkit_version` из legacy answers; workflow определяется по `uses: quokkify/ci-kit/.github/workflows/<WORKFLOWS[m]>.yml@<ref>`. Допустимые ключи job: `name`, `uses`, `with`. Docker допускает в `with:` только `context`, `dockerfile`, `target` и `push: false`.

- [ ] **Step 1: Write the failing tests** на фикстурах-словарях (по образцу реальных `validate.yml`, spec §1):
  - `test_skills_shape` → `[("core", {}), ("python", {"python_components": [{"id": "app-python", "name": "App Python", "path": ".", "install_command": "python -m pip install pytest allure-pytest", "test_command": "python -m pytest --alluredir=allure-results tests/", "upload_test_artifacts": True, "test_artifact_path": "allure-results", "test_artifact_name": "allure-results-app-python"}]})]`
  - `test_marketdesk_shape`: `node_version` `24` → `"24"`; `java-test-automation` → `java` с `run_build: False`, `test_artifact_name: "allure-results-marketdesk-java"`; docker с id `docker` → `docker_images: [{"id": "docker", "name": "docker"}]`.
  - `test_java_without_version_gets_17`.
  - `test_refusals` (каждый → `MappingError` с `job_id`): ключ `if`, ключ `needs`, ключ `secrets`, ref `v0.0.1` при `toolkit_version` `v3.3.0`, неизвестный workflow, вход `bogus`, `python-version: 3.10` (float), job без `uses` (`complete`), docker с `push: true`.
  - `test_check_contexts`: skills → `["Shared template contract", "App Python / ci"]`; marketdesk без `complete` → `[..., "MarketDesk / ci", "Java test automation / ci", "docker / build"]`.
  - В `tests/test_migration_checks.py`: legacy `validate.yml` с компонентом даёт `before` с `"<name> / ci"`, а чужой контекст ruleset (`"Lint / docker"`) не удаляется.
  - Обнови `CheckNamesTests.test_reusable_workflow_jobs_are_refused`: теперь отказ только для job без сопоставления.
- [ ] **Step 2: Run** `python -m unittest tests.test_ci_kit_legacy_jobs tests.test_ci_kit_migrate tests.test_migration_checks -v` — FAIL.
- [ ] **Step 3: Implement** `legacy_jobs.py`; `migrate.legacy_check_names` — тонкая обёртка.
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(ci-kit): map legacy validate jobs to module declarations`

### Task 7: Суженная проверка контракта `validate.yml` (L7)

**Files:**
- Modify: `tools/ci_kit/migrate.py:114-132` (`_contract_findings`)
- Test: `tests/test_ci_kit_migrate.py`

**Interfaces:**
- Consumes: Task 6 `parse_jobs`.
- Produces: для `.github/workflows/validate.yml` сравниваются `yaml.safe_load` рендера и файла после удаления из обоих `jobs` всех id, которые `parse_jobs` распознал как component-jobs (в файле) и всех `uses:`-jobs (в рендере). Прочие файлы области — как сейчас (побайтно после `_normalize`). Сообщение 003 сохраняет unified diff.

- [ ] **Step 1: Write the failing tests**
  - `test_hand_edited_component_with_is_not_003`: legacy-сценарий `polyglot`, в `validate.yml` правится `test-command` компонента → `_contract_findings(...) == []`.
  - `test_extra_contract_step_is_003`: добавлен шаг в `template-contract` → один finding `CIK-MIGRATE-003`, в сообщении есть имя шага.
  - `test_trigger_edit_is_003`: изменён `on.push.branches` → 003.
  - Существующий `test_hand_edited_validate_is_003_with_diff` остаётся зелёным.
- [ ] **Step 2: Run** — FAIL на первом.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tests.test_ci_kit_migrate` — PASS.
- [ ] **Step 5: Commit** `feat(ci-kit): compare only non-component validate jobs during migration`

### Task 8: Миграция нескольких модулей и проверка эквивалентности

**Files:**
- Modify: `tools/ci_kit/migrate.py:18-48,77-100,140-219`, `tools/ci_kit/codes.py` (текст `CIK-MIGRATE-005`: «Migration refused: a legacy validate job or input cannot be mapped to a module»), `docs/errors/CIK-MIGRATE-003.md`, `docs/errors/CIK-MIGRATE-005.md`
- Create: `tools/ci_kit/equivalence.py`
- Test: `tests/test_ci_kit_migrate.py`, `tests/test_ci_kit_equivalence.py`

**Interfaces:**
- Consumes: Task 6 `parse_jobs`, `legacy_to_modules`, `check_contexts`; Task 2 `build_plan`, `fill`; Task 1 `load_inputs`.
- Produces:
  - `MigrateResult.modules: list[str]`; `PAYLOAD_KEYS` += `"modules"`; marker `module` остаётся `"core"`.
  - `copier_copy_command(module: str, to: str, src: str, data: dict[str, str] | None = None) -> list[str]` — каждый элемент `data` → `-d key=value` (значение — компактный JSON); опционоподобные значения — `ValueError`, как сейчас.
  - `effective(module: str, values: dict) -> dict[str, object]` в `equivalence.py` — `default` таблицы для каждого входа workflow модуля (кроме `working-directory`), перекрытые `values` (ключи — имена входов в kebab-case).
  - `equivalence_findings(jobs: list[LegacyJob], plan: Plan) -> list[Finding]` — 005, если набор `(module, id)` не совпадает или `effective(module, job.with_) != effective(module, {input_name(k): v for k, v in entry.items() if k not in ("id", "name", "path")} | {"working-directory": entry["path"]})`; для docker `push` добавляется как `False` с обеих сторон.

Поток `migrate()` (spec §7.2): убрать ранний отказ «language components»; `parse_jobs` → при `MappingError` — 005 с `path` `validate.yml` и `job_id` в сообщении; `checks_before = check_contexts(...)`; шаг 1 без изменений; затем `copier copy` для каждого `(module, data)` из `legacy_to_modules` с отдельным commit `chore(ci-kit): add <module> module`; затем `equivalence_findings`; при findings — `_rollback` и возврат findings. `result.modules` — модули в порядке установки.

- [ ] **Step 1: Write the failing tests**
  - `test_effective_overlays_defaults` и `test_false_is_not_replaced_by_default` в `test_ci_kit_equivalence.py`: entry с `run_build: True` против legacy `run-build: False` → 005.
  - `test_polyglot_migrates_with_one_commit_per_module`: legacy-сценарий `polyglot` → `result.modules == ["core", "java", "python", "node"]`, `subjects(repo)[:5]` — hand-off + четыре `add <module> module`, `result.findings == []`, `result.checks_before` начинается с `"Shared template contract"`.
  - `test_docker_scenario_migrates`: сценарий `docker` → `"docker"` в `result.modules`, `"docker / build"` в `checks_before`.
  - `test_unmappable_job_is_005_and_tree_untouched`: добавлен job `complete` без `uses` → 005, HEAD не изменился.
  - Обновить `test_language_components_are_005` → удалить; `test_round_trip_has_exact_keys` — с `modules`.
- [ ] **Step 2: Run** `python -m unittest tests.test_ci_kit_migrate tests.test_ci_kit_equivalence -v` — FAIL.
- [ ] **Step 3: Implement**; документация ошибок — новые смыслы 003 (L7) и 005 (L6), исправление 005 — подготовительный PR (spec §9).
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(ci-kit): migrate repositories with language components`

### Task 9: Fleet

**Files:**
- Modify: `scripts/update_copier_fleet.py:1965-2125` (`run_ci_kit_migrate` читает `modules`; `migration_pr_body`; `migrate_repository`)
- Test: `tests/test_update_copier_fleet.py:2875-2895,3140`

**Interfaces:**
- Consumes: Task 8 payload с `modules`.
- Produces: `migrate_repository` больше не возвращает `not-applicable` для `components` и для одиночного 005 — 005 становится `migrate-rejected` с кодом; тело PR содержит строку `Modules: core, python, …` и таблицу `| legacy job | module | component |`.

- [ ] **Step 1: Write the failing tests**: репозиторий с `components` доходит до `run_ci_kit_migrate` (dry-run → `would-migrate`); одиночный 005 → `("migrate-rejected", "CIK-MIGRATE-005")`; тело PR содержит `Modules: core, python`. Удалить/обновить проверки `"language components"` (строки 2881, 3140).
- [ ] **Step 2: Run** `python -m unittest tests.test_update_copier_fleet -v` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(fleet): migrate repositories with language components`

### Task 10: Lifecycle и runbook

**Files:**
- Modify: `tests/test_migration_lifecycle.py`, `tests/module_test_support.py:165-197` (`component_less_scenarios` → добавить `component_scenarios()` для `polyglot`, `docker`, `java`, `node`), `docs/modules.md:17-90` (убрать блокер «language components»; описать подготовительный PR, 003/005, порядок модулей)
- Test: `tests/test_migration_lifecycle.py`

**Interfaces:**
- Consumes: Task 8 `migrate`, Task 2 `build_plan`.

- [ ] **Step 1: Write the failing tests** для каждого сценария из `component_scenarios()`: legacy с ручной правкой `with:` (`test-command`) → `migrate` → `findings == []` → `ci-kit update` на тот же ref без diff → `git revert` всех migration-commits возвращает исходное дерево; имена артефактов из матриц с `upload_test_artifacts` начинаются с `allure_external_artifact_prefix` (`allure-results-`).
- [ ] **Step 2: Run** `python -m unittest tests.test_migration_lifecycle -v` — FAIL.
- [ ] **Step 3: Implement** helper и документацию.
- [ ] **Step 4: Run** — PASS; затем `PROJECT_TOOLKIT_NESTED_VALIDATION=1 python scripts/validate.py --static` — PASS.
- [ ] **Step 5: Commit** `docs(modules): document component migration`

### PR 2: проверка и публикация

- [ ] Ревью всей ветки отдельным reviewer; security-ревью не требуется (нет секретов и прав), но `migration_checks.py` меняет rulesets — reviewer проверяет, что чужие контексты не трогаются.
- [ ] Push `feat/migrate-components`, PR `feat(migration): migrate repositories with language components`; зелёный CI; squash-merge после подтверждения владельца; релиз.

---

## Task 11: Раскатка (внешние шаги, каждый — с подтверждения владельца)

Не код; выполняется по `docs/modules.md` после релиза PR 2 (knowledge — допустимо после текущего релиза).

- [ ] 2a knowledge: `python scripts/update_copier_fleet.py --org quokkify --repo quokkify/knowledge --dry-run --migrate core --markdown-report <scratch>` — ожидаемо `would-migrate`; затем migrate, merge.
- [ ] 2a skills: dry-run → migrate PR → зелёный `ci / gate` (первое runtime-исполнение `python`) → `scripts/migration_checks.py --pr quokkify/skills#<n> --dry-run` (`before` содержит `App Python / ci`) → без `--dry-run` → merge → Allure Pages обновились.
- [ ] Пауза: следующий релиз ci-kit проходит fleet `update` по skills/knowledge без ручных правок.
- [ ] 2b marketdesk: подготовительный PR (шаг changelog → свой workflow со своим required check; удалить `complete`; required checks → `Shared template contract`, `MarketDesk / ci`, `Java test automation / ci`, `docker / build`) → migrate как выше → Allure external работает.
- [ ] Пауза, как выше.
- [ ] 2c q4j: решение владельца — migrate с заглушками или удалить их из answers; подготовительный PR (шаги release/publication и Gradle retry → свой workflow со своим required check; inline-скрипт — к рендеру); migrate.
- [ ] Итоги критериев spec §11 — комментарием в #393.
