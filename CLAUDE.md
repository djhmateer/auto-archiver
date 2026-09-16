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