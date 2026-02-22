# garmin-scheduler

Uploads Garmin activities to Google Drive on a schedule.

This repo also supports creating and scheduling Garmin workouts from a plan file.
The workflow is inspired by: https://github.com/yeekang-0311/garmin_planner

**Configuration (non-secret)**

Edit [sync_settings.toml](sync_settings.toml):

- `sync.activity_type`: Garmin activity type key to sync (e.g. `lap_swimming`, `swimming`, `running`).
- `sync.num_last_activities`: how many recent activities to inspect.
- `sync.detail_level`: `summary` or `detailed` (includes laps/splits/metrics when available).

Workout import settings:

- `workouts.deleteSameNameWorkout`: if true, deletes existing workouts with the same name before creating.

**Secrets / env vars**

Required:

- `GARMIN_SESSION`: base64 encoded tar of `.garth` (see below)
- `GDRIVE_FOLDER_ID_SWIMMING_DATA`: Drive folder id where activity JSON files are stored
- `GDRIVE_FOLDER_ID_WORKOUTS`: Drive folder id containing workout plan YAML files
- `GDRIVE_CREDENTIALS`: Google credentials JSON (either OAuth client JSON or service account JSON)

OAuth (recommended for personal My Drive) uses:

- `GDRIVE_TOKEN_JSON`: contents of the OAuth token JSON (from a local run)

Service account note:

- Service accounts cannot upload to **My Drive** because they have no storage quota.
  Use a **Shared Drive** instead, or use OAuth.

## Install

`pip install .`

Dev tools:

`pip install .[dev]`

## Local OAuth bootstrap (one-time)

1) In Google Cloud Console:
   - Enable **Google Drive API**.
   - Create an OAuth Client ID of type **Desktop app**.
   - Download the client JSON.
2) Set `GDRIVE_CREDENTIALS` to the downloaded client JSON (as a JSON string).
3) Run `python sync_activity.py` once locally.
   - A browser window opens for consent.
   - The script prints a JSON token; copy it into `GDRIVE_TOKEN_JSON`.

## sync_activity.py

Sync mode (default): fetch recent activities and upload JSON to Drive:

- `python sync_activity.py`

List mode (read-only; does not touch Drive):

- `python sync_activity.py --list`
- `python sync_activity.py --list --start-date 2026-02-01 --end-date 2026-02-21`

Date range filtering also applies to sync mode:

- `python sync_activity.py --start-date 2026-02-01 --end-date 2026-02-21`

## GitHub Actions (no browser)

Actions cannot complete the interactive OAuth step, so it must use an existing refresh token.

Add these repository secrets:

- `GARMIN_SESSION`
- `GDRIVE_FOLDER_ID_SWIMMING_DATA`
- `GDRIVE_CREDENTIALS` (OAuth client JSON)
- `GDRIVE_TOKEN_JSON` (OAuth token JSON)

The workflow in [.github/workflows/daily_sync.yml](.github/workflows/daily_sync.yml) reads these secrets and runs `sync_activity.py` headlessly.

## Formatting / linting

- Format: `python -m black .`
- Lint: `python -m flake8`

## Tests

- `python -m unittest discover -s tests -p "test_*.py"`

Optional read-only Garmin validation (skipped by default):

- `set RUN_GARMIN_VALIDATE_TESTS=1`
- `set GARMIN_VALIDATE_MAX_FETCH=25`
- `python -m unittest tests.test_garmin_readonly_validation`

## Pre-commit

Install the git hooks once:

- `pip install .[dev]`
- `pre-commit install`

Then on each commit, Black and Flake8 will run automatically.

## generate_garmin_session.py

Helper for first-time setup:

- Log in once locally: `python -c "import garth; garth.login('EMAIL','PASS'); garth.save('.garth')"`
- Run `python generate_garmin_session.py` (optionally `--garth-dir <path>`).
- Copy the printed base64 string into your repo's `GARMIN_SESSION` secret.

## schedule_workout.py

Schedules an existing Garmin workout onto your Garmin Connect calendar (so it can sync to your watch).

Required env var:

- `GARMIN_SESSION`

Examples:

- List workouts: `python schedule_workout.py --list`
- Schedule by name: `python schedule_workout.py --workout-name "ga_30min" --date 2026-02-22`
- Schedule by id: `python schedule_workout.py --workout-id 1234567890 --date 2026-02-22`

- Schedule by id: `python schedule_workout.py --workout-id 1234567890 --date 2026-02-22`

## sync_workouts.py

Creates workouts on Garmin Connect and schedules them on your Garmin calendar using a YAML plan file.

Run:

- `python sync_workouts.py workout_plan.yaml`

Drive mode (sync all YAML plans in the folder):

- `python sync_workouts.py --from-drive`

List mode:

- List plan filenames in Drive: `python sync_workouts.py --from-drive --list-plans`
- List the computed schedule (includes outdated entries): `python sync_workouts.py --list-schedule workout_plan.yaml`
- List the computed schedule for all Drive plans: `python sync_workouts.py --from-drive --list-schedule`

### Workout Plan File Spec (give this to Gemini)

Your generator (Gemini) should create a YAML file with these top-level keys:

- `settings` (optional)
- `definitions` (optional)
- `workouts` (required)
- `schedulePlan` (required)

#### `settings`

- `deleteSameNameWorkout` (boolean, optional)
   - If `true`, delete any existing workout(s) with the same `workoutName` before creating.
   - If omitted, falls back to `sync_settings.toml` `workouts.deleteSameNameWorkout`.

- `sportType` (string or mapping, optional)
   - If omitted, workouts default to `running`.
   - Supported strings (for now):
      - `running`
      - `pool_swim`
   - Example:

```yaml
settings:
  sportType: running

# For pool swims, also configure pool length (optional; defaults to 25 meter):
settings:
   sportType: pool_swim
   poolLength: 50
   poolLengthUnit: meter

# Or provide a full unit mapping if needed:
settings:
   sportType: pool_swim
   poolLength: 50
   poolLengthUnit:
      unitId: 1
      unitKey: meter
      factor: 100.0
```

#### `definitions`

Mapping of `NAME -> string` used for substitution in workout step strings.

- Any `$NAME` appearing in a step value is replaced with the corresponding value.

Example:

```yaml
definitions:
   VO2MaxP: "3:30-4:00"
```

#### `workouts`

Mapping of `workoutName -> steps`.

Two supported shapes for each workout value:

- A list of steps (existing format)
- A mapping with:
   - `description` (string, optional)
   - `steps` (list of steps, required)

Each step is a single-key mapping. Supported step keys:

- `warmup`
- `cooldown`
- `run` (alias: `swim`)
- `recovery`
- `repeat(N)` where `N` is an integer (nested steps)

Step value grammar (string):

- Duration (required):
   - `lap`
   - `<N>sec` (example: `30sec`)
   - `<N>min` (example: `15min`)
   - `<N>m` (example: `1200m`)
   - `<N>yd` (yards, converted to meters for Garmin payloads)
- Target (optional, space-separated):
   - `@H(z2)` heart rate zone (supports `z1`, `z2`, ...)
   - `@P(M:SS-M:SS)` pace range in min/km (example: `@P(6:35-7:00)`) 
   - `@SP(M:SS-M:SS)` swim pace range per 100m (converted to m/s)
   - `@C(LOW-HIGH)` cadence range (spm)
   - `@W(LOW-HIGH)` power range (watts)

Example:

```yaml
workouts:
   ga_30min:
      - warmup: lap
      - run: 30min @H(z2)
      - cooldown: lap

   interval_vo2max:
      - warmup: 15min @H(z2)
      - repeat(8):
         - run: 30sec @P($VO2MaxP)
         - recovery: 1200m
      - cooldown: 15min @H(z2)

Advanced per-step overrides (optional):

If you need a Garmin condition/target that isn't tokenized yet (e.g. swim send-off / HR-less-than), you can use the mapping step format and provide raw Garmin fields.

Example:

```yaml
- recovery:
    detail: lap
    notes: "Recover until HR drops below 120bpm"
    endCondition: { conditionTypeId: 999, conditionTypeKey: heart.rate.less.than }
    endConditionValue: 120
    targetType: { workoutTargetTypeId: 1, workoutTargetTypeKey: no.target }
```
```

#### `schedulePlan`

Two supported formats:

1) Absolute date schedule (recommended when generating a fixed plan)

- `start_from` (required): date string `YYYY-MM-DD`
- `workouts` (required): list of per-day entries

Each entry is a string:

- `rest` means skip scheduling that day
- otherwise, it is one workout name or multiple names separated by commas

Example:

```yaml
schedulePlan:
   start_from: 2026-02-22
   workouts:
      - interval_vo2max
      - ga_30min
      - rest
      - ga_30min, interval_vo2max
```

2) Relative weekday schedule (useful for "from today onward")

Omit `start_from`. Provide weekday-keyed entries in the list.
Weekdays are interpreted as the next occurrence from today onward (e.g. if today is Saturday,
`saturday` means today, `sunday` tomorrow, `monday` in two days, etc.).

```yaml
schedulePlan:
   workouts:
      - saturday: swim_endurance_400s
      - sunday: rest
      - monday: swim_technique_drills
```

### Gemini / Drive automation use case

A practical end-to-end flow is:

1) A Gem (Gemini) watches a Google Drive folder.
2) It inspects new files (e.g., plan notes, PDFs, text prompts).
3) It produces a `workout_plan.yaml` that matches the spec above.
4) This repo runs `python sync_workouts.py workout_plan.yaml` to create + schedule workouts on Garmin.

You can store the generated `workout_plan.yaml` in Drive as well, then pull it down in CI before running.
