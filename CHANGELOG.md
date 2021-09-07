# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0]

### Changed

- Command output now wraps separately from the prefix [#12](https://github.com/JoshKarpel/brood/pull/12).
- The process status table in the `log` renderer can now be disabled via configuration [#14](https://github.com/JoshKarpel/brood/pull/14).
- `brood run` now exits with code 0 when sent a keyboard interrupt (`Ctrl-C`) [#14](https://github.com/JoshKarpel/brood/pull/14).

### Removed

- Command output can no longer be given an overall style via configuration [#12](https://github.com/JoshKarpel/brood/pull/12).

## [0.1.0]

### Added

- Run commands concurrently based on a configuration file.
- Run non-daemonic commands with restart loops or with file watching.
- Display process status in a live-updating table, with console output streaming above it.
