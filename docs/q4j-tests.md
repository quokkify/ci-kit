# q4j test starter

Set `q4j_tests: true` in your Copier answers to generate `test-automation/`. It is disabled by default. Set `q4j_tests_path` to another dedicated root folder, such as `qa`, if preferred. Paths, traversal, and reserved source or build directories are rejected.

The starter is a standalone Gradle project with Java 21, `dev.quokkify:config` (including core transitively), and plain TestNG. It follows q4j’s Groovy layout: `build.gradle` applies compilation, dependencies, test, and code analysis scripts from `gradle/`, while `gradle/libs.versions.toml` owns dependency versions. It includes one configuration interface, a properties file, and one small test. Choose additional dependencies from the [q4j module catalog](https://github.com/quokkify/q4j#-module-catalog).

From `test-automation/`, compile and run Checkstyle without running tests:

```sh
./gradlew assemble testClasses checkstyleMain checkstyleTest
```

Run tests explicitly with `./gradlew test`. Override `TEST_MESSAGE` using an environment variable or `./gradlew -DTEST_MESSAGE="Custom message" test`. System properties take precedence over environment variables, then classpath properties.

Copier preserves the generated build, wrapper, sources, and configuration once they exist. The toolkit pins released dependency versions in `gradle/libs.versions.toml` and maintains q4j, TestNG, Checkstyle, and the Gradle wrapper through native Renovate managers. After generation, your repository’s Renovate Java preset maintains those dependencies; Copier updates preserve your build and wrapper. The starter README remains toolkit-owned and updateable. `q4j_installed_path` in the saved answers is toolkit-owned metadata recording the installed folder. Once enabled, Copier rejects disabling the option or changing the directory name to protect existing project files. Migrate or remove the test project manually and deliberately reconcile its saved answers before applying a different topology.

The option adds the Java Renovate preset and Java CodeQL language to inferred defaults. Explicit preset or language lists remain authoritative. No consumer CI job is generated. Add the compile command above to your own CI with JDK 21, and decide when to run tests.

Toolkit validation renders the working template in an isolated directory and runs `assemble testClasses checkstyleMain checkstyleTest` only:

```sh
python scripts/validate_q4j_fixture.py --static
python scripts/validate_q4j_fixture.py
```

The static command checks default omission, opt-in generation, and preservation of project-owned files on Copier update. Compilation additionally needs JDK 21 and network access for the pinned Gradle distribution and Maven dependencies.
