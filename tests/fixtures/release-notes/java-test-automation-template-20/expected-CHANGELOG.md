# Changelog

## [2.0.0](https://github.com/quokkify/java-test-automation-template/compare/v1.4.0...v2.0.0) (2026-11-07)

<!-- project-toolkit:release-notes v2 -->
**At a glance:** ⚠️ 1 breaking · 1 fixed · 5 updated · 29 internal

### ⚠️ Breaking changes

> [!WARNING]
> replace the q4j architecture gate with the ArchGate plugin ([#53](https://github.com/quokkify/java-test-automation-template/pull/53))
>
> Breaking change. Projects generated before 2.0.0:
>
> **After the update, the project has no architecture gate.** The migration removes the q4j gate but does not apply the ArchGate plugin. The gate stays disabled until section 3 of `docs/agents/architecture-verification.md` is done.
>
> 1. `copier update --trust --vcs-ref v2.0.0` (or later). The migration removes the old gate, and the build works again without it. Fix every `WARNING` line it prints.
> 2. Follow section 3 of `docs/agents/architecture-verification.md`: add the `archgate` version, library and plugin to the catalog; apply `alias(libs.plugins.archgate)` and the `archgate { }` block in `build.gradle`; add the two `testImplementation` lines; check that the services file exists.
> 3. Replace `verifyArchitecture` with `archgateCheck` in CI task lists.

### 🐛 Fixed

- **release:** show dependency updates in release notes ([#32](https://github.com/quokkify/java-test-automation-template/pull/32))

### ⬆️ Updated

- q4j 0.9.0 → 0.12.2 ([#18](https://github.com/quokkify/java-test-automation-template/pull/18), [#21](https://github.com/quokkify/java-test-automation-template/pull/21), [#23](https://github.com/quokkify/java-test-automation-template/pull/23), [#24](https://github.com/quokkify/java-test-automation-template/pull/24), [#26](https://github.com/quokkify/java-test-automation-template/pull/26))

<details><summary>Internal changes (29 PRs)</summary>

**Dependencies**

- actions/setup-java v6.0.1 → v6.1.0 ([#28](https://github.com/quokkify/java-test-automation-template/pull/28))
- actions/setup-python v7.0.0 → v7.1.0 ([#48](https://github.com/quokkify/java-test-automation-template/pull/48))
- checkstyle 14.3.0 → 14.4.0 ([#51](https://github.com/quokkify/java-test-automation-template/pull/51))
- com.github.spotbugs 6.5.12 → 6.5.13 ([#27](https://github.com/quokkify/java-test-automation-template/pull/27))
- gradle 9.8.0 → 9.8.2 ([#22](https://github.com/quokkify/java-test-automation-template/pull/22), [#41](https://github.com/quokkify/java-test-automation-template/pull/41))
- org.aspectj:aspectjweaver 1.9.25.1 → 1.9.26 ([#47](https://github.com/quokkify/java-test-automation-template/pull/47))
- python 3.14 → 3.15 ([#29](https://github.com/quokkify/java-test-automation-template/pull/29))

**Maintenance**

- **ci-kit:** hand off base, security, release, allure, renovate to modules ([#43](https://github.com/quokkify/java-test-automation-template/pull/43))
- **ci-kit:** hand off validate to core module ([#31](https://github.com/quokkify/java-test-automation-template/pull/31))
- **renovate:** update q4j immediately ([#17](https://github.com/quokkify/java-test-automation-template/pull/17))
- **template:** update shared project template (18 PRs, [#15](https://github.com/quokkify/java-test-automation-template/pull/15)–[#49](https://github.com/quokkify/java-test-automation-template/pull/49))

</details>

## [1.4.0](https://github.com/quokkify/java-test-automation-template/compare/v0.0.0...v1.4.0) (2026-10-01)


### 🐛 Bug Fixes

* an older release stays untouched ([#1](https://github.com/quokkify/java-test-automation-template/issues/1)) ([aaaaaaa](https://github.com/quokkify/java-test-automation-template/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa))
