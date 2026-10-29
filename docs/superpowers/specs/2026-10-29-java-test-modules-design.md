# Java: одна test-job на модуль сборки — дизайн

- Статус: draft, на ревью
- Issue: #530; потребители: quokkify/archgate#36, quokkify/q4j
- Опирается на: `docs/modules.md`, `docs/usage.md` (Allure, component mode), spec `2026-10-21-modular-ci-language-modules-design.md`
- Дата: 2026-10-29

## 1. Контекст

Модуль `java` запускает одну job на компонент. Multi-module сборка Gradle или Maven — это один компонент с `path: .`, поэтому `.github/workflows/java-ci.yml` выполняет lint, test и build последовательно в единственной job `ci`. Отсюда три проблемы:

- модули тестируются без параллелизма, и на всю сборку приходится одна проверка;
- Gradle останавливается на первой упавшей test-задаче, поэтому в Allure-отчёт не попадают модули, стоящие после неё;
- модуль не передаётся в отчёт как идентичность.

q4j обходит это собственным workflow «Run tests» и `allure` в режиме `external`. Обходной путь без своего workflow — один компонент на модуль, все с `path: .`. Он работает, но список модулей приходится держать в Copier answers, setup повторяется в каждом компоненте, модуль не передаётся в отчёт, а lint и build вручную вешаются на один из компонентов.

Устройство сейчас:

- `ci-kit plan` (`tools/ci_kit/plan.py`) валидирует `.ci/modules/java.yml` по `java.v1.json` и через `fill()` превращает компоненты в матрицу;
- `validate.yml` вызывает `java-ci.yml` по разу на компонент (`fail-fast: false`), а `gate` оценивает `needs.java`;
- компонентный режим `allure` принимает артефакты `^allure-results-[A-Za-z_][A-Za-z0-9_-]*$` в количестве от 1 до 50. Они раскладываются в layout `merged`, и `safe_extract.py` падает на любом повторе пути (`duplicate artifact path`).

## 2. Цель и критерии готовности

Компонент сборки один раз перечисляет свои модули в декларации и получает:

- одну параллельную test-job и один Allure-артефакт на каждый модуль;
- lint и build один раз на компонент;
- отчёт, который покрывает все модули, даже если один из них упал;
- PR, который блокируется через `gate`, если упала любая модульная job.

Готово, когда archgate работает на `test_modules` и `allure` в component mode, а q4j может удалить свой «Run tests».

Вне scope:

- автообнаружение модулей (`test_modules: auto`). Схема оставляет под него место, реализация — отдельная задача;
- изменения в компонентном и external режимах `allure`;
- перевод q4j и archgate — это PR в их репозиториях.

## 3. Принятые решения

| Вопрос | Решение |
|---|---|
| Источник списка модулей | Явный `test_modules` в декларации; discovery позже, аддитивно |
| Build tools | Gradle и Maven |
| Порядок jobs | `checks` и модульные `test` стартуют параллельно |
| Где разворачивать матрицу | Внутри `java-ci.yml` (подход A); планировщик и gate не меняются |
| Команда модуля | Дефолт по build tool, переопределяется `test_command` модуля |
| Allure | Компонентный режим без изменений; preflight в test-job |

Отклонённые варианты:

- **B: разворачивать матрицу в планировщике** — отдельный output `java_tests` и новая job в `validate.yml`. Требует правок `gate`, шаблона validate и контракта плана.
- **C: разворачивать модули в компоненты на уровне Copier.** Не решает проблему lint/build и не даёт модулю идентичность в отчёте.

## 4. Декларация и планировщик

```yaml
schema_version: 1
components:
  - id: archgate-java
    name: ArchGate Java
    path: .
    java_version: "21"
    test_modules:
      - id: core
        module: ":core"
        results: engine/core/build/allure-results
      - id: testing
        module: ":testing"
        test_command: ./gradlew :testing:check --console=plain
        results: testing/build/allure-results
```

### 4.1 Схема `java.v1.json`

Изменение аддитивное, `schema_version` остаётся `1`.

`test_modules` — массив (`minItems: 1`) объектов с `additionalProperties: false`:

| Поле | Обязательно | Ограничения | Смысл |
|---|---|---|---|
| `id` | да | `^[A-Za-z_][A-Za-z0-9_-]*$` | Идентификатор job и суффикс артефакта |
| `module` | да | `minLength: 1`, без переводов строк | Gradle project path (`:core`) или Maven-селектор `-pl` (`engine/core`, `:artifactId`) |
| `test_command` | нет | `minLength: 1` | Доверенная команда, заменяет команду по умолчанию |
| `results` | нет | тот же паттерн, что у `path`, без `..` и glob | Директория Allure results относительно `path` компонента; если задана, включает upload |

Если у компонента есть `test_modules`, на уровне компонента запрещены поля `test_command`, `upload_test_artifacts`, `test_artifact_name` и `test_artifact_path` (`dependentSchemas` → `not.anyOf required`). Нарушение даёт CIK-CORE-004 с путём к полю.

`run_tests: false` вместе с `test_modules` допустимо и отключает всю модульную матрицу.

Под discovery `test_modules` позже расширится до `oneOf: [array, {const: "auto"}]`, тоже без повышения версии схемы.

### 4.2 Проверки `ci-kit plan`

К проверкам компонентов (CIK-CORE-005) добавляются:

- `id` модулей уникальны в пределах компонента;
- имя артефакта `allure-results-<component id>-<module id>` не длиннее 128 символов (лимит имени в `allure-publisher-core.yml` и `safe_extract.py`) и не совпадает с именем артефакта другого компонента, включая обычный `test_artifact_name`;
- если включён модуль `allure`, общее число артефактов `allure-results-*` в плане не больше 50 (`maximum-artifacts` компонентного режима).

Код ошибки — CIK-CORE-005 для дублей и коллизий и новый CIK-CORE-013 для лимитов (следующий свободный номер; модульных префиксов у кодов нет). Новому коду нужна страница в `docs/errors/`.

### 4.3 Матрица

`fill()` передаёт `test_modules` как список; если модуля нет, используется значение по умолчанию из `workflow_inputs.json`. `validate.yml` пробрасывает:

```yaml
component-id: ${{ matrix.component.id }}
test-modules: ${{ toJSON(matrix.component.test_modules) }}
```

`java-ci.yml` считает пустыми значения `""`, `"[]"` и `"null"`.

## 5. `java-ci.yml`

### 5.1 Inputs

| Input | Тип | По умолчанию | Смысл |
|---|---|---|---|
| `component-id` | string | `java` | Префикс имён модульных артефактов |
| `test-modules` | string | `""` | JSON-массив модулей из декларации |

### 5.2 Jobs

- **`ci`** — `if: <test-modules пуст>`. Поведение то же, что сейчас, байт-в-байт по шагам. Это регрессионная граница для всех существующих потребителей.
- **`checks`** — `if: <test-modules не пуст>`. Выполняет setup, lint и build. Тестов не запускает:
  - Gradle по умолчанию: `<runner> build -x test --console=<mode>`, потому что `build` зависит от `test`;
  - Maven: текущий `package -DskipTests`;
  - пользовательские `lint_command` и `build_command` используются как есть.
- **`test`** — `if: <test-modules не пуст> && inputs.run-tests`; `name: test (${{ matrix.module.id }})`; `strategy: {fail-fast: false, matrix: {module: ${{ fromJSON(inputs.test-modules) }}}}`. Шаги:
  1. setup (общий, см. 5.3);
  2. env: `MODULE_PATH=${{ matrix.module.module }}`, `ALLURE_LABEL_MODULE=${{ matrix.module.id }}` (см. 6.2);
  3. команда:
     - `matrix.module.test_command`, если задана;
     - Gradle: `<runner> <module>:test --console=<mode>` через `actions/gradle-retry`;
     - Maven, два шага: `<runner> --batch-mode --no-transfer-progress -pl <module> -am install -DskipTests`, затем `<runner> --batch-mode --no-transfer-progress -pl <module> test`. Вариант `-am test` не подходит: он запускает и тесты upstream-модулей;
  4. preflight results (см. 6.1), `if: always() && matrix.module.results`;
  5. `actions/upload-artifact`, `if: always() && matrix.module.results`: `name: allure-results-<component-id>-<id>`, `path: <working-directory>/<results>`, `if-no-files-found: warn`. При ошибке компиляции results нет, и предупреждение не маскирует первичную ошибку; job остаётся красной из-за шага 3.

Значения модуля (`module`, `test_command`) передаются в шаги только через `env`, никогда не интерполируются в `run:`. Так сделано и для существующих команд.

`timeout-minutes`, `distribution`, `cache-*`, `install-command` и `gradle-console` применяются к каждой job одинаково.

### 5.3 Общий setup

Checkout репозитория и тулкита, разрешение build tool, `setup-java`, разрешение команд и `install-command` сейчас инлайном лежат в `ci`. Они выносятся в локальный composite action `actions/setup-java-ci` и вызываются из `ci`, `checks` и `test`. Сначала, на этапе плана, нужно проверить, можно ли переиспользовать или расширить существующий `actions/setup-java-gradle`. Если вынос меняет шаги `ci`, это регрессия, и тест 8.4 её ловит.

### 5.4 Gate

Изменений нет: падение любой `test (<id>)` делает вызывающую job `java (<name>)` failure, и `ci-kit gate` блокирует PR. Это подтверждает тест на `ci-kit gate` с `needs.java.result == failure`.

## 6. Allure

### 6.1 Коллизии в layout `merged`

Файлы `*-result.json` и `*-container.json` названы по UUID и между модулями не пересекаются. Пересекаться могут `environment.properties`, `executor.json` и `categories.json`. Если они есть в двух модулях, `safe_extract.py` в workflow отчёта падает с `duplicate artifact path`, и связь с причиной неочевидна.

Поэтому шаг preflight в test-job падает, если в `results` есть любой из этих файлов, и пишет сообщение: в модульном режиме файлы конфликтуют между модулями; categories задаются через `allure_categories_file` модуля `allure`. Компонентный режим `allure` не меняется.

### 6.2 Идентичность модуля

- `MODULE_PATH` — контракт из #530. Его читает `SuiteListener` в q4j (Allure `subSuite`).
- `ALLURE_LABEL_MODULE` нужен, чтобы `module-environment-label: module` в `actions/allure-report` разложил отчёт на окружения по модулям без кода в проекте. **Не проверено**: поддержку env `ALLURE_LABEL_*` в allure-java (и что label попадает в результаты Allure 3) нужно подтвердить по исходникам на первом шаге плана. Если не подтвердится, переменная не добавляется, и в доке остаётся только `MODULE_PATH`.

## 7. Документация

- `docs/modules.md`: раздел о multi-module сборке — пример для Gradle и Maven, правила полей, ограничения Allure (6.1), что делают `MODULE_PATH` и `ALLURE_LABEL_MODULE`.
- `docs/usage.md`, раздел Allure: в компонентном режиме компонент может загрузить по одному артефакту на модуль.
- `docs/errors/CIK-CORE-013.md`.

## 8. Тестирование

1. `tests/test_ci_kit_plan.py`:
   - схема принимает `test_modules` и per-module `test_command`;
   - схема отвергает `test_command`, `upload_test_artifacts`, `test_artifact_name` и `test_artifact_path` рядом с `test_modules`;
   - ловятся дубли `id` модулей, коллизия имён артефактов, длина больше 128 и лимит 50 при включённом `allure`;
   - `fill()` передаёт список.
2. `tests/test_modules_render.py` и новый сценарий `tests/scenarios/modules/core-java-modules.yml`: рендер декларации с `test_modules`.
3. `tests/test_modular_validate_workflow.py`: `validate.yml` передаёт `component-id` и `test-modules`; `workflow_inputs.json` синхронен.
4. Структурные тесты `java-ci.yml`:
   - при пустом `test-modules` шаги `ci` не изменились (регрессионная защита);
   - при непустом есть `checks` без тестов (`-x test`) и матрица `test` с `fail-fast: false`;
   - имя артефакта, `if: always()`, `if-no-files-found: warn`;
   - Gradle- и Maven-команды по умолчанию, включая двухшаговый Maven;
   - значения модуля передаются только через `env`;
   - есть preflight-шаг.
5. `tests/test_composite_actions.py`: новый setup action.
6. Проверка gate: failure модульной job → `java` failure → gate красный.

## 9. Раскатка

1. Релиз `feat` → 4.2.0. Изменение аддитивное, миграция fleet не нужна.
2. Канарейка — archgate (quokkify/archgate#36): переход на `test_modules` и component mode `allure`. Проверка — Allure-комментарий из PR-run, потому что push в `main` Allure не запускает.
3. q4j: удалить «Run tests» и перейти с `external` на component mode. Отдельный PR в q4j.
