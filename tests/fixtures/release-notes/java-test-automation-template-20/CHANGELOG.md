# Changelog

## [2.0.0](https://github.com/quokkify/java-test-automation-template/compare/v1.4.0...v2.0.0) (2026-11-07)


<!-- project-toolkit:rich-block:start -->
### 🔄 Migration
<!-- project-toolkit:rich-release-notes pr=53 -->
Breaking change. Projects generated before 2.0.0:

**After the update, the project has no architecture gate.** The migration removes the q4j gate but does not apply the ArchGate plugin. The gate stays disabled until section 3 of `docs/agents/architecture-verification.md` is done.

1. `copier update --trust --vcs-ref v2.0.0` (or later). The migration removes the old gate, and the build works again without it. Fix every `WARNING` line it prints.
2. Follow section 3 of `docs/agents/architecture-verification.md`: add the `archgate` version, library and plugin to the catalog; apply `alias(libs.plugins.archgate)` and the `archgate { }` block in `build.gradle`; add the two `testImplementation` lines; check that the services file exists.
3. Replace `verifyArchitecture` with `archgateCheck` in CI task lists.
<!-- project-toolkit:rich-block:end -->

### ⚠ BREAKING CHANGES

* gradle/architecture.gradle, tools/architecture/, architecture.packages and the verifyArchitecture task are removed. Run `copier update --trust`: the 2.0.0 Copier migration removes the `apply from: 'gradle/architecture.gradle'` line, the q4j-architecture and unused log4j catalog entries and the architecture.* properties. Then follow section 3 of docs/agents/architecture-verification.md to apply the ArchGate plugin, and replace verifyArchitecture with archgateCheck in CI.

### ✨ Features

* replace the q4j architecture gate with the ArchGate plugin ([#53](https://github.com/quokkify/java-test-automation-template/issues/53)) ([cd3b8b9](https://github.com/quokkify/java-test-automation-template/commit/cd3b8b9195d9c5b03f1c670ed62323043cbec8fc))


### 🐛 Bug Fixes

* **deps:** update q4j to v0.10.0 ([#18](https://github.com/quokkify/java-test-automation-template/issues/18)) ([f0685b9](https://github.com/quokkify/java-test-automation-template/commit/f0685b960f53d33ba12ea736af8a5cf862ccc71b))
* **deps:** update q4j to v0.11.0 ([#21](https://github.com/quokkify/java-test-automation-template/issues/21)) ([20cae04](https://github.com/quokkify/java-test-automation-template/commit/20cae0455982eacd86c058647e15d19ed3e09d7a))
* **deps:** update q4j to v0.12.0 ([#23](https://github.com/quokkify/java-test-automation-template/issues/23)) ([2d0f3de](https://github.com/quokkify/java-test-automation-template/commit/2d0f3de675737c0148fc01ac5d964faf3aed370d))
* **deps:** update q4j to v0.12.1 ([#24](https://github.com/quokkify/java-test-automation-template/issues/24)) ([b460807](https://github.com/quokkify/java-test-automation-template/commit/b4608074027a1fd40bfae845d4de4a4f34bec4ce))
* **deps:** update q4j to v0.12.2 ([#26](https://github.com/quokkify/java-test-automation-template/issues/26)) ([9241d57](https://github.com/quokkify/java-test-automation-template/commit/9241d57023002b088a697d21275d8c6bb49347b3))
* **release:** show dependency updates in release notes ([#32](https://github.com/quokkify/java-test-automation-template/issues/32)) ([1b09765](https://github.com/quokkify/java-test-automation-template/commit/1b09765efc38e8ac0febce8a5cc8e5fb96d5e160))


### 📦 Dependencies

* **deps:** update actions/setup-java action to v6.1.0 ([#28](https://github.com/quokkify/java-test-automation-template/issues/28)) ([abe7647](https://github.com/quokkify/java-test-automation-template/commit/abe7647f2251d175e3ec9ee3022eb386b982ce7a))
* **deps:** update actions/setup-python action to v7.1.0 ([#48](https://github.com/quokkify/java-test-automation-template/issues/48)) ([f84d785](https://github.com/quokkify/java-test-automation-template/commit/f84d7853fa2467c27ac7f03177159713102f0ae8))
* **deps:** update checkstyle to v14.4.0 ([#51](https://github.com/quokkify/java-test-automation-template/issues/51)) ([cdf1620](https://github.com/quokkify/java-test-automation-template/commit/cdf1620bd9729b0ee724684dacee801fca29c715))
* **deps:** update com.github.spotbugs to v6.5.13 ([#27](https://github.com/quokkify/java-test-automation-template/issues/27)) ([14f5654](https://github.com/quokkify/java-test-automation-template/commit/14f56544b7b722086f8056e98c64478e20ae05d8))
* **deps:** update gradle to v9.8.1 ([#22](https://github.com/quokkify/java-test-automation-template/issues/22)) ([aea4985](https://github.com/quokkify/java-test-automation-template/commit/aea4985a2f5718dd3fe4800512fe8b38933d4ddb))
* **deps:** update gradle to v9.8.2 ([#41](https://github.com/quokkify/java-test-automation-template/issues/41)) ([b06acdd](https://github.com/quokkify/java-test-automation-template/commit/b06acddb2babcbda4cb02a6686f46438a60ee122))
* **deps:** update org.aspectj:aspectjweaver to v1.9.26 ([#47](https://github.com/quokkify/java-test-automation-template/issues/47)) ([b52dec9](https://github.com/quokkify/java-test-automation-template/commit/b52dec98e9aea1bef2cadacb26df622bf8032c0b))
* **deps:** update python to 3.15 ([#29](https://github.com/quokkify/java-test-automation-template/issues/29)) ([5eebbff](https://github.com/quokkify/java-test-automation-template/commit/5eebbff15b82f468337afec5ea03960f31b71e99))


### 🧹 Chores

* **ci-kit:** hand off base, security, release, allure, renovate to modules ([#43](https://github.com/quokkify/java-test-automation-template/issues/43)) ([9e68893](https://github.com/quokkify/java-test-automation-template/commit/9e6889390dff27aec40f5399d602ea965b322f8a))
* **ci-kit:** hand off validate to core module ([#31](https://github.com/quokkify/java-test-automation-template/issues/31)) ([a4a206a](https://github.com/quokkify/java-test-automation-template/commit/a4a206ac673402d564537ab58a0871c37c918dee))
* **renovate:** update q4j immediately ([#17](https://github.com/quokkify/java-test-automation-template/issues/17)) ([9819c0c](https://github.com/quokkify/java-test-automation-template/commit/9819c0c7d6452fbd7d6c190f1d459ff8a30a4b36))
* **template:** update shared project template ([#15](https://github.com/quokkify/java-test-automation-template/issues/15)) ([e9ee66c](https://github.com/quokkify/java-test-automation-template/commit/e9ee66c5617cfd880066475987f593d2187e631f))
* **template:** update shared project template ([#16](https://github.com/quokkify/java-test-automation-template/issues/16)) ([eacfbee](https://github.com/quokkify/java-test-automation-template/commit/eacfbeef0d34e6701bb79792ccef64aae6484ab7))
* **template:** update shared project template ([#19](https://github.com/quokkify/java-test-automation-template/issues/19)) ([beeb7d2](https://github.com/quokkify/java-test-automation-template/commit/beeb7d2d9b39873f3ac20811e8364f1c04a4a511))
* **template:** update shared project template ([#25](https://github.com/quokkify/java-test-automation-template/issues/25)) ([b88095a](https://github.com/quokkify/java-test-automation-template/commit/b88095aac8053223e1659759f550e87ce811209b))
* **template:** update shared project template ([#30](https://github.com/quokkify/java-test-automation-template/issues/30)) ([befcbf6](https://github.com/quokkify/java-test-automation-template/commit/befcbf6e54ce87ab9c0eae395eb2040c4147819f))
* **template:** update shared project template ([#33](https://github.com/quokkify/java-test-automation-template/issues/33)) ([94a18a2](https://github.com/quokkify/java-test-automation-template/commit/94a18a2f62e1a96472ac93b6be9212afc451b618))
* **template:** update shared project template ([#34](https://github.com/quokkify/java-test-automation-template/issues/34)) ([b2952f4](https://github.com/quokkify/java-test-automation-template/commit/b2952f4c1640658b23f11d387f7368a26b53ae7f))
* **template:** update shared project template ([#35](https://github.com/quokkify/java-test-automation-template/issues/35)) ([b2f86bf](https://github.com/quokkify/java-test-automation-template/commit/b2f86bf6f321596cb5c9a4fa4bf16e90eb5e47ba))
* **template:** update shared project template ([#36](https://github.com/quokkify/java-test-automation-template/issues/36)) ([7807e28](https://github.com/quokkify/java-test-automation-template/commit/7807e28b7862fbf78408b5360d063e5b1186c07e))
* **template:** update shared project template ([#37](https://github.com/quokkify/java-test-automation-template/issues/37)) ([97903e8](https://github.com/quokkify/java-test-automation-template/commit/97903e8159e14c6b34469c92d9d505ed454f568b))
* **template:** update shared project template ([#38](https://github.com/quokkify/java-test-automation-template/issues/38)) ([3d65aef](https://github.com/quokkify/java-test-automation-template/commit/3d65aef63882a1b998ce094d1e4ae6c6cbf878e3))
* **template:** update shared project template ([#39](https://github.com/quokkify/java-test-automation-template/issues/39)) ([a3ce443](https://github.com/quokkify/java-test-automation-template/commit/a3ce443a7c1642de00c801b9d4c89216216a0b7e))
* **template:** update shared project template ([#40](https://github.com/quokkify/java-test-automation-template/issues/40)) ([45d7e0c](https://github.com/quokkify/java-test-automation-template/commit/45d7e0c898075f9239483a012335bc8e665338ac))
* **template:** update shared project template ([#42](https://github.com/quokkify/java-test-automation-template/issues/42)) ([1101304](https://github.com/quokkify/java-test-automation-template/commit/1101304840215e84525d5a99f2aad8f43e3849a4))
* **template:** update shared project template ([#44](https://github.com/quokkify/java-test-automation-template/issues/44)) ([7565f1c](https://github.com/quokkify/java-test-automation-template/commit/7565f1c3d49e423f50ce8a8f4804bd14da3e46db))
* **template:** update shared project template ([#45](https://github.com/quokkify/java-test-automation-template/issues/45)) ([69a0883](https://github.com/quokkify/java-test-automation-template/commit/69a08831300e2a9c8449e20946a80c0b09516fa0))
* **template:** update shared project template ([#46](https://github.com/quokkify/java-test-automation-template/issues/46)) ([c0ef198](https://github.com/quokkify/java-test-automation-template/commit/c0ef198d2e2c4dd1c433e8b67de76ac025769bdf))
* **template:** update shared project template ([#49](https://github.com/quokkify/java-test-automation-template/issues/49)) ([bb6b620](https://github.com/quokkify/java-test-automation-template/commit/bb6b6202a11b97f8ad0367038fd00c09d909e5e2))

## [1.4.0](https://github.com/quokkify/java-test-automation-template/compare/v0.0.0...v1.4.0) (2026-10-01)


### 🐛 Bug Fixes

* an older release stays untouched ([#1](https://github.com/quokkify/java-test-automation-template/issues/1)) ([aaaaaaa](https://github.com/quokkify/java-test-automation-template/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa))
