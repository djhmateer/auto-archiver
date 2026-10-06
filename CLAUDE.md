# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Auto Archiver is a Python tool for automatically archiving web content (social media posts, videos, images, webpages) in a secure and verifiable way. This is a fork of the original Bellingcat Auto Archiver with additional features including Uwazi integration, Facebook crawling, and various customizations.

## Architecture

The project follows a modular pipeline architecture:

1. **Feeders** - Input sources (CSV, Google Sheets, CLI, etc.) located in `src/auto_archiver/modules/`
2. **Extractors** - Content extractors for different platforms (Instagram, Twitter, Telegram, etc.)
3. **Enrichers** - Add metadata, hashes, timestamps to archived content
4. **Databases** - Track archiving status (CSV, Console, API, Google Sheets)
5. **Storages** - Save content (Local, S3, Google Drive)
6. **Formatters** - Format output (HTML, JSON)

Configuration is done via YAML files typically stored in `secrets/` directory.


## Key Files and Directories

- `src/auto_archiver/` - Main source code
  - `core/orchestrator.py` - Main orchestration logic
  - `modules/` - All feeder, extractor, enricher, database, storage, and formatter modules
  - `utils/` - Utility functions
  - `uwazi_api/` - Uwazi integration
- `secrets/` - Configuration files and credentials (not in git)
  - `orchestration*.yaml` - Configuration files for different archiving workflows
  - `service_account*.json` - Google service account credentials
  - `*.session` - Telegram session files
  - `*_cookies.txt` - Cookie files for authentication
- `tests/` - Test suite
- `pyproject.toml` - Project dependencies and configuration

## Module Development

New modules should follow the existing pattern:
1. Create a directory in `src/auto_archiver/modules/`
2. Include `__manifest__.py` with module metadata
3. Implement the appropriate base class (Feeder, Extractor, Enricher, etc.)
4. Add configuration handling

## Investigating Production Errors

When asked to look at the prod logs, query the PostgreSQL database `aa_logs` first. The `aa-logs` project
(`/home/dave/code/aa-logs`) imports every log line, at all levels, from the production servers into it:
- Connect with `PGPASSWORD=password psql -h localhost -U bob -d aa_logs`
- `logs` (`server_id` 11-15, `timestamp` TIMESTAMPTZ, `log_level`, `message`) - one row per log entry, all levels
  (DEBUG/INFO/SUCCESS/WARNING/ERROR), multi-line entries kept together
- `reboot` (`server_id`, `rebooted_at`) - daily reboot times; `log_files_parsed` - which files were imported
- Server log times are UTC; compare in UTC (e.g. `timestamp >= '2026-10-03 08:00+00'`)
- `message` keeps the module prefix (e.g. `auto_archiver.modules.s3_storage.s3_storage:uploadf:58 - Uploading ...`),
  so match with `LIKE '%Uploading %'`, not `LIKE 'Uploading %'`
- The data is only as fresh as the last `aa-logs` import, so check `max(timestamp)` per server.
  Don't run the import yourself; ask the user.

The same log files are also mirrored at `/mnt/t/aa-dashboard-log-import/<server_number>/` (one subfolder per
production server, e.g. `11`-`15`). Use them for anything not in the database. Each folder contains:
- `1debug.log` - the current, full DEBUG-level log (large, tens of MB)
- `3success.log` / `4warning.log` - filtered current-level logs
- rotated/archived copies named `1debug.<rotation-start-timestamp>.log` for older periods

The DEBUG-level detail around a WARNING/ERROR line (e.g. surrounding requests, timings, retries) often
gives much better context for root-causing an issue than the bare error line alone - query the surrounding
rows in `aa_logs` (or grep the relevant server's `1debug.log`) for the timestamp/module/request in question
rather than relying only on what was pasted into the conversation.

## Testing

- Run tests with `poetry run pytest -m "not download"`.
- Never run the live tests (`-m download`, or plain `pytest`, which includes them), and don't offer to run them.
- Never run anything by hand (yt-dlp, browsertrix-crawler, curl, scripts) that loads cookies or sessions from
  `secrets/` (`*_cookies.txt`, `profile.tar.gz`, `*.session`) against real sites.
- Why: sending real cookies outside production can trip bot detectors and get the accounts banned.
- Never run anything against a real site (browsertrix-crawler, yt-dlp, curl, scripts, a docker crawl), even
  without cookies, unless the user explicitly says to run it. Agreeing to "try" an approach is not permission
  to run it: propose the exact command and wait for a go-ahead.

## Version Control

- Never run `git commit` or `git push` (or any equivalent, e.g. via the `cp` skill). The user handles all commits and pushes to the remote manually.

## Important Customizations in This Fork

- Uwazi integration for content management
- Enhanced Facebook crawling capabilities
- Spreadsheet cell overwriting allowed
- Hardcoded log file locations (`logs/1debug.log`, etc.)
- WACZ image size filtering
- Fixed metadata.json filename for automation
- Updates disabled to prevent production issues