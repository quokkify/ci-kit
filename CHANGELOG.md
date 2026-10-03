# Changelog

## [3.0.1](https://github.com/quokkify/ci-kit/compare/v3.0.0...v3.0.1) (2026-10-03)


### 🐛 Bug Fixes

* **template:** keep v3 generated files lint-clean and follow the rename ([#368](https://github.com/quokkify/ci-kit/issues/368)) ([f395512](https://github.com/quokkify/ci-kit/commit/f395512952fba0381ee8beff476d7b62a08800d8))


### 🧹 Chores

* **deps:** update renovate to v44.132.4 ([#360](https://github.com/quokkify/ci-kit/issues/360)) ([a0fe427](https://github.com/quokkify/ci-kit/commit/a0fe42734dc18f50d58873bce17401394e5614a4))
* **deps:** update uv to v0.12.23 ([#359](https://github.com/quokkify/ci-kit/issues/359)) ([0f9d4b2](https://github.com/quokkify/ci-kit/commit/0f9d4b26bf38bfbff23da99446a02d6e74b4db10))

## [3.0.0](https://github.com/quokkify/ci-kit/compare/v2.25.0...v3.0.0) (2026-10-03)

<!-- project-toolkit:rich-block:start -->
<!-- project-toolkit:rich-release-notes pr=364 -->
### 🔄 Migration
For q4j, after the release that contains this change:
1. Set `allure_source: external` in `.copier-answers.yml` (update with `--data allure_source=external`).
2. Remove the hand-written `external-report` and `external-pages` jobs from `allure-report.yml`. The generated `report` job then publishes `Run tests` with history.
3. Remove the `placeholder.txt` commands from the component jobs in `validate.yml`.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
<!-- project-toolkit:rich-block:end -->

### ⚠ BREAKING CHANGES

* the repository is now quokkify/ci-kit. GitHub redirects the old name, but consumers should run copier update to rewrite uses: references and _src_path.
* **template:** the q4j_tests and q4j_tests_path questions are removed; use the quokkify/java-test-automation-template template instead.

### ✨ Features

* **template:** select the Allure result source explicitly ([#364](https://github.com/quokkify/ci-kit/issues/364)) ([c587190](https://github.com/quokkify/ci-kit/commit/c5871906ea8e46a9020ed35a6ee3c55b099b18b6))


### 🐛 Bug Fixes

* **release:** keep rich release blocks idempotent across reruns ([#363](https://github.com/quokkify/ci-kit/issues/363)) ([cff74ae](https://github.com/quokkify/ci-kit/commit/cff74ae9ea254dd0efb09ad34dbfe6a9f088d43f))
* **template:** include SpotBugs in Q4J starter ([#355](https://github.com/quokkify/ci-kit/issues/355)) ([d0c7630](https://github.com/quokkify/ci-kit/commit/d0c7630d7eb24af466e60e4a43dcbcecdd8f4003))
* **template:** write Prettier-compatible Copier answers ([#362](https://github.com/quokkify/ci-kit/issues/362)) ([725103e](https://github.com/quokkify/ci-kit/commit/725103e679c17c45afd34991bbfa187b36488c7b))


### 🧹 Chores

* **deps:** update com.github.spotbugs to v6.5.12 ([#358](https://github.com/quokkify/ci-kit/issues/358)) ([183c7a2](https://github.com/quokkify/ci-kit/commit/183c7a217ae76971cd9bb48a6dee5cf2ea3366c3))
* **deps:** update quokkify/project-toolkit to v2.25.0 ([#365](https://github.com/quokkify/ci-kit/issues/365)) ([96bdaf3](https://github.com/quokkify/ci-kit/commit/96bdaf351f114d4bbd1bc02adbaee29d15d1884f))


### ♻️ Refactoring

* rename repository to quokkify/ci-kit ([#366](https://github.com/quokkify/ci-kit/issues/366)) ([d13eaf2](https://github.com/quokkify/ci-kit/commit/d13eaf2854d27fd1dcf300d6078b791870b5e912))
* **template:** extract q4j starter to quokkify/java-test-automation-template ([#361](https://github.com/quokkify/ci-kit/issues/361)) ([55f3bdb](https://github.com/quokkify/ci-kit/commit/55f3bdbee3ec803b358c70de9287a1a61186f644))
* **template:** flatten template to template/ and add generated onboarding guide ([#357](https://github.com/quokkify/ci-kit/issues/357)) ([8e18be7](https://github.com/quokkify/ci-kit/commit/8e18be7791f2af870164a2c226e66ab189ac98d5))

## [2.25.0](https://github.com/quokkify/project-toolkit/compare/v2.24.0...v2.25.0) (2026-10-03)

<!-- project-toolkit:rich-block:start -->
<!-- project-toolkit:rich-release-notes pr=351 -->
### 🔄 Migration
Generated repositories pick up both changes through the normal template update. Custom callers can add `history-path` together with a matching `historyPath` in their own Allure config.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
<!-- project-toolkit:rich-block:end -->

### ✨ Features

* **template:** add minimal Q4J test automation starter ([#352](https://github.com/quokkify/project-toolkit/issues/352)) ([d14f202](https://github.com/quokkify/project-toolkit/commit/d14f202d7ea7feb95238e5ab37a622d693b3e0d2))


### 🐛 Bug Fixes

* **allure:** carry Allure history between report runs ([#351](https://github.com/quokkify/project-toolkit/issues/351)) ([13fbf2d](https://github.com/quokkify/project-toolkit/commit/13fbf2d019e66c93401459500d7e77e911f236ef))
* **release:** enrich only active manifest components ([#350](https://github.com/quokkify/project-toolkit/issues/350)) ([658cdba](https://github.com/quokkify/project-toolkit/commit/658cdbacac74c35d97760278db321bd16ae34df4))
* **template:** isolate Gitleaks concurrency by event ([#348](https://github.com/quokkify/project-toolkit/issues/348)) ([6b1515d](https://github.com/quokkify/project-toolkit/commit/6b1515d19313202b5d95947b8a06abd2278758fd))


### 🧹 Chores

* **deps:** update allure to v3.20.0 ([#344](https://github.com/quokkify/project-toolkit/issues/344)) ([e920453](https://github.com/quokkify/project-toolkit/commit/e920453f5be347b469da15b9e49ac1c1b032c503))
* **deps:** update quokkify/project-toolkit to v2.24.0 ([#341](https://github.com/quokkify/project-toolkit/issues/341)) ([5ad585a](https://github.com/quokkify/project-toolkit/commit/5ad585abfa935fed8e8566664d83fc0e6547b4ca))
* **deps:** update uv to v0.12.22 ([#343](https://github.com/quokkify/project-toolkit/issues/343)) ([6cf0d7d](https://github.com/quokkify/project-toolkit/commit/6cf0d7d4b0ba22d5c817fd24577d52c3b86d69db))

## [2.24.0](https://github.com/quokkify/project-toolkit/compare/v2.23.5...v2.24.0) (2026-10-01)


### ✨ Features

* **fleet:** report actual adoption and revision-bound health ([#338](https://github.com/quokkify/project-toolkit/issues/338)) ([baaa063](https://github.com/quokkify/project-toolkit/commit/baaa063bb5a3db3615f66154af74d73c4690a18f))


### 🐛 Bug Fixes

* **ci:** execute all suites and scan distributed workflows ([#336](https://github.com/quokkify/project-toolkit/issues/336)) ([bdb3f86](https://github.com/quokkify/project-toolkit/commit/bdb3f86521bec7fa2aa5b4d040d2c175e54cda8c))
* **release:** gate exact revisions and verify fleet pilots ([#337](https://github.com/quokkify/project-toolkit/issues/337)) ([f85a1e8](https://github.com/quokkify/project-toolkit/commit/f85a1e829ee6db254744cc33746590875c51a3e6))


### 🧹 Chores

* **deps:** update allure to v3.19.1 ([#333](https://github.com/quokkify/project-toolkit/issues/333)) ([e11983d](https://github.com/quokkify/project-toolkit/commit/e11983dfb024fefcc843825ba8e8e3dbdfc7c05c))
* **deps:** update quokkify/project-toolkit to v2.23.5 ([#331](https://github.com/quokkify/project-toolkit/issues/331)) ([25e366a](https://github.com/quokkify/project-toolkit/commit/25e366ab58b065fdc5bce4f798f6e93e289a6073))
* **deps:** update renovate to v44.121.4 ([#335](https://github.com/quokkify/project-toolkit/issues/335)) ([831bcb3](https://github.com/quokkify/project-toolkit/commit/831bcb387641ada1210c9d33fbe93ce4da09e52b))
* **deps:** update uv to v0.12.21 ([#334](https://github.com/quokkify/project-toolkit/issues/334)) ([3fd7d43](https://github.com/quokkify/project-toolkit/commit/3fd7d43b252112c74d2843be131b41abddfb807a))


### ♻️ Refactoring

* **allure:** reuse scoped report and Pages workflows ([#339](https://github.com/quokkify/project-toolkit/issues/339)) ([b08d360](https://github.com/quokkify/project-toolkit/commit/b08d360040ab8a1986d5eacf2ec7424922f2effd))

## [2.23.5](https://github.com/quokkify/project-toolkit/compare/v2.23.4...v2.23.5) (2026-09-29)


### 🐛 Bug Fixes

* place enriched highlights before release notes ([#327](https://github.com/quokkify/project-toolkit/issues/327)) ([8ff467e](https://github.com/quokkify/project-toolkit/commit/8ff467ed6d07410cbb3d5566a4e17c963b4a21f3))
* **renovate:** automerge toolkit docs updates ([#329](https://github.com/quokkify/project-toolkit/issues/329)) ([5c55515](https://github.com/quokkify/project-toolkit/commit/5c555154b3077fa5f17952ed1fc2a286f6a89239))


### 🧹 Chores

* **deps:** update renovate to v44.119.0 ([#328](https://github.com/quokkify/project-toolkit/issues/328)) ([0ba5bb7](https://github.com/quokkify/project-toolkit/commit/0ba5bb76827824eab7f8e3b6b5401ca916e3ff5c))

## [2.23.4](https://github.com/quokkify/project-toolkit/compare/v2.23.3...v2.23.4) (2026-09-29)


### 🐛 Bug Fixes

* **fleet:** reduce false Copier update conflicts ([#325](https://github.com/quokkify/project-toolkit/issues/325)) ([d4d16ef](https://github.com/quokkify/project-toolkit/commit/d4d16ef67385f8ee4087ad326a106eee5bcaa791))

## [2.23.3](https://github.com/quokkify/project-toolkit/compare/v2.23.2...v2.23.3) (2026-09-29)


### 🐛 Bug Fixes

* **release:** place rich notes before generated sections ([#322](https://github.com/quokkify/project-toolkit/issues/322)) ([fa44e17](https://github.com/quokkify/project-toolkit/commit/fa44e174fd8626d40258361e2571439aa5a80c39))


### 🧹 Chores

* **deps:** update renovate to v44.118.2 ([#323](https://github.com/quokkify/project-toolkit/issues/323)) ([6e58443](https://github.com/quokkify/project-toolkit/commit/6e58443badfaad342d90c92b66861cd0da69d036))

## [2.23.2](https://github.com/quokkify/project-toolkit/compare/v2.23.1...v2.23.2) (2026-09-29)


### 🐛 Bug Fixes

* **release:** keep Release Please as section owner ([#321](https://github.com/quokkify/project-toolkit/issues/321)) ([7a26210](https://github.com/quokkify/project-toolkit/commit/7a262107f5c000bb68d256cc5270a8552b288c31))
* **release:** normalize dependency chore entries ([#317](https://github.com/quokkify/project-toolkit/issues/317)) ([4ac4f3f](https://github.com/quokkify/project-toolkit/commit/4ac4f3f05ecff035b9631c896c4196aeb73fb4f0))
* **release:** normalize generated release PR body ([#318](https://github.com/quokkify/project-toolkit/issues/318)) ([e59d6ed](https://github.com/quokkify/project-toolkit/commit/e59d6ed69a4bb481285377622fa9254b79856a05))
* **release:** trigger releases for dependency updates ([#315](https://github.com/quokkify/project-toolkit/issues/315)) ([7b71157](https://github.com/quokkify/project-toolkit/commit/7b7115781d6ead2690db7c11bcce9a19490191b1))
* **template:** preserve release helper regex during rendering ([#311](https://github.com/quokkify/project-toolkit/issues/311)) ([544bd89](https://github.com/quokkify/project-toolkit/commit/544bd892f2094cc8f60570467f3b7e42fb3b29a3))


### 🧹 Chores

* **deps:** update gradle/actions action to v6.4.0 ([#314](https://github.com/quokkify/project-toolkit/issues/314)) ([af58859](https://github.com/quokkify/project-toolkit/commit/af58859259732d70156a6253593e2b763881ff35))
* **deps:** update quokkify/project-toolkit to v2.23.1 ([#313](https://github.com/quokkify/project-toolkit/issues/313)) ([1094327](https://github.com/quokkify/project-toolkit/commit/1094327cf5dde1c92f814671b7f70c2e24d87189))
* **deps:** update renovate to v44.117.2 ([#320](https://github.com/quokkify/project-toolkit/issues/320)) ([93cbca6](https://github.com/quokkify/project-toolkit/commit/93cbca66ba3eaa75fb787bfb8271e0e5f73fbb1b))
* **deps:** update uv to v0.12.20 ([#319](https://github.com/quokkify/project-toolkit/issues/319)) ([d936c9f](https://github.com/quokkify/project-toolkit/commit/d936c9faa2212d7487396272b699790f1b8fb387))

## [2.23.1](https://github.com/quokkify/project-toolkit/compare/v2.23.0...v2.23.1) (2026-09-28)


### 🐛 Bug Fixes

* detect stale release helpers on template updates ([#306](https://github.com/quokkify/project-toolkit/issues/306)) ([4ec80cc](https://github.com/quokkify/project-toolkit/commit/4ec80ccd1a07037fdee5defcfcdd0b090f669a48))

## [2.23.0](https://github.com/quokkify/project-toolkit/compare/v2.22.0...v2.23.0) (2026-09-28)

<!-- project-toolkit:rich-block:start -->
### 📦 Dependencies
- update quokkify/project-toolkit to v2.22.0 ([#286](https://github.com/quokkify/project-toolkit/pull/286) ([152728a](https://github.com/quokkify/project-toolkit/commit/152728a19f202daf14dea2027b3bb7536bcc7dde))) <!-- project-toolkit:rich-release-notes pr=286 -->
- update corepack to v0.36.0 ([#294](https://github.com/quokkify/project-toolkit/pull/294) ([7a7d1f6](https://github.com/quokkify/project-toolkit/commit/7a7d1f645b7ed01c1edab6abd8b9ad8dc44f4a49))) <!-- project-toolkit:rich-release-notes pr=294 -->
- update github/codeql-action/analyze digest to 2892aa5 ([#297](https://github.com/quokkify/project-toolkit/pull/297)) ([fabb7a8](https://github.com/quokkify/project-toolkit/commit/fabb7a8e4493d3a38831696d18fcf034c0940b71)) <!-- project-toolkit:rich-release-notes pr=297 -->
- update github/codeql-action/init digest to 2892aa5 ([#298](https://github.com/quokkify/project-toolkit/pull/298)) ([5b6ecb5](https://github.com/quokkify/project-toolkit/commit/5b6ecb5d6bdf6b1298df4764d9ae1cd71a7a2f73)) <!-- project-toolkit:rich-release-notes pr=298 -->
- update renovate to v44.115.13 ([#285](https://github.com/quokkify/project-toolkit/pull/285) ([d97449c](https://github.com/quokkify/project-toolkit/commit/d97449cd05f58fc014e2f4e8328ac1fc8d266d60)), [#287](https://github.com/quokkify/project-toolkit/pull/287) ([10fa8ba](https://github.com/quokkify/project-toolkit/commit/10fa8ba1be9bed7a61cd8dd573965d7c49e6a549)), [#288](https://github.com/quokkify/project-toolkit/pull/288) ([8a8f472](https://github.com/quokkify/project-toolkit/commit/8a8f472808bc51bfa47abbe959346c8c423e8644)), [#290](https://github.com/quokkify/project-toolkit/pull/290) ([ee3c0a5](https://github.com/quokkify/project-toolkit/commit/ee3c0a55ca49237d245bfa0552ab5db12d2ef3f5)), [#291](https://github.com/quokkify/project-toolkit/pull/291) ([64da766](https://github.com/quokkify/project-toolkit/commit/64da766cdf372a1a2ea7c17d3f69eefde05a9bdc)), [#299](https://github.com/quokkify/project-toolkit/pull/299) ([2fa2c32](https://github.com/quokkify/project-toolkit/commit/2fa2c32b911b01928c8a049c7d63c24c475b37ed))) <!-- project-toolkit:rich-release-notes pr=285 --> <!-- project-toolkit:rich-release-notes pr=287 --> <!-- project-toolkit:rich-release-notes pr=288 --> <!-- project-toolkit:rich-release-notes pr=290 --> <!-- project-toolkit:rich-release-notes pr=291 --> <!-- project-toolkit:rich-release-notes pr=299 -->
- update poetry to v2.5.1 ([#295](https://github.com/quokkify/project-toolkit/pull/295) ([386ea47](https://github.com/quokkify/project-toolkit/commit/386ea4751e1e94f1c24176b4f37bb82cd9fcd18a)), [#300](https://github.com/quokkify/project-toolkit/pull/300) ([10851f7](https://github.com/quokkify/project-toolkit/commit/10851f7fa6fc063dd2a540f02cc37be4a378424a))) [security] <!-- project-toolkit:rich-release-notes pr=295 --> <!-- project-toolkit:rich-release-notes pr=300 -->
- update uv to v0.12.19 ([#296](https://github.com/quokkify/project-toolkit/pull/296) ([e31d31f](https://github.com/quokkify/project-toolkit/commit/e31d31facee33e6a407af416e21c8b12ea343fba)), [#301](https://github.com/quokkify/project-toolkit/pull/301) ([b8c53e9](https://github.com/quokkify/project-toolkit/commit/b8c53e9088cbcf6b45967345c4f3e455fb327d15))) [security] <!-- project-toolkit:rich-release-notes pr=296 --> <!-- project-toolkit:rich-release-notes pr=301 -->
- update allure to v3.19.0 ([#304](https://github.com/quokkify/project-toolkit/pull/304) ([9ea7148](https://github.com/quokkify/project-toolkit/commit/9ea7148e51e68d886527cfcc5f2b56b80c7dee02))) <!-- project-toolkit:rich-release-notes pr=304 -->
<!-- project-toolkit:rich-release-notes pr=293 -->
#### feat(ci): centralize checkout in a shared Copier action
### Migration
Use the first released project-toolkit version containing `actions/checkout` (planned for v2.23.0). Wait until that release is published; v2.22.0 does not contain the wrapper.

1. Run the existing Copier fleet update to the new release. Generated workflows switch to the shared checkout automatically, and `.copier-answers.yml` records the matching `toolkit_version`.
2. In project-owned workflows such as `.github/workflows/docs.yml`, replace the upstream `uses` reference once. Preserve existing `with`, `id`, `if`, and permissions.

Before:

```yaml
- name: Checkout
  id: source
  uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6
  with:
    fetch-depth: 0
    persist-credentials: false
```

After, once v2.23.0 is published and recorded as `toolkit_version`:

```yaml
- name: Checkout
  id: source
  uses: quokkify/project-toolkit/actions/checkout@v2.23.0
  with:
    fetch-depth: 0
    persist-credentials: false
```

If the release version changes, use the actual published tag recorded in `.copier-answers.yml` instead. Existing `steps.source.outputs.ref` and `steps.source.outputs.commit` remain available. The wrapper preserves upstream defaults, including `persist-credentials: true` when omitted; keep an explicit `false` for read-only checkouts.

3. Run the project's CI and merge its migration PR. Start with `path-of-exile-starter`, then repeat for other projects. Review upstream v7 compatibility when migrating from v6, including runner requirements and privileged-event checkout restrictions.
4. Subsequent upstream checkout updates are handled by Renovate in project-toolkit, released with the toolkit, and delivered by the fleet updater in template-update PRs. It also updates shared-action references in project-owned workflows. Plain `copier update` only renders template-owned files. No local Renovate override is needed to discover the upstream checkout pin.
<!-- project-toolkit:rich-block:end -->

### ✨ Features

* **ci:** centralize checkout in a shared Copier action ([#293](https://github.com/quokkify/project-toolkit/issues/293)) ([097c920](https://github.com/quokkify/project-toolkit/commit/097c920d8bbf65343fa85f0e8a69687fe0d9c8ff))
* **python:** pin pip and default runtime to 3.14 ([#302](https://github.com/quokkify/project-toolkit/issues/302)) ([885db26](https://github.com/quokkify/project-toolkit/commit/885db261abb8aa76e9c77085eed3f8b1a2aed565))
* **security:** add templates and ruleset reconciler ([#280](https://github.com/quokkify/project-toolkit/issues/280)) ([5345d7e](https://github.com/quokkify/project-toolkit/commit/5345d7ed55fa08f4089ae37f40c03e4a9f545a3f))


### 🐛 Bug Fixes

* **ci:** align setup actions, caches, and project environments ([#292](https://github.com/quokkify/project-toolkit/issues/292)) ([74fd4b3](https://github.com/quokkify/project-toolkit/commit/74fd4b38573d6f7485b3733333d79ebb56397b81))
* honor config-backed single release inputs ([#305](https://github.com/quokkify/project-toolkit/issues/305)) ([c70c14a](https://github.com/quokkify/project-toolkit/commit/c70c14a8526ba1a24b3e58c9947901ad705283f5))
* **release:** compact repeated dependency updates ([#303](https://github.com/quokkify/project-toolkit/issues/303)) ([7763124](https://github.com/quokkify/project-toolkit/commit/7763124a70344838bf6c55efbfd9ddb893643b7e))

## [2.22.0](https://github.com/quokkify/project-toolkit/compare/v2.21.6...v2.22.0) (2026-09-25)

<!-- project-toolkit:rich-block:start -->
### 📦 Dependencies
- update quokkify/project-toolkit to v2.21.6 ([#277](https://github.com/quokkify/project-toolkit/pull/277)) ([a015a50](https://github.com/quokkify/project-toolkit/commit/a015a50e09c85f55fec6e8872a0583e5f77a192b)) <!-- project-toolkit:rich-release-notes pr=277 -->
- update renovate to v44.106.0 ([#278](https://github.com/quokkify/project-toolkit/pull/278)) ([1390405](https://github.com/quokkify/project-toolkit/commit/1390405c6184381cffcfdd8da72a82276050098c)) <!-- project-toolkit:rich-release-notes pr=278 -->
<!-- project-toolkit:rich-block:end -->

### ✨ Features

* require named Copier component jobs ([#283](https://github.com/quokkify/project-toolkit/issues/283)) ([285f9f4](https://github.com/quokkify/project-toolkit/commit/285f9f45b2e4fef8a0a82a5f703669b3deee218d))


### 🐛 Bug Fixes

* **fleet:** isolate targeted update concurrency ([#281](https://github.com/quokkify/project-toolkit/issues/281)) ([9e6e762](https://github.com/quokkify/project-toolkit/commit/9e6e76253f9495305b315e2e22970cd0e926a584))

## [2.21.6](https://github.com/quokkify/project-toolkit/compare/v2.21.5...v2.21.6) (2026-09-21)

<!-- project-toolkit:rich-block:start -->
### 📦 Dependencies
- update quokkify/project-toolkit to v2.21.5 ([#267](https://github.com/quokkify/project-toolkit/pull/267)) ([6041f09](https://github.com/quokkify/project-toolkit/commit/6041f09ea08ecef76d1d4c67e920cc097e6bafe8)) <!-- project-toolkit:rich-release-notes pr=267 -->
- update allure to v3.18.0 ([#268](https://github.com/quokkify/project-toolkit/pull/268)) ([1934caf](https://github.com/quokkify/project-toolkit/commit/1934cafdbb34c64c14a1ef7d6d73cbc354c56bc5)) <!-- project-toolkit:rich-release-notes pr=268 -->
- update renovate to v44.103.2 ([#270](https://github.com/quokkify/project-toolkit/pull/270)) ([3fdf97b](https://github.com/quokkify/project-toolkit/commit/3fdf97bf7bd8e4b1eefc366b57699eb60bd3d912)) <!-- project-toolkit:rich-release-notes pr=270 -->
<!-- project-toolkit:rich-block:end -->

### 🐛 Bug Fixes

* **actions:** retry transient Gradle HTTP 403 ([#276](https://github.com/quokkify/project-toolkit/issues/276)) ([d9f2f1c](https://github.com/quokkify/project-toolkit/commit/d9f2f1c7fb4bf5ce5229dc6a7ef6a3faf7112843))
* **fleet:** improve audit inventory coverage ([#246](https://github.com/quokkify/project-toolkit/issues/246)) ([8f89def](https://github.com/quokkify/project-toolkit/commit/8f89def8e60cb076a2135ad1695fc5b5038bb24e))
* **renovate:** deduplicate Allure action updates ([#272](https://github.com/quokkify/project-toolkit/issues/272)) ([e005c43](https://github.com/quokkify/project-toolkit/commit/e005c43afcf38c9481c16355ba08a9f08cbdfd01))
* **template:** harden Copier update workflow ([#273](https://github.com/quokkify/project-toolkit/issues/273)) ([c2c64ab](https://github.com/quokkify/project-toolkit/commit/c2c64ab4ad9ed69a80b62774abd0f56c3826cbd8))

## [2.21.5](https://github.com/quokkify/project-toolkit/compare/v2.21.4...v2.21.5) (2026-09-16)

<!-- project-toolkit:rich-block:start -->
### 📦 Dependencies
- update renovate to v44.82.1 ([#251](https://github.com/quokkify/project-toolkit/pull/251)) ([33ee3ec](https://github.com/quokkify/project-toolkit/commit/33ee3ec3b442653b6771c7e6d44eabee50cdbbf7)) <!-- project-toolkit:rich-release-notes pr=251 -->
- update quokkify/project-toolkit to v2.21.4 ([#252](https://github.com/quokkify/project-toolkit/pull/252)) ([cc20190](https://github.com/quokkify/project-toolkit/commit/cc201907ca7efe5155ff6c7d3672dafc707116c4)) <!-- project-toolkit:rich-release-notes pr=252 -->
- update renovate to v44.82.4 ([#253](https://github.com/quokkify/project-toolkit/pull/253)) ([3f8ecf7](https://github.com/quokkify/project-toolkit/commit/3f8ecf73fbdc733a939ecb14009e5484d763c170)) <!-- project-toolkit:rich-release-notes pr=253 -->
- update java-jdk to v25 ([#254](https://github.com/quokkify/project-toolkit/pull/254)) ([5e5cab2](https://github.com/quokkify/project-toolkit/commit/5e5cab25a18383a91386907ccb5b83c80f1ae01a)) <!-- project-toolkit:rich-release-notes pr=254 -->
- update renovate to v44.82.5 ([#255](https://github.com/quokkify/project-toolkit/pull/255)) ([ba5e350](https://github.com/quokkify/project-toolkit/commit/ba5e3508f8dbe5547c84ffe5770921f0c08e498b)) <!-- project-toolkit:rich-release-notes pr=255 -->
- update github actions non-major updates ([#256](https://github.com/quokkify/project-toolkit/pull/256)) ([4d475f2](https://github.com/quokkify/project-toolkit/commit/4d475f21b6b892c718b26426f67a7fa2f6175b68)) <!-- project-toolkit:rich-release-notes pr=256 -->
- update renovate to v44.83.0 ([#261](https://github.com/quokkify/project-toolkit/pull/261)) ([4791e2b](https://github.com/quokkify/project-toolkit/commit/4791e2bb75d2cfb14a90ee9b7de637371f63d76c)) <!-- project-toolkit:rich-release-notes pr=261 -->
- update docker/setup-buildx-action action to v4.4.1 ([#262](https://github.com/quokkify/project-toolkit/pull/262)) ([e44b0a9](https://github.com/quokkify/project-toolkit/commit/e44b0a9e056b29bd8e3f3b09eff41d20df1a1df1)) <!-- project-toolkit:rich-release-notes pr=262 -->
- update renovate to v44.94.0 ([#263](https://github.com/quokkify/project-toolkit/pull/263)) ([e8cf624](https://github.com/quokkify/project-toolkit/commit/e8cf62475271d295654517ba0ec514eb8b668665)) <!-- project-toolkit:rich-release-notes pr=263 -->
<!-- project-toolkit:rich-block:end -->

### 🐛 Bug Fixes

* default toolkit Java to 17 ([#257](https://github.com/quokkify/project-toolkit/issues/257)) ([fcd3a7a](https://github.com/quokkify/project-toolkit/commit/fcd3a7ac959c398868fbb2e893c9ba86961f2a2f))
* keep Java examples on default 17 ([#260](https://github.com/quokkify/project-toolkit/issues/260)) ([33580c5](https://github.com/quokkify/project-toolkit/commit/33580c5e14b78bd6a6f87c703c5c93bc7f86aa1d))
* keep public Java examples on Java 17 ([#259](https://github.com/quokkify/project-toolkit/issues/259)) ([00df9c7](https://github.com/quokkify/project-toolkit/commit/00df9c7e7c9656af0f609d57ed75f7793369719b))

## [2.21.4](https://github.com/quokkify/project-toolkit/compare/v2.21.3...v2.21.4) (2026-09-14)


### 🐛 Bug Fixes

* **allure:** keep Copier workflow contract mode-neutral ([#247](https://github.com/quokkify/project-toolkit/issues/247)) ([4e6dedc](https://github.com/quokkify/project-toolkit/commit/4e6dedce79e151519d286e1d35c307afc9978862))

## [2.21.3](https://github.com/quokkify/project-toolkit/compare/v2.21.2...v2.21.3) (2026-09-11)

<!-- project-toolkit:rich-block:start -->
### 📦 Dependencies
- update quokkify/project-toolkit to v2.21.2 ([#241](https://github.com/quokkify/project-toolkit/pull/241)) ([4e42096](https://github.com/quokkify/project-toolkit/commit/4e4209671fa40b6e2545f3a9581ff9f88e0232a4)) <!-- project-toolkit:rich-release-notes pr=241 -->
- update renovate to v44.80.0 ([#242](https://github.com/quokkify/project-toolkit/pull/242)) ([1437a02](https://github.com/quokkify/project-toolkit/commit/1437a028b72c7b59d2eaa200aaf12bbb616f1be7)) <!-- project-toolkit:rich-release-notes pr=242 -->
<!-- project-toolkit:rich-block:end -->

### 🐛 Bug Fixes

* **ci:** invalidate Gradle catalogs and bound recovery ([#243](https://github.com/quokkify/project-toolkit/issues/243)) ([1e44ad1](https://github.com/quokkify/project-toolkit/commit/1e44ad17b7e10a2a7ac76a5a15c41a3a8b6a04a5))

## [2.21.2](https://github.com/quokkify/project-toolkit/compare/v2.21.1...v2.21.2) (2026-09-10)

<!-- project-toolkit:rich-block:start -->
### 📦 Dependencies
- update github actions non-major updates ([#233](https://github.com/quokkify/project-toolkit/pull/233)) ([a8425f2](https://github.com/quokkify/project-toolkit/commit/a8425f283c90396381c4e1b356f0c799a0752c8d)) <!-- project-toolkit:rich-release-notes pr=233 -->
- update allure to v3.17.0 ([#234](https://github.com/quokkify/project-toolkit/pull/234)) ([83e7163](https://github.com/quokkify/project-toolkit/commit/83e71639a5ecbf5ab13e2b1e97ba9c8056f64a93)) <!-- project-toolkit:rich-release-notes pr=234 -->
- update renovate to v44.79.1 ([#235](https://github.com/quokkify/project-toolkit/pull/235)) ([35260a2](https://github.com/quokkify/project-toolkit/commit/35260a2a3a9c64fc7faa8515a56558d9df761f7a)) <!-- project-toolkit:rich-release-notes pr=235 -->
- update github/codeql-action/analyze digest to b96794f ([#236](https://github.com/quokkify/project-toolkit/pull/236)) ([fb77ec8](https://github.com/quokkify/project-toolkit/commit/fb77ec8441277ea27893f68a110be24969eef137)) <!-- project-toolkit:rich-release-notes pr=236 -->
- update github/codeql-action/init digest to b96794f ([#237](https://github.com/quokkify/project-toolkit/pull/237)) ([7706723](https://github.com/quokkify/project-toolkit/commit/7706723be734c20e6a3eb68d13399d1cc14b0dca)) <!-- project-toolkit:rich-release-notes pr=237 -->
<!-- project-toolkit:rich-block:end -->

### 🐛 Bug Fixes

* **release:** defer mixed dependency sections ([1bd7d26](https://github.com/quokkify/project-toolkit/commit/1bd7d269096cd3c338530a0df4b5de039ab1a501))
* **release:** render dependency notes as bullets ([#231](https://github.com/quokkify/project-toolkit/issues/231)) ([04a05ed](https://github.com/quokkify/project-toolkit/commit/04a05eda4e38d1ecdc5a6a00ba359b40814d9b59))

## [2.21.1](https://github.com/quokkify/project-toolkit/compare/v2.21.0...v2.21.1) (2026-09-09)

<!-- project-toolkit:rich-block:start -->
### 📦 Dependencies
<!-- project-toolkit:rich-release-notes pr=219 -->
chore(deps): update allure to v3.16.1
<!-- project-toolkit:rich-release-notes pr=220 -->
chore(deps): update copier to v9.18.2
<!-- project-toolkit:rich-release-notes pr=221 -->
chore(deps): update quokkify/project-toolkit to v2.21.0
<!-- project-toolkit:rich-release-notes pr=222 -->
chore(deps): update renovate to v44.69.13
<!-- project-toolkit:rich-release-notes pr=223 -->
chore(deps): update actions/checkout action to v7
<!-- project-toolkit:rich-release-notes pr=224 -->
chore(deps): update node to v24.21.0
<!-- project-toolkit:rich-block:end -->

### 🐛 Bug Fixes

* **fleet:** seed release manifest after copier update ([3219caa](https://github.com/quokkify/project-toolkit/commit/3219caae5280f9705b9867a115b25edfc34dd577))
* **release:** group dependency enrichment headings ([#230](https://github.com/quokkify/project-toolkit/issues/230)) ([511fff3](https://github.com/quokkify/project-toolkit/commit/511fff32c58529294e4d29467424bca0eefc3468))
* **release:** keep dependency notes visible in template ([#225](https://github.com/quokkify/project-toolkit/issues/225)) ([ab0714a](https://github.com/quokkify/project-toolkit/commit/ab0714a537b9480e8754ddbba38201128e339d28))
* **release:** migrate legacy dependency notes ([#227](https://github.com/quokkify/project-toolkit/issues/227)) ([94973f7](https://github.com/quokkify/project-toolkit/commit/94973f76667b4a5e4c3be88a50b43486c11a2758))
* **release:** preserve legacy dependency discovery ([#228](https://github.com/quokkify/project-toolkit/issues/228)) ([597983d](https://github.com/quokkify/project-toolkit/commit/597983ddd1575da39a6fb7917d15ecdd489e089a))

## [2.21.0](https://github.com/quokkify/project-toolkit/compare/v2.20.1...v2.21.0) (2026-09-09)


### ✨ Features

* **release:** enrich release notes from PR bodies ([dd35650](https://github.com/quokkify/project-toolkit/commit/dd35650cce5f5d9e4aa74d620996a9bb33eab2e0))

## [2.20.1](https://github.com/quokkify/project-toolkit/compare/v2.20.0...v2.20.1) (2026-09-02)


### 🐛 Bug Fixes

* **allure:** merge external results from a separate source directory ([#206](https://github.com/quokkify/project-toolkit/issues/206)) ([8082629](https://github.com/quokkify/project-toolkit/commit/8082629d8ccbfc16ac31cae8f3ddb8a35d83f883))

## [2.20.0](https://github.com/quokkify/project-toolkit/compare/v2.19.2...v2.20.0) (2026-09-02)


### ✨ Features

* **ci:** propagate template releases to consumers automatically ([#202](https://github.com/quokkify/project-toolkit/issues/202)) ([a8b7156](https://github.com/quokkify/project-toolkit/commit/a8b71566ff701f7df149dd3a060e61aee015bef3))


### 🐛 Bug Fixes

* **fleet:** bump digest-pinned toolkit refs with the template version ([#204](https://github.com/quokkify/project-toolkit/issues/204)) ([95c4bbd](https://github.com/quokkify/project-toolkit/commit/95c4bbd87cc7b8bcd80b47c1ad688cbb11180cec))

## [2.19.2](https://github.com/quokkify/project-toolkit/compare/v2.19.1...v2.19.2) (2026-09-02)


### 🐛 Bug Fixes

* **allure:** propagate current CLI and track Renovate updates ([#201](https://github.com/quokkify/project-toolkit/issues/201)) ([c33d542](https://github.com/quokkify/project-toolkit/commit/c33d54217b283c13fad4394e6befdf4e52da83d9))
* **renovate:** manage every executable Copier pin ([#200](https://github.com/quokkify/project-toolkit/issues/200)) ([da44951](https://github.com/quokkify/project-toolkit/commit/da44951ed110c467b011435da6e90d6160e6d94a))


### 📚 Documentation

* use centralized architecture diagram ([#198](https://github.com/quokkify/project-toolkit/issues/198)) ([2d98168](https://github.com/quokkify/project-toolkit/commit/2d9816882af46068e5bd9b9e1b6191e6b4c4c1c1))


### 🧹 Chores

* **deps:** update copier to v9.18.1 ([#195](https://github.com/quokkify/project-toolkit/issues/195)) ([f6ec162](https://github.com/quokkify/project-toolkit/commit/f6ec162ecbc5be3239395a903afbb13d11a5ce70))
* **deps:** update quokkify/project-toolkit to v2.19.1 ([#196](https://github.com/quokkify/project-toolkit/issues/196)) ([c735d8e](https://github.com/quokkify/project-toolkit/commit/c735d8eb2a3e6583f25204a55e359bc3d1e5d9ed))
* **deps:** update renovate to v44.56.1 ([#197](https://github.com/quokkify/project-toolkit/issues/197)) ([9fe83cf](https://github.com/quokkify/project-toolkit/commit/9fe83cfbd658b1fa303059af24cb8407d72c8d7a))

## [2.19.1](https://github.com/quokkify/project-toolkit/compare/v2.19.0...v2.19.1) (2026-09-01)


### 🐛 Bug Fixes

* **allure:** link the PR comment to the published report ([#194](https://github.com/quokkify/project-toolkit/issues/194)) ([bf3913b](https://github.com/quokkify/project-toolkit/commit/bf3913b605e20e0672530f75286a8b7911e0da7e))
* **fleet:** bump toolkit references in project-owned workflows ([#191](https://github.com/quokkify/project-toolkit/issues/191)) ([aa4d4e1](https://github.com/quokkify/project-toolkit/commit/aa4d4e19d6e84bfb1542c26bf84e861caf3d9039))

## [2.19.0](https://github.com/quokkify/project-toolkit/compare/v2.18.1...v2.19.0) (2026-09-01)


### ✨ Features

* **copier:** support manifest release mode ([#190](https://github.com/quokkify/project-toolkit/issues/190)) ([e5e8257](https://github.com/quokkify/project-toolkit/commit/e5e825736c71f08257a7c749821ebff7c6f7bcc2))


### 🐛 Bug Fixes

* **fleet:** let git use the token the workflow already provides ([#187](https://github.com/quokkify/project-toolkit/issues/187)) ([a00b4fa](https://github.com/quokkify/project-toolkit/commit/a00b4fad2ca34f768aaf7abc6c3fb6a5355adacb))


### 📚 Documentation

* state the permissions the fleet token actually needs ([#189](https://github.com/quokkify/project-toolkit/issues/189)) ([a7570b4](https://github.com/quokkify/project-toolkit/commit/a7570b4606e60cb971853d81c9f9c2ed569309aa))

## [2.18.1](https://github.com/quokkify/project-toolkit/compare/v2.18.0...v2.18.1) (2026-09-01)


### 🐛 Bug Fixes

* **copier:** return the Renovate config to the project that owns it ([#185](https://github.com/quokkify/project-toolkit/issues/185)) ([c070151](https://github.com/quokkify/project-toolkit/commit/c070151789d7305ca0e79b0902d0b9185fe56163))

## [2.18.0](https://github.com/quokkify/project-toolkit/compare/v2.17.0...v2.18.0) (2026-09-01)


### ✨ Features

* **copier:** let a project declare what CodeQL scans ([#182](https://github.com/quokkify/project-toolkit/issues/182)) ([72340c7](https://github.com/quokkify/project-toolkit/commit/72340c75e4e40d8e7d3ae29a835c3349421dc194))


### 🐛 Bug Fixes

* **copier:** emit a Renovate config that matches a formatter ([#183](https://github.com/quokkify/project-toolkit/issues/183)) ([c294840](https://github.com/quokkify/project-toolkit/commit/c294840c878aa87d628f28e7211abcf1f05f86aa))
* **copier:** stop template updates from overwriting a project's README ([#181](https://github.com/quokkify/project-toolkit/issues/181)) ([b4d8aec](https://github.com/quokkify/project-toolkit/commit/b4d8aec4a1aacf521ee1656a2f3a7595ce162058))

## [2.17.0](https://github.com/quokkify/project-toolkit/compare/v2.16.0...v2.17.0) (2026-09-01)


### ✨ Features

* **copier:** add a self-service template update workflow ([#179](https://github.com/quokkify/project-toolkit/issues/179)) ([7c81411](https://github.com/quokkify/project-toolkit/commit/7c814118849320dd458babb0e0935fd375c28504))
* **copier:** give project-toolkit sole ownership of template-owned pins ([#173](https://github.com/quokkify/project-toolkit/issues/173)) ([82a18e4](https://github.com/quokkify/project-toolkit/commit/82a18e43bb8e0bb4547c640f355e213e07c889e8))
* **workflows:** add the public-only Copier fleet auto-update workflow ([#162](https://github.com/quokkify/project-toolkit/issues/162)) ([44bf820](https://github.com/quokkify/project-toolkit/commit/44bf820dc0b74e97524be4c88c70842024e1a963))


### 🐛 Bug Fixes

* match Allure resolve step by action name, not pinned SHA ([#178](https://github.com/quokkify/project-toolkit/issues/178)) ([259c5a8](https://github.com/quokkify/project-toolkit/commit/259c5a8306356e402b6370c049e6b251f4208958))
* match the Allure report action by name, not by its pinned SHA ([#180](https://github.com/quokkify/project-toolkit/issues/180)) ([dee812c](https://github.com/quokkify/project-toolkit/commit/dee812c9a3ed4305332d96f2931adf700754bb5b))


### 🧹 Chores

* **deps:** update actions/github-script action to v9 ([#176](https://github.com/quokkify/project-toolkit/issues/176)) ([fab8049](https://github.com/quokkify/project-toolkit/commit/fab8049d052ca7395de8087bc7681f3117ea47b5))
* **deps:** update quokkify/allure-report-action action to v0.4.1 ([#174](https://github.com/quokkify/project-toolkit/issues/174)) ([56a5ce2](https://github.com/quokkify/project-toolkit/commit/56a5ce2634b67bc2d16cee90e7a8122d79af4074))
* **deps:** update renovate to v44.54.0 ([#175](https://github.com/quokkify/project-toolkit/issues/175)) ([de5bff6](https://github.com/quokkify/project-toolkit/commit/de5bff6e33ffe4f10fd725f90aad05895cd93250))

## [2.16.0](https://github.com/quokkify/project-toolkit/compare/v2.15.0...v2.16.0) (2026-09-01)


### ✨ Features

* **copier:** make the CodeQL workflow opt-out ([#172](https://github.com/quokkify/project-toolkit/issues/172)) ([cf34077](https://github.com/quokkify/project-toolkit/commit/cf34077bed1d76e024459600ab9f054a3945bdfe))


### 🐛 Bug Fixes

* **gitleaks:** stop fetching a ref the checkout already has ([#170](https://github.com/quokkify/project-toolkit/issues/170)) ([92b4ce6](https://github.com/quokkify/project-toolkit/commit/92b4ce6f19f7319ea90348b803399f76b9ea5262))

## [2.15.0](https://github.com/quokkify/project-toolkit/compare/v2.14.0...v2.15.0) (2026-09-01)


### ✨ Features

* **workflows:** add trusted reusable Allure publisher ([b5ecec8](https://github.com/quokkify/project-toolkit/commit/b5ecec8724d6dc86064c2741bd1292fda273e09d))


### 🧹 Chores

* **deps:** update quokkify/project-toolkit to v2.12.3 ([#163](https://github.com/quokkify/project-toolkit/issues/163)) ([a64f31d](https://github.com/quokkify/project-toolkit/commit/a64f31d1458ece4685c71a229902ee053c3dfe4f))
* **deps:** update quokkify/project-toolkit to v2.12.4 ([#166](https://github.com/quokkify/project-toolkit/issues/166)) ([7c7f2eb](https://github.com/quokkify/project-toolkit/commit/7c7f2eb6db6856d9069d696faf4738651b5af1b4))
* **deps:** update quokkify/project-toolkit to v2.14.0 ([#167](https://github.com/quokkify/project-toolkit/issues/167)) ([7bdc88c](https://github.com/quokkify/project-toolkit/commit/7bdc88c77feb91ed86b6ed97321132b65a97d960))
* **deps:** update renovate to v44.50.3 ([#168](https://github.com/quokkify/project-toolkit/issues/168)) ([be1ab05](https://github.com/quokkify/project-toolkit/commit/be1ab05837e71a5e633620860af207e864fff9b0))

## [2.14.0](https://github.com/quokkify/project-toolkit/compare/v2.13.0...v2.14.0) (2026-08-28)


### ✨ Features

* **deps:** update actions/setup-java action to v6 ([#160](https://github.com/quokkify/project-toolkit/issues/160)) ([e15bbed](https://github.com/quokkify/project-toolkit/commit/e15bbed541f0ede907009fa4e3dd886eccb8aa34))


### ⚙️ CI

* add emoji changelog sections ([#158](https://github.com/quokkify/project-toolkit/issues/158)) ([2f24d16](https://github.com/quokkify/project-toolkit/commit/2f24d1618b36fa6734b3f120f72c6c1002928f9b))


### 🧹 Chores

* **deps:** update actions/download-artifact to v8 ([#157](https://github.com/quokkify/project-toolkit/issues/157)) ([575ec41](https://github.com/quokkify/project-toolkit/commit/575ec41bacf7f499d719ebb1b6a0ad5a87640a5e))
* **deps:** update copier to v9.17.2 ([#137](https://github.com/quokkify/project-toolkit/issues/137)) ([789159a](https://github.com/quokkify/project-toolkit/commit/789159af62f9ff737f2e0d7db97bbdaed54ee1a6))
* **deps:** update github actions non-major updates ([#155](https://github.com/quokkify/project-toolkit/issues/155)) ([60aa749](https://github.com/quokkify/project-toolkit/commit/60aa74968fecc586ef6442b6e0872a2902d4be3b))
* **deps:** update renovate to v44.42.0 ([#153](https://github.com/quokkify/project-toolkit/issues/153)) ([f0b3d79](https://github.com/quokkify/project-toolkit/commit/f0b3d795ca479e3e3da2c949b2fab22f8cdae5e7))
* **deps:** update renovate to v44.42.1 ([#154](https://github.com/quokkify/project-toolkit/issues/154)) ([c0828a5](https://github.com/quokkify/project-toolkit/commit/c0828a57a517b13eb96cbd4f6e351f238c9fe1f4))
* **deps:** update renovate to v44.49.1 ([#156](https://github.com/quokkify/project-toolkit/issues/156)) ([42d7c09](https://github.com/quokkify/project-toolkit/commit/42d7c0972b198f1144aed91eac7e2718c00f80cc))

## [2.13.0](https://github.com/quokkify/project-toolkit/compare/v2.12.4...v2.13.0) (2026-08-28)


### Features

* **compose-up:** support quiet compose pulls ([#152](https://github.com/quokkify/project-toolkit/issues/152)) ([685f139](https://github.com/quokkify/project-toolkit/commit/685f139130e857513dcf3b42baa810f33010c1e2))


### Bug Fixes

* recognize custom Allure audit layouts ([#150](https://github.com/quokkify/project-toolkit/issues/150)) ([cc2cadf](https://github.com/quokkify/project-toolkit/commit/cc2cadfcb033f3ccaa2957f0f498676aba50bc25))

## [2.12.4](https://github.com/quokkify/project-toolkit/compare/v2.12.3...v2.12.4) (2026-08-27)


### Bug Fixes

* update Allure action to v0.4.1 ([#148](https://github.com/quokkify/project-toolkit/issues/148)) ([9a3c32e](https://github.com/quokkify/project-toolkit/commit/9a3c32e7e3eea9481fb0595392a108465a606025))

## [2.12.3](https://github.com/quokkify/project-toolkit/compare/v2.12.2...v2.12.3) (2026-08-25)


### Bug Fixes

* preserve trusted Allure compact comment content ([e138033](https://github.com/quokkify/project-toolkit/commit/e13803375369b08f395c43f9dd453702de8107f6))

## [2.12.2](https://github.com/quokkify/project-toolkit/compare/v2.12.1...v2.12.2) (2026-08-25)


### Bug Fixes

* **actions:** propagate Allure report v0.3.0 ([#143](https://github.com/quokkify/project-toolkit/issues/143)) ([308a02c](https://github.com/quokkify/project-toolkit/commit/308a02cd35c2f61728368643264b9b478b9e1b77))
* **ci:** add install-command for python components with allure_report ([#140](https://github.com/quokkify/project-toolkit/issues/140)) ([f6eb9ef](https://github.com/quokkify/project-toolkit/commit/f6eb9ef739255ee5bf7f66d61a9700c6496ca8af))
* pin copier fleet audit workflow ([#121](https://github.com/quokkify/project-toolkit/issues/121)) ([1f6c137](https://github.com/quokkify/project-toolkit/commit/1f6c1378f2dd7cadf32d398e4f4de5c82bf015f7))
* **validate:** handle unreadable copier.yml ([#123](https://github.com/quokkify/project-toolkit/issues/123)) ([baf0aab](https://github.com/quokkify/project-toolkit/commit/baf0aab9b5ce68a5e277df5efb56086b5e0b054a))

## [2.12.1](https://github.com/quokkify/project-toolkit/compare/v2.12.0...v2.12.1) (2026-08-11)


### Bug Fixes

* **actions:** update Allure report action ([#114](https://github.com/quokkify/project-toolkit/issues/114)) ([18d4ec6](https://github.com/quokkify/project-toolkit/commit/18d4ec65143bda06cd6629683fc806b6b824c721))

## [2.12.0](https://github.com/quokkify/project-toolkit/compare/v2.11.1...v2.12.0) (2026-08-09)


### Features

* **actions:** forward compose lifecycle hooks ([#106](https://github.com/quokkify/project-toolkit/issues/106)) ([cb0391a](https://github.com/quokkify/project-toolkit/commit/cb0391aa5546172dca8ba10c0a7a66ef9ee510e9))

## [2.11.1](https://github.com/quokkify/project-toolkit/compare/v2.11.0...v2.11.1) (2026-08-08)


### Bug Fixes

* **allure:** harden generated workflow inputs ([#99](https://github.com/quokkify/project-toolkit/issues/99)) ([0e8adfb](https://github.com/quokkify/project-toolkit/commit/0e8adfbb5cced488e281cbfe180e2a1a04f755b1))

## [2.11.0](https://github.com/quokkify/project-toolkit/compare/v2.10.1...v2.11.0) (2026-08-08)


### Features

* **allure:** support external workflow artifacts ([#98](https://github.com/quokkify/project-toolkit/issues/98)) ([cb1357e](https://github.com/quokkify/project-toolkit/commit/cb1357e29a77075d2168e2a1cbd375f0a45a1aff))


### Bug Fixes

* **fleet:** align Copier release answers ([#94](https://github.com/quokkify/project-toolkit/issues/94)) ([6b171ce](https://github.com/quokkify/project-toolkit/commit/6b171cea17b7e2d4e4798380b4a75fe36aaabb52))

## [2.10.1](https://github.com/quokkify/project-toolkit/compare/v2.10.0...v2.10.1) (2026-08-06)


### Bug Fixes

* **allure:** update report action to v0.2.1 ([#91](https://github.com/quokkify/project-toolkit/issues/91)) ([9e0c1a6](https://github.com/quokkify/project-toolkit/commit/9e0c1a62551b22b8a8b161a17d9174f7d801b3d2))

## [2.10.0](https://github.com/quokkify/project-toolkit/compare/v2.9.1...v2.10.0) (2026-08-06)


### Features

* add Copier Allure reporting to project template ([#84](https://github.com/quokkify/project-toolkit/issues/84)) ([e3e384d](https://github.com/quokkify/project-toolkit/commit/e3e384db947cbead8b79c4219d95f25bbac81a5c))

## [2.9.1](https://github.com/quokkify/project-toolkit/compare/v2.9.0...v2.9.1) (2026-08-06)


### Bug Fixes

* **allure:** update report action to v0.2.0 ([#85](https://github.com/quokkify/project-toolkit/issues/85)) ([2abc6f9](https://github.com/quokkify/project-toolkit/commit/2abc6f9b8dcdc8c5d513702c15e78ef47b6831b5))

## [2.9.0](https://github.com/quokkify/project-toolkit/compare/v2.8.2...v2.9.0) (2026-08-06)


### Features

* report Copier template inventory ([#79](https://github.com/quokkify/project-toolkit/issues/79)) ([7f874b2](https://github.com/quokkify/project-toolkit/commit/7f874b24f59aad334613223cc4b6f979770328ef))


### Bug Fixes

* canonicalize Copier template source URLs ([#71](https://github.com/quokkify/project-toolkit/issues/71)) ([388e35f](https://github.com/quokkify/project-toolkit/commit/388e35f7b7fefbe408beb8ed97dc5a4f1df9ce5d))
* preserve Copier answers formatting during audit ([#73](https://github.com/quokkify/project-toolkit/issues/73)) ([be4e244](https://github.com/quokkify/project-toolkit/commit/be4e2445139d5798b6fc677e21664aa55d728a40))

## [2.8.2](https://github.com/quokkify/project-toolkit/compare/v2.8.1...v2.8.2) (2026-08-03)


### Bug Fixes

* harden generated project automation workflows ([#69](https://github.com/quokkify/project-toolkit/issues/69)) ([10000c3](https://github.com/quokkify/project-toolkit/commit/10000c3057166ca7be5b47348acb5fe34d3fb354))

## [2.8.1](https://github.com/quokkify/project-toolkit/compare/v2.8.0...v2.8.1) (2026-08-03)


### Bug Fixes

* exclude non-consumer repositories from Copier audit ([#66](https://github.com/quokkify/project-toolkit/issues/66)) ([123167f](https://github.com/quokkify/project-toolkit/commit/123167fede66543fe4ca91d206e60a8bf3aa34ca))

## [2.8.0](https://github.com/quokkify/project-toolkit/compare/v2.7.2...v2.8.0) (2026-08-03)


### Features

* automate Copier fleet updates ([#64](https://github.com/quokkify/project-toolkit/issues/64)) ([784b675](https://github.com/quokkify/project-toolkit/commit/784b67579e9fae07ffbaf3b0eec1f557fe2aa4c1))

## [2.7.2](https://github.com/quokkify/project-toolkit/compare/v2.7.1...v2.7.2) (2026-08-02)


### Bug Fixes

* **actions:** support Allure installation tokens ([#61](https://github.com/quokkify/project-toolkit/issues/61)) ([7b7b82c](https://github.com/quokkify/project-toolkit/commit/7b7b82c1c39513b4523d61e3a186c12175cf2e20))

## [2.7.1](https://github.com/quokkify/project-toolkit/compare/v2.7.0...v2.7.1) (2026-08-02)


### Bug Fixes

* **actions:** update secure Allure reporter ([#59](https://github.com/quokkify/project-toolkit/issues/59)) ([b61eb85](https://github.com/quokkify/project-toolkit/commit/b61eb8506769dbe1e2787a0d93fcbbad8800b688))

## [2.7.0](https://github.com/quokkify/project-toolkit/compare/v2.6.0...v2.7.0) (2026-08-02)


### Features

* **actions:** add Allure report action ([#57](https://github.com/quokkify/project-toolkit/issues/57)) ([e09af98](https://github.com/quokkify/project-toolkit/commit/e09af98bff6783e81f92dc526f20290de674c266))

## [2.6.0](https://github.com/quokkify/project-toolkit/compare/v2.5.3...v2.6.0) (2026-08-02)


### Features

* **ci:** split validation jobs and add CodeQL ([#49](https://github.com/quokkify/project-toolkit/issues/49)) ([cedeea9](https://github.com/quokkify/project-toolkit/commit/cedeea98dd085ce8496202fd0eec141e28938640))
* wire gh-pages subdirectory compatibility wrapper to immutable standalone action ([#55](https://github.com/quokkify/project-toolkit/issues/55)) ([5bd0355](https://github.com/quokkify/project-toolkit/commit/5bd03558151e7d8159b94d3a3cff12642760f000))


### Bug Fixes

* **security:** resolve CodeQL validation alerts ([#51](https://github.com/quokkify/project-toolkit/issues/51)) ([a3a6978](https://github.com/quokkify/project-toolkit/commit/a3a697891447e09fc5e778a0e72c271a6a1cc8b8))

## [2.5.3](https://github.com/quokkify/project-toolkit/compare/v2.5.2...v2.5.3) (2026-08-01)


### Bug Fixes

* **actions:** authenticate gh-pages git with askpass ([#46](https://github.com/quokkify/project-toolkit/issues/46)) ([117229a](https://github.com/quokkify/project-toolkit/commit/117229addda82d2dd1a16a2d200e9aa19d0d79a7))

## [2.5.2](https://github.com/quokkify/project-toolkit/compare/v2.5.1...v2.5.2) (2026-08-01)


### Bug Fixes

* **actions:** use real token for gh-pages auth ([#44](https://github.com/quokkify/project-toolkit/issues/44)) ([c7cba31](https://github.com/quokkify/project-toolkit/commit/c7cba315692f61a4e17bd07536531a3d407c109b))

## [2.5.1](https://github.com/quokkify/project-toolkit/compare/v2.5.0...v2.5.1) (2026-08-01)


### Bug Fixes

* **actions:** pass token to gh-pages git auth ([#42](https://github.com/quokkify/project-toolkit/issues/42)) ([41b2e36](https://github.com/quokkify/project-toolkit/commit/41b2e3697f3d41905bf95e7b6b0f2a03e50ef2b6))

## [2.5.0](https://github.com/quokkify/project-toolkit/compare/v2.4.0...v2.5.0) (2026-08-01)


### Features

* **actions:** add gh-pages report retention ([#40](https://github.com/quokkify/project-toolkit/issues/40)) ([7f797ab](https://github.com/quokkify/project-toolkit/commit/7f797abd4a2efa95989fe341eba8392f76e636b3))

## [2.4.0](https://github.com/quokkify/project-toolkit/compare/v2.3.0...v2.4.0) (2026-08-01)


### Features

* **actions:** add gh-pages subdirectory deploy action ([#38](https://github.com/quokkify/project-toolkit/issues/38)) ([5aaca14](https://github.com/quokkify/project-toolkit/commit/5aaca142a43b998e0756b2995d13f4bec1bf2990))

## [2.3.0](https://github.com/quokkify/project-toolkit/compare/v2.2.0...v2.3.0) (2026-08-01)


### Features

* **renovate:** track validation dependencies ([#30](https://github.com/quokkify/project-toolkit/issues/30)) ([8707332](https://github.com/quokkify/project-toolkit/commit/870733296b04f650249b96f80f70dc11e3aa9525))


### Bug Fixes

* **renovate:** use quokkify shared preset ([#28](https://github.com/quokkify/project-toolkit/issues/28)) ([583d403](https://github.com/quokkify/project-toolkit/commit/583d40315cd5587b1570635f51a8866215d44d20))

## [2.2.0](https://github.com/quokkify/project-toolkit/compare/v2.1.0...v2.2.0) (2026-08-01)


### Features

* **copier:** support config-only projects ([#24](https://github.com/quokkify/project-toolkit/issues/24)) ([d932533](https://github.com/quokkify/project-toolkit/commit/d932533b27c3d2d1b7117cab994fce7eb6ce0379))

## [2.1.0](https://github.com/quokkify/project-toolkit/compare/v2.0.1...v2.1.0) (2026-08-01)


### Features

* **actions:** add JUnit step summary ([#13](https://github.com/quokkify/project-toolkit/issues/13)) ([b3dfca7](https://github.com/quokkify/project-toolkit/commit/b3dfca76183b306e57624885c67d8069f3e15d21))

## [2.0.1](https://github.com/quokkify/project-toolkit/compare/v2.0.0...v2.0.1) (2026-08-01)


### Bug Fixes

* **actions:** use quokkify repositories ([#11](https://github.com/quokkify/project-toolkit/issues/11)) ([63b504d](https://github.com/quokkify/project-toolkit/commit/63b504dc5f63ba3cce5a9e15837bae61155eb3cc))

## [2.0.0](https://github.com/ylazakovich/project-toolkit/compare/v1.1.1...v2.0.0) (2026-07-31)


### ⚠ BREAKING CHANGES

* Compose startup and container-health ownership moves to the pinned standalone v2.3.0 action. Legacy wait-for-health=false and show-logs-on-failure=false modes now fail closed before startup.

### Features

* delegate Compose startup to standalone health action ([68e51e0](https://github.com/ylazakovich/project-toolkit/commit/68e51e07bd48d804d6e9b7543ad830126b3a096b))

## [1.1.1](https://github.com/ylazakovich/project-toolkit/compare/v1.1.0...v1.1.1) (2026-07-31)


### Bug Fixes

* **actions:** validate Gradle wrappers ([#7](https://github.com/ylazakovich/project-toolkit/issues/7)) ([d933683](https://github.com/ylazakovich/project-toolkit/commit/d933683e8143db428888a3a0707903580c600f1a))

## [1.1.0](https://github.com/ylazakovich/project-toolkit/compare/v1.0.0...v1.1.0) (2026-07-31)


### Features

* **copier:** add shared Renovate presets ([#5](https://github.com/ylazakovich/project-toolkit/issues/5)) ([3e1f7fc](https://github.com/ylazakovich/project-toolkit/commit/3e1f7fc9a48b13ab36d79667536920d3bc618667))

## 1.0.0 (2026-07-31)


### Features

* **actions:** add reusable setup and compose primitives ([#2](https://github.com/ylazakovich/project-toolkit/issues/2)) ([6fa4b48](https://github.com/ylazakovich/project-toolkit/commit/6fa4b481e1271111b82939ca538c6554104a8e0f))
* initialize reusable project toolkit ([#1](https://github.com/ylazakovich/project-toolkit/issues/1)) ([d3798b5](https://github.com/ylazakovich/project-toolkit/commit/d3798b5b656d61e6f2e0fde4e8a62e67be9227f3))
* **release:** add Release Please driver ([#3](https://github.com/ylazakovich/project-toolkit/issues/3)) ([56590cb](https://github.com/ylazakovich/project-toolkit/commit/56590cb91bfdd92856235949c9364485593342f2))

## Changelog

Release Please maintains this file from Conventional Commits.
