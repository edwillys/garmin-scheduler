# AGENTS

This repository contains small Python CLIs that talk to Garmin Connect (via `garth`) and Google Drive.

## What this repo does

- `sync_activity.py`: downloads recent Garmin activities and syncs their JSON to Google Drive.
- `sync_workouts.py`: creates Garmin workouts and schedules them on the Garmin calendar from YAML plans (local file or Google Drive).
- `schedule_workout.py`: schedules an existing Garmin workout by name/id.
- `generate_garmin_session.py`: helper to generate a `GARMIN_SESSION` secret.

## CLI conventions

- Prefer read-only behavior for listing flags (e.g. `--list`, `--list-schedule`).
- Avoid side effects when the user is asking for help/usage: `--help` should work without requiring env vars.
- When adding new flags, keep defaults safe and predictable.

## Secrets / configuration

- `GARMIN_SESSION`: base64 tar of a `.garth` directory.
- Google Drive env vars are required only for Drive actions.

## Development

- After changes, run formatting + lint + tests:
	- `python -m black .`
	- `python -m flake8 .`
	- `python -m unittest discover -s tests -p "test_*.py"`
