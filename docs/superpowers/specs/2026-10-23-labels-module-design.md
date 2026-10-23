# Модуль `labels` — дизайн

- Статус: draft, на ревью
- PR: #467 (переделка); зависимости: quokkify/.github#23 (манифест, смёржен), quokkify/renovate-presets#63
- Опирается на: spec #6 (`2026-10-23-legacy-exit-design.md`), `docs/modules.md`
- Дата: 2026-10-23

## 1. Контекст

#467 добавляет синхронизацию меток организации: reusable `.github/workflows/labels-sync.yml` и вызывающий workflow `.github/workflows/labels.yml` у потребителя. В первой версии вызывающий workflow рендерит legacy-шаблон (`template/`). Spec #6 переводит все legacy-файлы в модули и удаляет `template/` в v4.0.0, так что новый legacy-файл сразу стал бы долгом миграции.

Раскатить новый модуль на флит сейчас нечем: `--migrate` удалён в #485, `--migrate-legacy` (spec #6) переносит только legacy-области.

## 2. Цель

1. `labels.yml` принадлежит модулю `labels`; у потребителя `.copier-answers.labels.yml` и `.ci/modules/labels.yml`. В `template/` синхронизации меток нет.
2. Общий способ поставить модуль в существующий репозиторий: `ci-kit add` и fleet `--add-module`.
3. Required checks не меняются; #467 мёржится независимо от ветки spec #6.

## 3. Не цели

- Включение `delete-other-labels` (фаза 2, после миграции на Issue Types).
- Автоматическая установка `labels` в `ci-kit migrate-legacy`.
- Изменения reusable `labels-sync.yml`, метки `area: template` у PR флита, пресета renovate-presets#63.

## 4. Модуль `labels`

| Путь в `modules/labels/template/` | Содержимое |
|---|---|
| `{{ _copier_conf.answers_file }}.jinja` | копия answers-шаблона `core` |
| `.ci/modules/labels.yml.jinja` | `schema_version: 1` |
| `.github/workflows/labels.yml.jinja` | тело из `template/.github/workflows/labels.yml.jinja` без изменений; legacy-файл удаляется |

Вопросы: только `toolkit_version` (D8 spec #6: совпадает с `core`).

| Файл | Изменение |
|---|---|
| `copier.yml` | `module.choices` += `labels`; `toolkit_version.when` += `labels` |
| `tools/ci_kit/modules.py` | `ModuleInfo.job: str \| None`; `"labels": ModuleInfo(("core",), None)` |
| `tools/ci_kit/gate.py` | модуль с `job is None` не оценивается: он не входит в `needs` `ci / gate` |
| `tools/ci_kit/schemas/labels.v1.json` | `schema_version: 1`, других ключей нет |
| `template/docs/ci-kit.md.jinja` | без строки `labels.yml` (legacy его не рендерит) |
| `scripts/validate.py` | проверка вызова `labels-sync.yml@<toolkit_version>` переезжает с legacy-сценариев на рендер модуля |

`ci-kit update`, `plan`, `doctor`, drift и fleet `update` обрабатывают модуль без изменений: они идут по answers-файлам. Проверка покрытия пресета (`scripts/validate.py`, `TEMPLATE_WORKFLOW_SOURCES`) уже читает `modules/*/template`; путь у потребителя тот же, renovate-presets#63 остаётся верным.

Workflow запускается на push в `main` по `.github/labels.yml` и самому workflow, поэтому мёрж PR установки сразу синхронизирует метки.

## 5. `ci-kit add`

`ci-kit add --module <m> --repo <path> --to <ref> [--template-src <src>] [--json <file>]`

1. Предусловия, до любых изменений:

   | Код | Условие |
   |---|---|
   | `CIK-ADD-001` | рабочее дерево не чистое |
   | `CIK-ADD-002` | модуль неизвестен (`MODULES`) или `legacy` |
   | `CIK-ADD-003` | нет answers-файла модуля из `requires`, или его `_commit` ≠ `--to` |
   | `CIK-ADD-004` | файл, который рендерит модуль, уже существует |

   Модуль уже стоит (`.copier-answers.<m>.yml` есть) — результат `installed: false`, без находок и изменений, exit 0.
2. `copier copy --trust --defaults --vcs-ref <ref> -d module=<m> -d toolkit_version=<ref> --answers-file .copier-answers.<m>.yml <src> <repo>`, один коммит `chore(ci-kit): add <m> module`.
3. `build_plan` без находок, иначе `CIK-ADD-005` и откат к исходному HEAD (включая созданные ignored-файлы).
4. JSON: `module`, `ref`, `installed`, `commits`, `findings`.

Каждый код — `tools/ci_kit/codes.py` и `docs/errors/<code>.md`.

## 6. Fleet: `--add-module`

`update_copier_fleet.py --add-module <m>` (вместе с `--dry-run` или `--write`):

| Статус | Когда |
|---|---|
| `up-to-date` | `.copier-answers.<m>.yml` уже есть |
| `needs-update-first` | `_commit` core ≠ целевой ref |
| `would-add` | dry-run, `ci-kit add` прошёл бы |
| `add-rejected` | находки `ci-kit add`; detail — коды |
| `pull-request` | ветка `ci-kit/add-<m>`, PR с меткой `area: template` (fallback без метки как у template update) |

Несовместим с `--auto-merge` и `--merge-ready`. Существующий PR на ветке обновляется, а не дублируется.

`rollout_copier_fleet.py --add-module <m>` использует текущее pilot-кольцо и `verify_pilot`; пилот принимается со статусами `up-to-date` и `pull-request`, как сейчас.

## 7. Согласование со spec #6

Spec #6 выполняется параллельно на `feat/legacy-exit`. Общие интерфейсы:

- `ModuleInfo.job = None` и пропуск в `gate.py` — используются модулями `release`, `security`, `allure`, `renovate`.
- `update_order` (задача 4 spec #6) включает `labels` в фиксированный порядок.
- `copier.yml` `module.choices` и `toolkit_version.when` — конфликт слияния тривиален.

Раскатка `labels` — последний шаг после раскатки v3.10.0 (spec #6 §5.4): релиз с #467, dry-run `--add-module labels` по всему флиту, pilot-кольцо, остальные.

## 8. Тесты

- Рендер модуля: набор файлов, `labels-sync.yml@<toolkit_version>`, actionlint.
- `gate`: модуль без job не требует job в `needs` и не роняет gate.
- `ci-kit add`: каждый `CIK-ADD-*`; повторный запуск — `installed: false`; успешная установка на фикстуре `core` с настоящим copier (один коммит, `build_plan` чист); откат при `CIK-ADD-005`.
- Fleet: каждый статус таблицы §6; PR-ветка и метка; несовместимость с `--auto-merge`; rollout принимает пилота.
- Полная проверка: `scripts/run_test_suites.py`, `scripts/validate.py`, `scripts/validate_modules_fixture.py`.
