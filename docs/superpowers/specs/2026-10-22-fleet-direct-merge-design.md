# Прямой мерж fleet-PR обновления шаблона — дизайн

- Статус: draft, на ревью
- Issue: [#245](https://github.com/quokkify/ci-kit/issues/245)
- Опирается на: #455 (`--auto-merge`), `docs/fleet-health.md` §Auto-merge
- Дата: 2026-10-22

## 1. Контекст

`update_copier_fleet.py --auto-merge` (#455) включает нативный auto-merge
GitHub (`gh pr merge --auto`) для подходящих PR из
`automation/copier-template-update`. На практике он ничего не мержит:

- во всём публичном флоте `allow_auto_merge: false`, поэтому условие 4 из
  `fleet-health.md` всегда даёт `skipped`;
- `rollout_copier_fleet.py` отклоняет флаг (`auto-merge is not wired into
  the pilot gate yet`), и `copier-fleet-auto-update.yml` его не передаёт;
- ежедневные запуски (`17 4 * * 0,2-6`) работают в режиме dry-run, а запуски с
  записью идут только после релиза и по понедельникам.

Канарейка 2026-10-22 (#245): на приватном репо Free-плана `allow_auto_merge`
не включается, а `gh pr merge --auto` молча мержит сразу, даже с pending или
failed проверками. Нативный путь нельзя ослаблять до вызова `--auto` без
условия 4.

Приватные репо (сейчас только knowledge) остаются вне флота
(`PRIVATE_REPOSITORY_ERROR`). knowledge обновляет себя сам (knowledge#68).

## 2. Цель

Каждый автоматический запуск флота находит уже открытый PR обновления шаблона
и мержит его, если PR подходит по правилам #455 и его проверки зелёные.
Настройка `allow_auto_merge` для этого не нужна.

## 3. Решения

| # | Вопрос | Решение | Почему |
|---|--------|---------|--------|
| D1 | Способ мержа | Если `allow_auto_merge` выключен, подходящий PR мержится напрямую: `gh pr merge <n> --squash --match-head-commit <sha>`. Если включён, работает прежний нативный путь | Флаг не нужно включать в каждом репо. Rulesets публичных репо GitHub проверяет и при прямом мерже |
| D2 | Когда PR готов | Все записи `statusCheckRollup` на текущем head завершены с `SUCCESS`, `NEUTRAL` или `SKIPPED`; последняя завершилась не меньше 10 минут назад; `mergeable == MERGEABLE`; хотя бы одна проверка есть | Те же правила, что у knowledge `auto-merge.yml`. Пауза ловит workflows, которые регистрируют проверки поздно |
| D3 | Новые состояния `auto_merge` | `merged` (смержен этим запуском), `waiting` (проверки идут, их нет или пауза не прошла), `skipped` с причиной `failed checks: …` | Отчёт отличает «ещё рано» от «сломано» |
| D4 | Ежедневный мерж | Новый режим `--merge-ready`. Copier не запускается, ничего не пушится. Для каждого репо флота берётся открытый PR из automation-ветки, правила проверяются, PR мержится по D1/D2 | Пользователь выбрал ежедневный мерж. Обновлять шаблон каждый день не нужно |
| D5 | Версии в `--merge-ready` | `applied` — это `_commit` в `.copier-answers*.yml` на базовой ветке, `target` — в head PR, через `gh api …/contents?ref=`. Модули сравниваются по файлам. Файл ответов, который появился или пропал, делает PR неподходящим | В этом режиме нет локального прогона, из которого брать `inventory.commit` и `rows` |
| D6 | Подключение к rollout | `rollout` перестаёт отклонять `--auto-merge` и передаёт его во все стадии. `--merge-ready` проходит мимо гейта пилотов, как `--dry-run` и `--repo` | Шаблон не меняется, значит пилоты нечего проверять |
| D7 | Workflow | Запуски с записью (релиз, понедельник) получают `--auto-merge`. Ежедневный cron после dry-run аудита запускает второй шаг `--write --auto-merge --merge-ready` с `FLEET_UPDATE_TOKEN` | Закрывает открытое решение 3 из #245: каждый автоматический запуск мержит готовое |
| D8 | Пилоты | В том же запуске пилот после `verify_pilot` не мержится, его мержит следующий ежедневный `--merge-ready` | Меньше изменений в rollout. Задержка не больше суток |

## 4. Изменения

### 4.1 `scripts/update_copier_fleet.py`

- В `AUTO_MERGE_PULL_FIELDS` добавляется `statusCheckRollup`.
- Новая чистая функция `checks_verdict(rollup, now) -> (state, reason)` для
  правил D2.
- `enable_auto_merge`: если подходящий PR в репо с выключенным
  `allow_auto_merge`, вместо `skipped` вызывается `checks_verdict`, а при
  `ready` — прямой мерж с `--match-head-commit expected`. Ошибка `gh pr merge`
  даёт `failed` с очищенным текстом, как сейчас.
- `--merge-ready`: работает только вместе с `--write --auto-merge` и не
  совместим с `--migrate`, `--dry-run` и `--template-ref`. Для каждого репо:
  найти открытый PR по `--head <branch>`. Если PR нет — `up-to-date` без
  записи. Если есть — прочитать версии по D5 и передать их в
  `enable_auto_merge` с `head=None` (текущий head PR).

### 4.2 `scripts/rollout_copier_fleet.py`

- Убрать `GateError` для `--auto-merge`.
- Добавить `args.merge_ready` в сквозную ветку вместе с `dry_run`/`repo`.

### 4.3 `.github/workflows/copier-fleet-auto-update.yml`

- В режиме записи к `args` добавляется `--auto-merge`.
- Новый шаг `Merge ready template updates`: только для
  `github.event.schedule == '17 4 * * 0,2-6'`, запускает
  `rollout_copier_fleet.py` с `--write --auto-merge --merge-ready` и
  `FLEET_UPDATE_TOKEN`. Отчёт пишется в summary.

### 4.4 Отчёт и документация

- В строке контекста отчёта появляются `N merged` и `N waiting on CI` из
  `auto_merge.state`. `skipped` с `failed checks` попадает в существующую
  корзину «PR with failed CI».
- `docs/fleet-health.md` §Auto-merge: прямой мерж, правила D2,
  `--merge-ready`, расписание.

## 5. Безопасность

- Мержатся только PR, чей head — automation-ветка в самом репо
  (`isCrossRepository == false`), с базой в ветке по умолчанию.
- Major-бамп, откат версии, не-релизный тег и явный `--template-ref` остаются
  ручными (правила #455 не меняются).
- `--match-head-commit` защищает от гонки с новым пушем между проверкой и
  мержем.
- Никакого `gh pr merge --auto` без включённого `allow_auto_merge`
  (канарейка, §1).
- `FLEET_UPDATE_TOKEN` уже ограничен публичными репо quokkify. Права не
  расширяются: мерж требует тех же `contents`/`pull_requests: write`, что
  и создание PR. Для PR, которые меняют `.github/workflows/*`, нужен
  `workflows: write`. Это проверяется на первом пилоте, и если права нет,
  получится явный `failed`.

## 6. Тесты

- `checks_verdict`: зелёные и устоявшиеся проверки, pending, failed,
  StatusContext, пустой rollup, пауза меньше 10 минут.
- `enable_auto_merge` с фикстурами `gh`: прямой мерж с `--match-head-commit`
  при выключенном `allow_auto_merge`; нативный путь без изменений при
  включённом; `waiting` и `failed checks`.
- `--merge-ready`: чтение версий из API (D5), нет PR → `up-to-date`, major-бамп
  → `skipped`, несовместимые флаги отклоняются.
- `rollout`: принимает `--auto-merge`, `--merge-ready` идёт мимо пилотов.
- Валидатор и actionlint для workflow.
- Канарейка после релиза: `workflow_dispatch` на `compose-health-check-action`
  с открытым PR обновления, затем один ежедневный прогон.

## 7. Вне области

- Приватные репо и снятие `--public-only`.
- Мерж пилотов в том же запуске после `verify_pilot` (D8).
- Миграции `ci-kit/migrate-*`.
- Включение `allow_auto_merge` во флоте.
