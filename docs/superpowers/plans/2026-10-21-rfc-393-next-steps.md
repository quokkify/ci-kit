# RFC #393 — что осталось сделать

- RFC: [#393](https://github.com/quokkify/ci-kit/issues/393)
- Состояние на 2026-10-22; подробности — комментарии со статусом волны 2, раскатки v3.6.0 и итогом §11 в #393.
- Специфики: spec #1 `docs/superpowers/specs/2026-10-12-modular-ci-templates-design.md`, spec #2 `…/2026-10-12-modular-ci-migration-design.md`, spec #4 `…/2026-10-21-modular-ci-language-modules-design.md`. Runbook: `docs/modules.md`.

Каждый внешний шаг (merge, смена ruleset, раскатка) — только с подтверждения владельца.

## Где мы

| | Состояние |
|---|---|
| ci-kit | модули `core`, `java`, `python`, `node`, `docker` (#452); `ci-kit migrate` для репозиториев с компонентами (#454); релизы до v3.6.1 |
| На модулях | 12 из 12 (11 + новый archgate): волна 1 (7 × `core`), knowledge (`core`), skills (`core` + `python`), marketdesk (`core` + `java` + `node` + `docker`, marketdesk#418), q4j (`core`, q4j#781), archgate (`core` + `java`, archgate#5) |
| Раскатка | v3.6.1 по всем 12 (11 fleet PR + knowledge#70) без ручных правок; волна 2 закрыта (§11) |

## 1. Раскатка v3.6.0 и проверка Allure (сделано 2026-10-22)

### 1.1 Раскатка v3.6.0

- [x] Проверить последний запуск `copier-fleet-auto-update.yml`: pilots (gh-pages-subdir-action, skills, q4j) → fan-out без ошибок.
- [x] Смёржить зелёные fleet PR (squash, только `CLEAN`).
- [x] Пока идёт раскатка, ничего не мёржить в pilot-репозитории: gate останавливается с `Pilot base is behind the current target branch` и требует перезапуска.
- [x] **quokkify/archgate** — новый, 12-й потребитель (legacy, v3.3.0, компонент `java:.`). Раскатка v3.6.0 не смогла запушить ветку: `Permission to quokkify/archgate.git denied` (403) — токену раскатки (`FLEET_UPDATE_TOKEN`) не выдан доступ к репозиторию. Выдать доступ и перезапустить `copier-fleet-auto-update.yml` с `repository=quokkify/archgate`, `template-ref=<текущий тег>`. Его миграция на модули — отдельно (java-компонент, через `ci-kit migrate`, как marketdesk/q4j). Доступ выдан, archgate#3 смёржен. Мигрирован archgate#5; ruleset `main` создан (`CodeQL`, `gitleaks`, `ci / gate`).
- [x] knowledge (приватный, вне раскатки): `python scripts/update_copier_fleet.py --org quokkify --repo quokkify/knowledge --template-ref v3.6.0 --write` (с `GH_TOKEN`). knowledge#66 смёржен.

### 1.2 Проверка Allure после v3.6.0 (#450, исправлено #460)

Репозитории: car-service-platform, compose-health-check-action, path-of-exile-starter — после merge их fleet PR v3.6.0.

Критерий успеха на `main`: workflow **Allure report** (job «Generate and comment trusted Allure report», `workflow_run` с `main`) использует `allure-publisher-core.yml@v3.6.0`, шаги **Download validated Allure artifacts** и **Generate unprivileged report** — `success`. Запуски, где эти шаги `skipped`, доказательством не считаются.

```sh
gh run list --repo quokkify/<repo> --branch main --workflow allure-report.yml --limit 5
gh run view <run-id> --repo quokkify/<repo> --json jobs --jq '.jobs[]|"\(.name): \(.conclusion)"'
```

Ошибки до исправления (эталонные запуски):

| Репозиторий | Ошибка | Запуск |
|---|---|---|
| car-service-platform | `duplicate artifact path: …ci-env-fragment.properties` | 39654471037 |
| compose-health-check-action | `duplicate artifact path: allure-results/ci-env-fragment.properties` | 39654605924 |
| path-of-exile-starter | `No source allure-results directories found under .allure-input/source-artifacts` | 39653981725 |

Если шаг падает сейчас:

| Сообщение | Чья зона |
|---|---|
| `Expected exactly one module value …`, `Partial module provenance …` | фрагмент потребителя (car-service-platform#280 добавляет `<Prefix>.Module`, path-of-exile-starter#1136 пишет `Module=java`; оба смёржены) |
| `artifact … has no scannable allure-results directory`, `… outside a scannable allure-results directory`, `only regular files directly under allure-results are read` | новые ранние проверки ci-kit#460 (`safe_extract`, раскладка по артефактам) |

- [x] Все три репозитория зелёные → отметить в #393.
- [x] Если падает → комментарий в #450 (закрыт #460) с run URL и строкой ошибки. Не понадобилось.

## 2. Завершить волну 2 (spec #4 §9, §11)

- [x] Пауза выдержана: релиз v3.6.0 прошёл fleet `update` по skills и knowledge без ручных правок (skills#137 и knowledge#66 — одним bot-commit, `ci / gate` зелёный).
- [x] **marketdesk**: `python scripts/update_copier_fleet.py --org quokkify --repo quokkify/marketdesk --template-ref <текущий тег> --migrate core --write` → зелёный `ci / gate` → `python scripts/migration_checks.py --pr quokkify/marketdesk#<n> --dry-run` (ожидаемо `checks_before`: `Shared template contract`, `MarketDesk / ci`, `Java test automation / ci`, `docker / build`) → без `--dry-run` → merge. Проверить Allure external на `main`. Смёржен marketdesk#418; Allure от модульного `Validate`: 987 тестов, как до миграции.
- [x] **q4j**: то же; модуль только `core`, `checks_before` = `Shared template contract`. Required checks q4j от validate не зависят, но `migration_checks.py` всё равно добавляет `checks_after`: `ci / gate` теперь required, как в волне 1. Смёржен q4j#781.
- [x] Пауза: следующий релиз ci-kit проходит fleet `update` по marketdesk и q4j. v3.6.1: marketdesk#419, q4j#782.
- [x] Зафиксировать критерии §11 комментарием в #393: (1) никто не на legacy validate; (2) один релиз прошёл fleet по всем 11 без ручных правок; (3) ручное вмешательство — только подготовительные PR и отказы с кодом; (4) Allure-отчёты и Pages skills, marketdesk, q4j публикуются как до миграции.

## 3. Открытые хвосты

- [ ] ci-kit#470 — flaky unit test: `copier copy` падает с exit 1, stderr глотается (`tests/module_test_support.py` `run_copier`); заблокировал релиз v3.6.1 до перезапуска. Шаг 1 сделан: #477 (смёржен) печатает stdout/stderr упавшего подпроцесса в отчёте раннера. Три случая, все на раннерах с Python 3.15.1; локально и в контейнере 3.15.1 не воспроизводится (0/400). Ждём следующий сбой с видимой причиной, потом фикс.
- [x] ci-kit#459 — `migration_checks.py --revert` добавляет в required checks контексты, которых до миграции не было. Исправлено в #478: прямой запуск печатает `removed`/`added`, `--revert` требует `--restore` с этим `removed` (подмножество `checks_before`).
- [x] gh-pages-subdir-action#51 — `release-please debug-config` без токена ловит анонимный rate limit и останавливает раскатку на первом pilot. Исправлено в gh-pages-subdir-action#55: шаг передаёт `--token` с токеном задания.
- [x] `scripts/verify_ci_gate.py`: `HANDOFF_CONTEXTS["validate"]` перечисляет компонентные контексты pilot-репозиториев вручную (`App Python / ci`). Новый pilot с компонентами потребует новой строки; обобщить (выводить контексты из legacy `validate.yml` pilot'а, как `legacy_jobs.check_contexts`). Сделано в #480: `PILOT_CHECKS` — только собственные проверки пилота, `template_contexts` выводит проверки шаблона на SHA пилота, `HANDOFF_CONTEXTS` удалён.
- [ ] Отложенные мелочи из ревью волны 2:
  - Copier-валидаторы python/node принимают пустую `*_version`, схема отклоняет её только на `ci-kit plan`;
  - `legacy_jobs.DOCKER_INPUTS` дублирует `MODULES["docker"].fields`;
  - `migrate` оставляет коммиты, если `plan` нашёл замечания без расхождения эквивалентности; `_rollback` не чистит ignored-файлы; `-d toolkit_version` передаётся и не-core модулям;
  - lifecycle-тесты миграции: update проверяется только на том же ref, ручная правка — только у первого компонента.

## 4. Следующие волны RFC

Каждая — небольшая spec + plan по механике spec #2/#4 (область `handed_off`, модуль-владелец, `migrate`, pilot-кольца).

- [ ] **Удаление legacy validate** из `template/` (после §11): убрать область `validate` из legacy, `CIK-LEGACY-*` для неё, ветку `components` в `copier.yml`.
- [ ] **Spec #3 — Renovate → `renovate-presets`**: пресеты, Copier-модуль `renovate` по подключённым модулям, self-hosted runner как reusable workflow; разорвать цикл ci-kit ↔ renovate-presets.
- [ ] **security** (CodeQL + gitleaks): CodeQL сейчас выводит языки из legacy `components` — у мигрированных репозиториев answers `components` и модули дублируют источник истины (spec #4 §3).
- [ ] **release** (release-please, enrich), **allure** (publisher, Pages), **copier-update** (consumer workflow).
- [ ] **Удаление legacy-шаблона целиком**, когда все области переданы модулям.
- [ ] Решения RFC, ещё не принятые: нужны ли независимые релизы модулей; сохранять ли требование единого `toolkit_version` для всех `uses:` или заменить проверяемым контрактом совместимости.
