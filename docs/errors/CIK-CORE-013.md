# CIK-CORE-013

Build-module test artifacts exceed a report limit.

## Cause

A java component with `test_modules` produces an artifact name longer than 128 characters (`allure-results-<component id>-<module id>`), or, with the `allure` module installed, the repository uploads more than 50 `allure-results-*` artifacts.

## Fix

Shorten the component or test module `id`, or merge test modules so fewer `results` paths upload separate artifacts.

## Example

A component with a 64-character `id` and a test module with a 64-character `id` give a 143-character artifact name; 51 test modules with `results` in a repository with the `allure` module exceed the 50-artifact limit.
