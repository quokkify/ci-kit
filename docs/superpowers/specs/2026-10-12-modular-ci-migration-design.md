# Миграция потребителей на модули и fleet — дизайн (spec #2)

- Статус: draft, на ревью
- RFC: [#393](https://github.com/quokkify/ci-kit/issues/393)
- Опирается на: `docs/superpowers/specs/2026-10-12-modular-ci-templates-design.md` (spec #1)
- Дата: 2026-10-12

## 1. Контекст

Spec #1 вводит модули (`core`, `java`) и reusable `validate.yml`, но существующие 11 потребителей живут на legacy-шаблоне (`template/`, один `.copier-answers.yml`). Fleet (`scripts/update_copier_fleet.py`, `scripts/rollout_copier_fleet.py`) и `copier-update.yml` у потребителя знают только один answers-файл. Required checks у потребителей завязаны на имена legacy-jobs (`Shared template contract` и т.п.).

Фактическая картина fleet на 2026-10-12:

| Репозиторий | `components` | Прочее |
|---|---|---|
| allure-report-action | — | codeql, gitleaks, release, renovate |
| car-service-platform | — | codeql, gitleaks, allure, release, renovate |
| compose-health-check-action | — | codeql, gitleaks, allure, release, renovate |
| gh-pages-subdir-action | — | codeql, gitleaks, allure, release, renovate |
| java-test-automation-template | — | codeql, gitleaks, release, renovate |
| knowledge | — | gitleaks, renovate |
| path-of-exile-starter | — | codeql, gitleaks, allure, release, renovate |
| renovate-presets | — | codeql, gitleaks, release, renovate |
| marketdesk | node | docker, codeql, gitleaks, allure, release, renovate |
| skills | python | codeql, gitleaks, allure, release (manifest), renovate |
| q4j | java, node, python | codeql, gitleaks, allure, release (manifest), renovate |

У 8 из 11 нет языковых компонентов: их legacy validate состоит только из `template-contract`.

## 2. Цель

Конечное состояние — все потребители полностью на модулях, legacy-шаблон удалён. Путь — гибрид: области legacy передаются модулям волнами, у каждого файла в любой момент один владелец.

Spec #2 покрывает **механику миграции** и **волну 1** (`validate` → `core` для 8 репозиториев без компонентов).

## 3. Не цели

- Волны `security` (codeql + gitleaks), `release`, `allure`, `copier-update`, языковые (`python`, `node`, маппинг `components`) — отдельные небольшие spec/plan по механике этого документа.
- Удаление legacy-шаблона.
- Drift для legacy-owned файлов.
- Renovate (spec #3).

## 4. Решения

| # | Решение | Причина |
|---|---|---|
| M1 | Гибрид: legacy-вопрос `handed_off` исключает файлы переданных областей. | Польза с первой волны; один владелец на файл. |
| M2 | Реестр областей — `tools/ci_kit/handoff.py`; тест сверяет его с `copier.yml`. | `copier.yml` не импортирует Python; тест не даёт копиям разойтись. |
| M3 | Проверки legacy `template-contract` переезжают в `ci-kit plan` (`CIK-LEGACY-*`) и применяются только к legacy-owned файлам. | Legacy `validate.yml` уходит к `core`. |
| M4 | Одна команда `ci-kit update` для fleet и `copier-update.yml`; `ci-kit migrate` на том же коде. | Убирает дублирование логики обновления. |
| M5 | Legacy-специфичные шаги fleet остаются в `update_copier_fleet.py`; модули обновляет `ci-kit update --skip-legacy`. | Без переписывания fleet; legacy-логика исчезнет вместе с областями. |
| M6 | Update и migrate никогда не смешиваются в одном PR. | При падении однозначно видно, что сломалось. |
| M7 | Fleet не меняет rulesets; смена required checks — отдельный helper поверх `reconcile_ruleset.py` с preflight на HEAD PR. | Сохраняет принцип `docs/architecture.md`; исключает PR, который нельзя смёрджить. |
| M8 | Откат миграции — `git revert` migration PR и обратная смена checks (`--revert`). | Copier не поддерживает даунгрейд (ruling R12 прототипа). |

## 5. Контракт передачи владения

### 5.1 Legacy-вопрос

```yaml
handed_off:
  type: yaml
  default: []
  when: "{{ module == 'legacy' }}"
  help: Areas now owned by ci-kit modules; legacy stops rendering their files.
```

Валидатор: YAML-список строк, каждая — известная область, без повторов.

`_exclude` legacy для волны 1:

```yaml
- "{% if 'validate' in (handed_off | default([])) %}.github/workflows/validate.yml{% endif %}"
```

### 5.2 Реестр

```python
@dataclass(frozen=True)
class Area:
    module: str
    legacy_paths: tuple[str, ...]

AREAS = {"validate": Area(module="core", legacy_paths=(".github/workflows/validate.yml",))}
```

Тест: множество областей в реестре = множество областей в валидаторе `handed_off` = множество областей в `_exclude`; пути совпадают.

### 5.3 Проверки `ci-kit plan`

| Код | Условие |
|---|---|
| `CIK-HANDOFF-001` | область в `handed_off`, модуль-владелец не подключён |
| `CIK-HANDOFF-002` | модуль подключён, его область не передана (legacy и модуль владеют одним файлом) |
| `CIK-HANDOFF-003` | `handed_off` содержит неизвестную область или не является списком строк |
| `CIK-LEGACY-001` | в `.copier-answers.yml` нет `_src_path`, `_commit` или `toolkit_version` |
| `CIK-LEGACY-002` | `_commit` ≠ `toolkit_version` |
| `CIK-LEGACY-003` | `uses: quokkify/ci-kit/...@<ref>` в legacy-owned workflow ≠ `toolkit_version` (module-owned файлы исключены) |
| `CIK-LEGACY-004` | включённая возможность (`renovate`, `codeql`, `allure_report`) без своих legacy-файлов |
| `CIK-LEGACY-005` | legacy-owned JSON/YAML не парсится |

Каждый код получает runbook в `docs/errors/`. Проверки `CIK-LEGACY-*` — перенос логики из `template/.github/workflows/validate.yml.jinja` (job `template-contract`); legacy-рендер без `handed_off` их по-прежнему выполняет сам.

## 6. `ci-kit update`

```text
ci-kit update --repo PATH --to REF [--module M ...] [--skip-legacy]
```

1. Находит answers-файлы; порядок: legacy → `core` → языковые модули (по имени) → прочие модули (по имени).
2. Для каждого файла: `copier update --defaults --conflict=rej --skip-tasks -a <file> --vcs-ref REF`; legacy — с `--trust` (как сейчас), модули — без. Для `core` дополнительно `-d toolkit_version=REF`.
3. После каждого файла — commit `chore(ci-kit): update <module> to <REF>` (Copier требует чистое дерево перед следующим update).
4. `.rej`-файл или ненулевой выход Copier → `CIK-UPDATE-001` со списком файлов и модулем; дальнейшие файлы не трогаются.
5. В конце — `ci-kit doctor`; findings → exit 1, коммиты остаются.

Вывод `--json PATH`: `{"ref", "modules": [{"module", "from", "to", "commit"}], "findings": [...]}`.

## 7. `ci-kit migrate`

```text
ci-kit migrate --module MODULE --repo PATH --to REF [--json PATH]
```

### 7.1 Предусловия

| Код | Проверка |
|---|---|
| `CIK-MIGRATE-001` | рабочее дерево чистое |
| `CIK-MIGRATE-002` | legacy `_commit` == `REF` |
| `CIK-MIGRATE-003` | передаваемые файлы совпадают с legacy-рендером на `_commit` (нормализация toolkit-ref как в drift); иначе abort с diff |
| `CIK-MIGRATE-004` | модуль ещё не подключён и область не передана |

### 7.2 Шаги (каждый — отдельный commit)

1. `copier update --trust --defaults --skip-tasks -a .copier-answers.yml --vcs-ref REF -d 'handed_off=[..., <area>]'`, затем `git rm` путей области.
2. `copier copy --defaults -a .copier-answers.<module>.yml -d module=<module> -d toolkit_version=REF --vcs-ref REF gh:quokkify/ci-kit .`
3. `ci-kit doctor`.

### 7.3 Вывод

```json
{"module": "core", "area": "validate", "ref": "v3.2.0",
 "checks_before": ["Shared template contract"],
 "checks_after": ["ci / gate"],
 "commits": ["<sha>", "<sha>"], "findings": []}
```

`checks_before` — отображаемые имена jobs удаляемого legacy `validate.yml` (`template-contract` и component jobs), прочитанные из файла до шага 1. `checks_after` для `core` — `["ci / gate"]`.

### 7.4 Инвариант caller'а

Caller `core` сохраняет `name: Validate`: на это имя подписаны `workflow_run`-триггеры Allure external-режима. Тест в наборе модулей фиксирует имя.

## 8. Fleet

### 8.1 Инвентарь

- Листинг корня репозитория через contents API; собираются `.copier-answers.yml` и `.copier-answers.*.yml`.
- По каждому файлу: модуль, `_commit`, `_src_path` (проверка allowlist `quokkify/ci-kit` через `fullmatch`).
- Состояние: `legacy` | `hybrid(<areas>)` | `modules`; попадает в JSON/Markdown-отчёты и health-отчёт.

### 8.2 Режим `update`

Существующий legacy-pipeline → `ci-kit update --skip-legacy --to <target>`. Один PR; тело — таблица `модуль | old → new | doctor`.

### 8.3 Режим `migrate --module core`

| Ситуация | Действие |
|---|---|
| нет legacy-answers или область уже передана | пропуск, статус `not-applicable` |
| legacy `_commit` ≠ цель | пропуск, статус `needs-update-first` |
| `CIK-MIGRATE-*` | PR не создаётся; код и diff в отчёте |
| иначе | клон → `ci-kit migrate` → ветка `ci-kit/migrate-<module>` → PR |

PR: заголовок `chore(ci-kit): hand off <area> to <module> module`; тело — изменения, коммиты, вывод `doctor`, `checks_before → checks_after`, точная команда `migration_checks.py`, откат.

### 8.4 Pilot-кольца

`rollout_copier_fleet.py` для `migrate` берёт ожидаемые checks из `checks_after` migrate-JSON. Pilots волны 1: `gh-pages-subdir-action`, `java-test-automation-template`, `renovate-presets`.

### 8.5 Смена required checks

```text
python scripts/migration_checks.py --pr OWNER/REPO#N [--revert] [--dry-run]
```

1. Читает migrate-JSON из тела PR: первый fenced-блок ```json сразу после строки `<!-- ci-kit-migrate -->`; fleet пишет его в тело PR.
2. Читает текущие effective required checks ветки (переиспользует код health-отчёта).
3. Новый набор = текущие − `checks_before` + `checks_after` (`--revert` — наоборот). Набор не может стать пустым.
4. Вызывает `reconcile_ruleset.py --repo … --revision <PR head> --active --check …`.

Порядок на репозиторий: PR зелёный → `migration_checks.py` → merge. Fleet не мёрджит и не меняет rules.

### 8.6 `copier-update.yml` потребителя

Legacy-owned шаблон получает шаг после legacy-update: `ci-kit update --skip-legacy --to "$ref"`.

## 9. Волна 1

Зависимости:

1. Прототип spec #1 — go (Task 8: вложенный reusable, имена checks, `ci / gate` в реальном GitHub).
2. Релиз ci-kit `vX` с механикой spec #2.

Порядок:

1. Fleet `update` всех репозиториев до `vX`.
2. `migrate --module core` для pilots; на каждый: зелёный PR → `migration_checks.py` → merge.
3. Следующий релиз ci-kit проходит fleet `update` по гибридным pilots без конфликтов.
4. `migrate` для allure-report-action, car-service-platform, compose-health-check-action, knowledge, path-of-exile-starter.

marketdesk, skills, q4j остаются на legacy validate до языковых волн.

Канал быстрого фикса через Renovate-bump caller'а (spec #1 §9.5) не работает, пока Renovate-preset матчит только `quokkify/project-toolkit/...`; до spec #3 версию caller'а двигает fleet `update` (шаг 2 раздела 6).

## 10. Тестирование

- **Unit:** согласованность реестра областей и `copier.yml`; негативный тест на каждый `CIK-HANDOFF-*`, `CIK-LEGACY-*`, `CIK-MIGRATE-*`, `CIK-UPDATE-*`; порядок и коммиты `update`; `migration_checks`: расчёт набора (сохраняет не-validate checks), `--revert`, отказ от пустого набора.
- **Lifecycle (локально):** каждый legacy-сценарий без компонентов → `migrate core` → `doctor` чист → `update` до следующего тега без конфликтов → `git revert` возвращает исходный рендер байт-в-байт.
- **Legacy-совместимость:** рендер с `handed_off: []` идентичен рендеру без вопроса.
- **Fleet:** в стиле `tests/test_update_copier_fleet.py` — несколько answers-файлов, отбор `migrate`, `needs-update-first`, тело PR, отказ без PR при `CIK-MIGRATE-003`.
- **Ring 0:** job `modules` в `validate-toolkit.yml` дополнительно мигрирует legacy-fixture на `core` и прогоняет `doctor`.

## 11. Критерии готовности волны 1

1. 8 репозиториев в состоянии `hybrid(validate)`; их ruleset требует `ci / gate`, прочие required checks сохранены.
2. Один релиз ci-kit прошёл fleet `update` по всем 8 без ручных правок.
3. Ни один migration PR не потребовал ручного вмешательства, кроме отклонённых `migrate` с кодом.
