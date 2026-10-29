# Java: одна test-job на модуль сборки — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** java-компонент с `test_modules` получает job `checks` (lint+build) и параллельную матрицу `test (<id>)`, по одному Allure-артефакту на модуль, без изменений для существующих потребителей.

**Architecture:** `ci-kit plan` валидирует `test_modules` по `java.v1.json` и кодирует его в JSON-строку, где для каждого модуля уже посчитано имя артефакта. `validate.yml` передаёт эту строку в новый input `test-modules` workflow `java-ci.yml`. Если строка пустая, работает прежняя job `ci`; если нет — `checks` и матрица `test`. Gate и компонентный режим `allure` не меняются.

**Tech Stack:** GitHub Actions reusable workflows, Python 3.12+ (`tools/ci_kit`), jsonschema draft 2020-12, Copier, unittest.

**Spec:** `docs/superpowers/specs/2026-10-29-java-test-modules-design.md`

## Global Constraints

- Схема остаётся `schema_version: 1`, изменение только аддитивное.
- Имя артефакта: `allure-results-<component id>-<module id>`, не длиннее 128 символов; лимит Allure-артефактов в плане — 50.
- Новый код ошибки: `CIK-CORE-013` (лимиты). Дубли и коллизии идут под `CIK-CORE-005`.
- Значения из декларации попадают в `run:` только через `env`, никогда не интерполируются.
- Шаги job `ci` в `java-ci.yml` не меняются. Это регрессионная граница.
- Имена setup-шагов в `checks` и `test` совпадают с именами в `ci` (`gate` классифицирует падения по `TOOLKIT_STEPS`).
- Новые сторонние actions не добавляются; используются только SHA, уже закреплённые в файле.
- Локальный запуск: venv с `pip install -e .` (pins из `pyproject.toml`), тесты с `GIT_CONFIG_GLOBAL=/dev/null`, например `GIT_CONFIG_GLOBAL=/dev/null python -m unittest discover -s tests -p 'test_ci_kit_plan.py' -v`.
- Перед PR: `python scripts/validate.py --static`, `actionlint .github/workflows/*.yml examples/*.yml`, `git diff --check`.

## Review Focus

1. Модуль упал на компиляции, и results нет → upload только предупреждает, job красная из-за шага теста, `gate` — `project`. Тест в задаче 3.
2. `results` модуля содержит `environment.properties` → job падает в шаге `Check module Allure results` с сообщением про `allure_categories_file`. Тест в задаче 2.
3. `module` или `test_command` с кавычками и `$(...)` → значение не исполняется как часть скрипта (только `env`). Тест в задаче 2.
4. Компонент без `test_modules` после обновления → `test-modules` пустой, выполняется только `ci`, а `checks`/`test` пропущены. Тест в задаче 2.
5. Обычный `test_artifact_name` другого компонента совпадает с модульным артефактом → CIK-CORE-005 в плане, а не молчаливая перезапись артефакта. Тест в задаче 1.

---

### Task 1: Контракт декларации и планировщик

**Files:**
- Create: `tools/ci_kit/test_modules.py`
- Modify: `tools/ci_kit/schemas/java.v1.json`, `tools/ci_kit/plan.py`, `tools/ci_kit/inputs.py` (`fill`), `tools/ci_kit/codes.py`, `tools/ci_kit/workflow_inputs.json` (регенерация), `.github/workflows/java-ci.yml` (только объявление input), `.github/workflows/validate.yml` (одна строка в `with`), `copier.yml` (`allowed_keys` и проверка для `java_components`)
- Create: `docs/errors/CIK-CORE-013.md`, `tests/scenarios/modules/core-java-modules.yml`
- Test: `tests/test_ci_kit_plan.py`, `tests/test_modules_render.py`

**Interfaces:**
- Produces (`tools/ci_kit/test_modules.py`):
  - `ARTIFACT_PREFIX = "allure-results-"`, `MAX_ARTIFACT_NAME = 128`, `MAX_ALLURE_ARTIFACTS = 50`
  - `artifact_name(component_id: str, module_id: str) -> str`
  - `encode_test_modules(component: dict) -> str`: `""`, если `test_modules` нет. Иначе компактный JSON (`separators=(",", ":")`) — список `{"id", "module", "test_command", "results", "artifact"}`, где отсутствующие `test_command`/`results` становятся `""`, а `artifact` — `artifact_name(...)` при непустом `results`, иначе `""`.
  - `component_artifacts(component: dict) -> list[str]`: имена артефактов, которые загрузит компонент (модульные или `test_artifact_name` при `upload_test_artifacts: true`).
- Produces (контракт с задачей 2): input `test-modules` у `java-ci.yml` (`type: string`, `default: ""`) получает строку `encode_test_modules`; поля элемента — `id`, `module`, `test_command`, `results`, `artifact`.

- [ ] **Step 1: Write the failing tests** в `tests/test_ci_kit_plan.py`, на `modules_repo`/`codes` как в соседних тестах; базовый компонент `{"id": "gw", "name": "GW", "path": ".", "java_version": "21"}`:
  - `test_java_test_modules_are_encoded_with_artifact_names`: модули `[{"id": "core", "module": ":core", "results": "core/build/allure-results"}, {"id": "it", "module": ":it", "test_command": "./gradlew :it:check"}]` → `json.loads(entry["test_modules"]) == [{"id": "core", "module": ":core", "test_command": "", "results": "core/build/allure-results", "artifact": "allure-results-gw-core"}, {"id": "it", "module": ":it", "test_command": "./gradlew :it:check", "results": "", "artifact": ""}]`, без findings.
  - `test_java_without_test_modules_encodes_empty_string`: `entry["test_modules"] == ""`.
  - `test_test_modules_forbid_component_test_fields`: для каждого из `test_command`, `upload_test_artifacts`, `test_artifact_name`, `test_artifact_path` рядом с `test_modules` → `["CIK-CORE-004"]`.
  - `test_test_modules_schema_rejects_bad_items`: `[]`, `{"id": "1x", "module": ":a"}`, `{"id": "a"}`, `{"id": "a", "module": "a b"}`, `{"id": "a", "module": ":a", "results": "../x"}`, лишний ключ → `["CIK-CORE-004"]`.
  - `test_duplicate_test_module_id_is_core_005`: два модуля `id: core` → `CIK-CORE-005`, в сообщении `test module id core`.
  - `test_module_artifact_colliding_with_other_component_is_core_005`: java `gw` с модулем `core` + `results`, python-компонент с `upload_test_artifacts: true`, `test_artifact_name: allure-results-gw-core` → `CIK-CORE-005`.
  - `test_overlong_module_artifact_is_core_013`: id компонента и модуля по 64 символа → `CIK-CORE-013`.
  - `test_more_than_50_allure_artifacts_is_core_013_only_with_allure`: 51 модуль с `results`; без модуля `allure` в репо findings нет, с ним — `CIK-CORE-013`. Модуль `allure` включается так же, как это делает `modules_repo`/`rendered_repo` в `tests/module_test_support.py`.
- [ ] **Step 2: Run** `GIT_CONFIG_GLOBAL=/dev/null python -m unittest discover -s tests -p 'test_ci_kit_plan.py' -v`. Ожидается: новые тесты FAIL (`KeyError: 'test_modules'` или schema violation).
- [ ] **Step 3: Implement.**
  - `java.v1.json`: свойство `test_modules` — массив, `minItems: 1`, items с `additionalProperties: false`, `required: [id, module]`. `id` — паттерн id компонента; `module` — `^\S+$`; `test_command` — `minLength: 1`; `results` — паттерн `path`. На уровне компонента добавить `dependentSchemas.test_modules = {"not": {"anyOf": [{"required": ["test_command"]}, {"required": ["upload_test_artifacts"]}, {"required": ["test_artifact_name"]}, {"required": ["test_artifact_path"]}]}}`.
  - `java-ci.yml`: input `test-modules` (`type: string`, `default: ""`, description «JSON list of build modules from ci-kit plan; empty runs the single ci job.») ставится после `test-command`.
  - Перегенерировать `workflow_inputs.json` командой из `tests/test_ci_kit_inputs.py`.
  - `validate.yml`: в `with` java-job добавить `test-modules: ${{ matrix.component.test_modules }}` (алфавитный порядок, после `test-command`).
  - `fill()`: для `module == "java"` записать `entry["test_modules"] = encode_test_modules(component)`.
  - `plan.py`: `_test_module_findings(loaded: dict[str, dict]) -> list[Finding]`, вызывается после цикла CIK-CORE-005. Проверяет дубли `id` в компоненте (CIK-CORE-005, путь `.ci/modules/java.yml`), повторы среди `component_artifacts` всех языковых компонентов (CIK-CORE-005), длину > 128 (CIK-CORE-013), а при `"allure" in loaded` — число имён с `ARTIFACT_PREFIX` > 50 (CIK-CORE-013).
  - `codes.py`: `"CIK-CORE-013": "Build-module test artifacts exceed a report limit"`.
  - `docs/errors/CIK-CORE-013.md` с разделами `## Cause`, `## Fix`, `## Example` (лимиты 128 и 50; что делать — укоротить id или объединить модули).
  - `copier.yml`: добавить `test_modules` в `allowed_keys` у `java_components`. Если ключ задан, а значение — не непустой список mappings, валидатор пишет: `test_modules must be a non-empty YAML list of mappings.`
- [ ] **Step 4: Run** тот же набор плюс `-p 'test_ci_kit_inputs.py'` и `-p 'test_modular_validate_workflow.py'`. Ожидается: PASS, включая существующие `test_schema_properties_match_workflow_inputs` и `test_language_jobs_pass_every_field_without_fallbacks`.
- [ ] **Step 5: Render test.** Сценарий `tests/scenarios/modules/core-java-modules.yml` строится как `core-java.yml` плюс `test_modules` с двумя модулями (один с `test_command`). В `tests/test_modules_render.py` тест `test_java_test_modules_render_and_plan_cleanly`: рендер сценария → `build_plan(repo, drift=False).findings == []`, `json.loads(...matrices["java"][0]["test_modules"])` содержит оба `id`. Run `-p 'test_modules_render.py'` → PASS (включая `test_validator_allowed_keys_match_schemas`).
- [ ] **Step 6: Commit** `feat(java): declare build modules for per-module test jobs (#530)`.

### Task 2: Jobs `checks` и `test` в `java-ci.yml`

**Files:**
- Modify: `.github/workflows/java-ci.yml`
- Test: `tests/test_composite_actions.py` (новый класс `JavaTestModulesWorkflowTests` рядом с `GradleConsoleModeTests`)

**Interfaces:**
- Consumes: input `test-modules` и поля элемента из задачи 1.
- Produces (для задачи 3): имена шагов модульной job — `Gradle module test with bounded recovery`, `Maven module dependencies`, `Maven module test`, `Check module Allure results`, `Upload test artifacts`; имя job — `test (${{ matrix.module.id }})`.

Устройство workflow:

- **`ci`:** добавить только `if: ${{ inputs.test-modules == '' }}`.
- **`checks`:** `if: ${{ inputs.test-modules != '' }}`. Шаги — копия `ci` без двух test-шагов и без upload. В шаге `commands` добавить env `SPLIT_TESTS: "true"`.
- **`test`:** `if: ${{ inputs.test-modules != '' && inputs.run-tests }}`, `name: test (${{ matrix.module.id }})`, `strategy: {fail-fast: false, matrix: {module: ${{ fromJSON(inputs.test-modules) }}}}`, те же `runs-on`, `timeout-minutes`, `defaults`. Шаги:
  1. setup-шаги `ci` с теми же именами (checkout ×2, `Resolve build tool for dependency cache`, `Set up Java`, `Resolve build tool and commands` с env `SPLIT_TESTS: "true"`, `MODULE: ${{ matrix.module.module }}`, `MODULE_TEST_COMMAND: ${{ matrix.module.test_command }}`, `Project setup`);
  2. job-level `env: MODULE_PATH: ${{ matrix.module.module }}`;
  3. `Gradle module test with bounded recovery` через `./.toolkit/actions/gradle-retry` с `steps.commands.outputs.test`;
  4. `Maven module dependencies` (`if:` maven и непустой `steps.commands.outputs.prepare`), затем `Maven module test`;
  5. `Check module Allure results` — `if: ${{ always() && matrix.module.results != '' }}`, env `RESULTS`;
  6. `Upload test artifacts` — `if: ${{ always() && matrix.module.artifact != '' }}`, `name: ${{ matrix.module.artifact }}`, `path: ${{ format('{0}/{1}', inputs.working-directory, matrix.module.results) }}`, `if-no-files-found: warn`.

Скрипт шага `commands` один и тот же текст во всех трёх jobs. Он расширяется так:

- при `SPLIT_TESTS == true` и пустом `BUILD_COMMAND` Gradle-build по умолчанию — `$runner build -x test --console=$GRADLE_CONSOLE`;
- при непустом `MODULE` `test` = `MODULE_TEST_COMMAND`, если он задан. Иначе Gradle: `$runner $MODULE:test --console=$GRADLE_CONSOLE`; Maven: `prepare` = `$runner --batch-mode --no-transfer-progress -pl $MODULE -am install -DskipTests`, `test` = `$runner --batch-mode --no-transfer-progress -pl $MODULE test`. `MODULE` подставляется в команду через `printf %q`, как `--console`;
- дополнительно выводится `prepare=` (пустой, если не нужен).

В `ci` env `SPLIT_TESTS`/`MODULE` не заданы, поэтому выводы для `ci` совпадают с текущими.

Сообщение preflight, когда в `$RESULTS` есть любой из `environment.properties`, `executor.json`, `categories.json`, с кодом 1:
`<file> in <RESULTS> collides between build modules in one Allure report; remove it from module results and set categories through the allure module's allure_categories_file.`

- [ ] **Step 1: Write the failing tests** (`JavaTestModulesWorkflowTests`):
  - `test_ci_job_keeps_its_steps`: `if` у `ci` равен `${{ inputs.test-modules == '' }}`, а список имён шагов `ci` равен литералу из текущего файла (15 шагов от `Check out repository` до `Upload test artifacts`, скопировать до правки). Выводы `commands` для `ci` фиксируют resolver-тесты ниже (вариант «без `SPLIT_TESTS`»).
  - `test_split_jobs_run_only_with_modules`: `if` у `checks` — `${{ inputs.test-modules != '' }}`, у `test` — `${{ inputs.test-modules != '' && inputs.run-tests }}`; `test.strategy.fail-fast is False`; матрица `${{ fromJSON(inputs.test-modules) }}`; `name == "test (${{ matrix.module.id }})"`.
  - `test_setup_steps_match_ci_by_name_and_body`: для каждого имени из `TOOLKIT_STEPS ∩ names(ci)` шаг в `checks` и `test` равен шагу в `ci`, без учёта `env` у `commands`; `run` у `commands` совпадает во всех трёх jobs.
  - `test_checks_job_has_no_test_steps`: в `checks` нет шагов, имя которых содержит `test`, кроме `Upload` (его тоже нет).
  - `test_module_values_reach_run_only_through_env`: ни один `run` в `checks`/`test` не содержит `${{`.
  - Через `_resolve`-хелпер (вынести из `GradleConsoleModeTests` в модульную функцию `resolve_java_commands(env: dict[str, str], tool_file: str) -> tuple[CompletedProcess, str]`): Gradle `MODULE=":core"` → `test=./gradlew :core:test --console=plain`; `SPLIT_TESTS=true` → `build=./gradlew build -x test --console=plain`; без `SPLIT_TESTS` → `build=./gradlew build --console=plain` (регрессия); Maven (`pom.xml`, `mvnw`) `MODULE=core` → `prepare=./mvnw --batch-mode --no-transfer-progress -pl core -am install -DskipTests`, `test=./mvnw --batch-mode --no-transfer-progress -pl core test`; `MODULE_TEST_COMMAND="./gradlew :it:check"` → `test` равен ему, `prepare` пуст; `MODULE='$(touch pwned)'` → файла `pwned` нет, `test` содержит экранированное значение.
  - `test_results_preflight_rejects_shared_files`: выполнить `run` шага `Check module Allure results` во временной директории: только `*-result.json` → код 0; с `environment.properties` → код 1, в stderr `collides between build modules` и `allure_categories_file`.
  - `test_module_upload_contract`: у `Upload test artifacts` в `test` — `if` с `always()`, `name: ${{ matrix.module.artifact }}`, `if-no-files-found: warn`.
- [ ] **Step 2: Run** `GIT_CONFIG_GLOBAL=/dev/null python -m unittest discover -s tests -p 'test_composite_actions.py' -v`. Ожидается: новые тесты FAIL (`KeyError: 'checks'`), существующие PASS.
- [ ] **Step 3: Implement** изменения в `java-ci.yml` по описанию выше; `GradleConsoleModeTests._resolve` переводится на новый хелпер.
- [ ] **Step 4: Run** тот же набор и `actionlint .github/workflows/java-ci.yml`. Ожидается: PASS и чистый actionlint.
- [ ] **Step 5: Commit** `feat(java): run tests as one job per build module (#530)`.

### Task 3: Gate, документация, финальная проверка

**Files:**
- Modify: `docs/modules.md`, `docs/usage.md` (раздел Allure, component mode)
- Test: `tests/test_ci_kit_gate.py`

**Interfaces:**
- Consumes: имена шагов и job из задачи 2.

- [ ] **Step 1: Write the regression tests** в `tests/test_ci_kit_gate.py` по образцу `test_failed_gradle_test_is_project`:
  - `test_failed_module_test_job_fails_java_as_project`: `needs.java.result == "failure"`, в jobs — `{"name": "java (GW) / test (core)", "conclusion": "failure", "steps": [{"name": "Set up Java", "conclusion": "success", "number": 3}, {"name": "Gradle module test with bounded recovery", "conclusion": "failure", "number": 7}]}` → гейт красный, категория `project`, failing step — это имя.
  - `test_failed_module_setup_is_toolkit`: падение на `Set up Java` в `java (GW) / test (core)` → `toolkit`.
- [ ] **Step 2: Run** `-p 'test_ci_kit_gate.py'`. Ожидается PASS без изменений в `gate.py`: `_matches` уже разбирает `java (GW) / test (core)`. Если FAIL — остановиться и разобраться через systematic-debugging, а не подгонять тест.
- [ ] **Step 3: Docs.**
  - `docs/modules.md`: раздел «Build modules (`test_modules`)» — пример Gradle (из spec §4) и Maven (`module: engine/core`); поля и запреты; job `checks` и `test (<id>)`; команды по умолчанию, включая двухшаговый Maven; `MODULE_PATH`; имя артефакта; ограничения Allure (три файла, лимиты 50/128, CIK-CORE-013).
  - `docs/usage.md`: в абзаце про component mode одно предложение: компонент с `test_modules` загружает по артефакту на модуль с `results`.
- [ ] **Step 4: Full verification:** `GIT_CONFIG_GLOBAL=/dev/null python scripts/validate.py --static`, `GIT_CONFIG_GLOBAL=/dev/null python scripts/run_test_suites.py`, `actionlint .github/workflows/*.yml examples/*.yml`, `git diff --check`. Ожидается: всё зелёное. Затем проверить изменённые файлы на `TODO`, `skip` и заглушки.
- [ ] **Step 5: Commit** `docs(java): document per-module test jobs (#530)`.
