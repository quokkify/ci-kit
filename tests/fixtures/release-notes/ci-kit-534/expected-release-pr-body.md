:robot: I have created a release *beep* *boop*
---


## [4.3.0](https://github.com/quokkify/ci-kit/compare/v4.2.0...v4.3.0) (2026-11-07)

<!-- project-toolkit:release-notes v2 -->
**At a glance:** 3 new · 3 fixed · 13 internal

### ✨ New

- **actions:** publish Gradle Problems Report in job summary ([#552](https://github.com/quokkify/ci-kit/pull/552))
- **allure:** expose opt-in pyramid advisory gates in reusable publisher ([#549](https://github.com/quokkify/ci-kit/pull/549))
- **core:** add PR title checklist and document branch naming rules ([#550](https://github.com/quokkify/ci-kit/pull/550))

### 🐛 Fixed

- **actions:** escape workflow commands in action error output ([#553](https://github.com/quokkify/ci-kit/pull/553))
- **allure:** fail component mode when results are not at the artifact root ([#546](https://github.com/quokkify/ci-kit/pull/546))
- **rollout:** retry transient GitHub reads in the fleet CI gate ([#545](https://github.com/quokkify/ci-kit/pull/545))

<details><summary>Internal changes (13 PRs)</summary>

**Dependencies**

- actions/setup-java v6.1.0 → v6.2.0 ([#544](https://github.com/quokkify/ci-kit/pull/544))
- allure 3.22.1 → 3.33.0 ([#543](https://github.com/quokkify/ci-kit/pull/543))
- docker/login-action v4.7.0 → v4.8.0 ([#544](https://github.com/quokkify/ci-kit/pull/544))
- github/codeql-action 9b32540 → 33d7b85 ([#544](https://github.com/quokkify/ci-kit/pull/544))
- renovate 44.190.2 → 44.221.1 ([#532](https://github.com/quokkify/ci-kit/pull/532), [#535](https://github.com/quokkify/ci-kit/pull/535)–[#541](https://github.com/quokkify/ci-kit/pull/541))
- uv 0.13.6 → 0.13.7 ([#542](https://github.com/quokkify/ci-kit/pull/542))

**Maintenance**

- **actions:** cover gradle-retry 429 exhaustion and malformed inputs ([#548](https://github.com/quokkify/ci-kit/pull/548))
- update quokkify/ci-kit to v4.2.0 ([#533](https://github.com/quokkify/ci-kit/pull/533))

</details>

---
This PR was generated with [Release Please](https://github.com/googleapis/release-please). See [documentation](https://github.com/googleapis/release-please#release-please).
