# Test automation

Standalone Java 21 tests using q4j configuration and TestNG. The starter demonstrates configuration wiring; replace it with your API, UI, or integration checks. Install JDK 21, then compile and check the starter:

```sh
./gradlew assemble testClasses checkstyleMain checkstyleTest spotbugsMain spotbugsTest
```

Checkstyle uses q4j’s rules from `tools/checkstyle/`, with XML reports in CI and HTML locally. SpotBugs uses maximum effort, default confidence, and q4j’s exclusions from `tools/spotbugs/excludeFilter.xml`; it produces XML and HTML reports.

Run tests explicitly when ready:

```sh
./gradlew test
TEST_MESSAGE="Environment override" ./gradlew test
./gradlew -DTEST_MESSAGE="System property override" test
```

`example.TestConfig` loads system properties, environment variables, and `src/test/resources/test.properties` in that order. Use `ConfigRegistry.get(TestConfig.class)` to access it.

Add the q4j modules your tests need from the [module catalog](https://github.com/quokkify/q4j#-module-catalog) to `gradle/libs.versions.toml` using the existing q4j version reference, then declare them in `gradle/dependencies.gradle`. The config module brings core transitively.

The build, Gradle scripts, version catalog, wrapper, Checkstyle rules, configuration, and starter test belong to your project and are preserved by Copier updates. This README is maintained by project-toolkit. Add consumer CI separately using the compile command above and, when appropriate, the test command.

Existing starters keep their project-owned build and configuration during Copier updates. To add SpotBugs, add the `spotbugs-plugin` version and `spotbugs` plugin alias from the current template catalog, apply `alias(libs.plugins.spotbugs)` in `build.gradle`, and merge the SpotBugs block and task reports from `gradle/code-analysis.gradle`. Copy the current `tools/spotbugs/excludeFilter.xml` if it was not generated.
