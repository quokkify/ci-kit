# Удаление legacy validate — дизайн (spec #5, закрытие RFC #393)

- Статус: draft, на ревью
- RFC: [#393](https://github.com/quokkify/ci-kit/issues/393)
- Опирается на: spec #2 (`2026-10-12-modular-ci-migration-design.md`), spec #4 (`2026-10-21-modular-ci-language-modules-design.md`), `docs/modules.md`
- Дата: 2026-10-22

## 1. Контекст

Волны 1 и 2 закрыты, критерии §11 spec #2 записаны в #393. Все 12 потребителей на v3.7.0, у каждого в `.copier-answers.yml` `handed_off: [validate]`, `validate.yml` рендерит модуль `core`. Legacy `template/.github/workflows/validate.yml.jinja` никто не рендерит, но его поддерживают:

- `copier.yml`: `handed_off` с default `[]` и исключение `validate.yml` в `_exclude`;
- `ci-kit migrate` (`tools/ci_kit/migrate.py`): единственная область — `validate`. `_contract_findings` рендерит legacy на целевом ref и сравнивает его `validate.yml` с репозиторием;
- `legacy_jobs.py`, `equivalence.py`: разбор legacy-jobs и проверка эквивалентности для `migrate`;
- раскатка: `update_copier_fleet.py --migrate`, `rollout_copier_fleet.py --migrate`, проверка миграции в `validate_modules_fixture.py`;
- тесты `migrate`, lifecycle, equivalence и `legacy_jobs`, собирающие шаблон из HEAD.

`migrate` требует `_commit == --to` и legacy validate в рендере на этом ref. Поэтому после удаления файла мигрировать на новый релиз невозможно в принципе, а код миграции остаётся без потребителей.

## 2. Цель

1. В `template/` нет legacy validate. Legacy-шаблон рендерит только CodeQL, gitleaks, release, Renovate, Allure, copier-update и docs; `validate.yml` всегда принадлежит `core`.
2. Репозиторий без `validate` в `handed_off` не теряет CI молча: `copier copy`/`update` legacy и `ci-kit plan` отказывают с `CIK-LEGACY-006` и runbook.
3. Код миграции validate удалён; остаётся только то, что нужно для отката уже сделанных миграций (`migration_checks.py`).
4. RFC #393 закрыт: оставшиеся пункты §4 — отдельные issues.

## 3. Не цели

- Ответы `components`, `docker` и прочие legacy-вопросы `copier.yml`: их читают CodeQL, Allure (`allure_source: auto`), release-please manifest и `docs/ci-kit.md`. Ни один legacy-вопрос не используется только legacy validate (проверено по `template/` и `copier.yml`). Уходят в волне security (spec #4 §3).
- Реестр областей (`handoff.py`, `handed_off`, `CIK-HANDOFF-*`): нужен следующим волнам.
- Общий каркас миграции для будущих областей: волны security/release спроектируют свою механику.
- Пункты §4 RFC, кроме этого.

## 4. Решения

| # | Решение | Почему |
|---|---|---|
| D1 | Удалить `template/.github/workflows/validate.yml.jinja` и строку `_exclude` для него. | Файл ничей. |
| D2 | `handed_off`: default `[validate]`, валидатор требует `validate` в списке. Допустимые области не меняются. | Новая legacy-копия сразу в `hybrid(validate)`; ответ остаётся явным для `plan` и следующих волн. |
| D3 | Guard в `_exclude` через `mandatory`, по образцу `__allure_pin_mismatch__`: при `module == 'legacy'` и `validate` не в `handed_off` рендер падает с текстом `CIK-LEGACY-006` и ссылкой на runbook. | Copier с `--defaults` не валидирует сохранённые ответы; `_exclude` вычисляется всегда. Без guard `copier update` молча удалит `validate.yml`. |
| D4 | `ci-kit plan`: `legacy_findings` выдаёт `CIK-LEGACY-006`, если в legacy answers нет `validate` в `handed_off`. Новый код в `codes.py` и `docs/errors/CIK-LEGACY-006.md`. | Отказ виден и без рендера, в `doctor`/`plan`. |
| D5 | Удалить `ci-kit migrate` (подкоманда CLI, `migrate.py`), `legacy_jobs.py` (`check_contexts` переезжает, см. D6), `equivalence.py`, `--migrate` в `update_copier_fleet.py` и `rollout_copier_fleet.py`, шаг миграции в `validate_modules_fixture.py`, их тесты, коды `CIK-MIGRATE-*` в `codes.py` и `docs/errors/CIK-MIGRATE-*.md`. `CIK-UPDATE-001` остаётся: его выдаёт `update`. | Ноль потребителей; на новых ref миграция невозможна (§1). |
| D6 | `migration_checks.py` остаётся рабочим: `parse_marker`, `MARKER`, `CHECKS_AFTER` и `check_contexts` переезжают в `tools/ci_kit/migration_marker.py`. | Откат required checks у 12 мигрированных (`--revert --restore`) нужен до закрытия RFC и после. |
| D7 | Отставший репозиторий мигрирует CLI ci-kit с последнего релиза, где legacy validate есть (`vLAST`), с `--to vLAST`, затем обновляется. Runbook `CIK-LEGACY-006` описывает это; `vLAST` вписывается в runbook и CHANGELOG при релизе. На момент записи последний тег v3.7.0, release PR #475 готовит v3.8.0. | Путь миграции сохраняется без поддержки кода в `main`. |
| D8 | `template/docs/ci-kit.md.jinja`: убрать строку `validate.yml` и пункт чек-листа про пути компонентов в нём. README/usage/human-guide/architecture/modules: новый репозиторий = legacy + `core` (+ языковые модули). | Документация описывает единственный путь. |

## 5. Версия релиза

`docs/versioning.md`: «Breaking workflow/input/template changes require a new major release». Для всех 12 потребителей контракт не меняется: они уже в `hybrid(validate)`, fleet `update` проходит без правок. Ломается legacy-копия без `core` и подкоманда `ci-kit migrate`.

Решение владельца при согласовании подхода — minor. Эта spec фиксирует расхождение с политикой: при ревью spec владелец подтверждает minor или выбирает major (`feat!` с футером `BREAKING CHANGE:`). До подтверждения план пишется под minor с отдельным разделом «Breaking для не-флита» в CHANGELOG.

## 6. Поток для разных репозиториев

| Репозиторий | `copier update` legacy на новый релиз | `ci-kit plan` |
|---|---|---|
| флит (12), `handed_off: [validate]` + `core` | без изменений | без изменений |
| legacy без `handed_off` (вне флита) | отказ `CIK-LEGACY-006`, файлы не меняются | `CIK-LEGACY-006` |
| новая legacy-копия | `handed_off: [validate]`, `validate.yml` не создаётся; без `core` `plan` выдаёт `CIK-HANDOFF-001` | `CIK-HANDOFF-001`, пока не добавлен `core` |

## 7. Тесты

- Регрессия guard: `copier copy` и `copier update` legacy с `handed_off: []` падают с `CIK-LEGACY-006` и не трогают файлы; с `[validate]` проходят и не рендерят `validate.yml`.
- `plan`: негативный тест `CIK-LEGACY-006`; существующие тесты `CIK-HANDOFF-*` остаются.
- `copier.yml`: default `handed_off` = `[validate]`, валидатор отклоняет список без `validate`.
- `migration_checks` и маркер: тесты переезжают на `migration_marker.py` без изменения поведения.
- Тесты, рендерящие legacy с компонентами и проверяющие legacy `validate.yml`, удаляются или переводятся на `hybrid(validate)`; тесты `migrate`/lifecycle/equivalence/`legacy_jobs` удаляются вместе с кодом. `scripts/run_test_suites.py` обновляет список файлов.
- Lifecycle `update` для `hybrid(validate)` (#483) остаётся и проходит на новом шаблоне.
- Проверка: `scripts/validate.py` и полный набор тестов зелёные локально и в CI.

## 8. Раскатка

1. PR в ci-kit, ревью, merge — с подтверждения владельца.
2. Релиз (release-please), fleet `update` по 12 + knowledge вручную. Критерий: все PR зелёные без ручных правок, `validate.yml` в них не меняется.
3. Отметить пункт в чек-листе.

## 9. Закрытие RFC #393

После шага 2 §8, каждый шаг с подтверждения владельца:

1. Issues по оставшимся пунктам §4: spec #3 Renovate → `renovate-presets`; модуль security (CodeQL + gitleaks, удаление дублирующих `components`); модули release, allure, copier-update; удаление legacy-шаблона целиком; решения о независимых релизах модулей и едином `toolkit_version`. Плюс уже открытый #470.
2. Итоговый комментарий в #393: что сделано по волнам, ссылки на issues; закрыть #393.
3. Закрыть #466 (живой чек-лист) со ссылкой на итоговый комментарий.
