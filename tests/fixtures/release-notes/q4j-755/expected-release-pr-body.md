:robot: I have created a release *beep* *boop*
---


## [0.13.0](https://github.com/quokkify/q4j/compare/v0.12.2...v0.13.0) (2026-11-07)

<!-- project-toolkit:release-notes v2 -->
**At a glance:** ⚠️ 1 breaking · 42 internal

### ⚠️ Breaking changes

> [!WARNING]
> **architecture:** replace the architecture module with the ArchGate plugin ([#801](https://github.com/quokkify/q4j/pull/801))
>
> `dev.quokkify:architecture` is no longer published. `HttpStatusConstantRule` moved from `dev.quokkify:rest-assured` to `dev.quokkify:archgate-rules`. The Copier template migrates in parallel in quokkify/java-test-automation-template#50 and no longer needs the q4j `architecture` artifact.

<details><summary>Internal changes (42 PRs)</summary>

**Dependencies**

- com.github.spotbugs 6.5.12 → 6.5.13 ([#759](https://github.com/quokkify/q4j/pull/759))
- com.github.spotbugs:spotbugs-annotations 4.10.4 → 4.10.5 ([#754](https://github.com/quokkify/q4j/pull/754))
- curlimages/curl 8.22.0 → 8.23.0 ([#760](https://github.com/quokkify/q4j/pull/760))
- gradle 9.8.1 → 9.8.2 ([#794](https://github.com/quokkify/q4j/pull/794))
- mongo 9.0.2 → 9.0.4 ([#767](https://github.com/quokkify/q4j/pull/767), [#768](https://github.com/quokkify/q4j/pull/768))
- nginx 1.31.6 → 1.31.8 ([#758](https://github.com/quokkify/q4j/pull/758), [#793](https://github.com/quokkify/q4j/pull/793))
- rabbitmq 4.3.6-management → 4.4.0-management ([#796](https://github.com/quokkify/q4j/pull/796))
- reportportal/migrations, reportportal/service-api 5.15.4 → 5.15.5 ([#762](https://github.com/quokkify/q4j/pull/762), [#764](https://github.com/quokkify/q4j/pull/764))
- reportportal/service-authorization, reportportal/service-index 5.15.1 → 5.15.2 ([#757](https://github.com/quokkify/q4j/pull/757), [#763](https://github.com/quokkify/q4j/pull/763))
- reportportal/service-auto-analyzer 5.15.5 → 5.15.6 ([#765](https://github.com/quokkify/q4j/pull/765))
- reportportal/service-jobs 5.15.2 → 5.15.3 ([#766](https://github.com/quokkify/q4j/pull/766))
- rest.assured 6.1.0 → 6.2.0 ([#770](https://github.com/quokkify/q4j/pull/770))
- selenide 7.19.0 → 7.19.1 ([#795](https://github.com/quokkify/q4j/pull/795))
- selenium/hub, selenium/node-docker 4.50.0-20261010 → 4.51.0-20261010 ([#761](https://github.com/quokkify/q4j/pull/761))
- selenium/standalone-chromium 2bc5a3e → ba9dde7 ([#761](https://github.com/quokkify/q4j/pull/761))
- traefik v3.7.14 → v3.7.15 ([#769](https://github.com/quokkify/q4j/pull/769))

**Maintenance**

- **ci-kit:** hand off base, security, release, allure, renovate to modules ([#788](https://github.com/quokkify/q4j/pull/788))
- **ci-kit:** hand off validate to core module ([#781](https://github.com/quokkify/q4j/pull/781))
- **ci-kit:** let Prettier check codeql.yml again ([#790](https://github.com/quokkify/q4j/pull/790))
- **ci-kit:** prepare for the legacy template exit ([#787](https://github.com/quokkify/q4j/pull/787))
- **ci-kit:** read the project contract from the module answers ([#791](https://github.com/quokkify/q4j/pull/791))
- **modules:** update q4j module documentation to v0.12.2 ([#756](https://github.com/quokkify/q4j/pull/756))
- **template:** update shared project template (15 PRs, [#772](https://github.com/quokkify/q4j/pull/772)–[#799](https://github.com/quokkify/q4j/pull/799))
- **validate:** drop placeholder language components ([#779](https://github.com/quokkify/q4j/pull/779))
- **validate:** prepare validate for the ci-kit modules migration ([#778](https://github.com/quokkify/q4j/pull/778))

</details>

---
This PR was generated with [Release Please](https://github.com/googleapis/release-please). See [documentation](https://github.com/googleapis/release-please#release-please).
