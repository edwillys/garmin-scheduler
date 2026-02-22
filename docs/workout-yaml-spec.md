# Workout Plan YAML Spec

This document describes the YAML format consumed by the workout scheduler.

Examples in this repo:

- `workout_plan_swim.yaml`
- `workout_plan_run.yaml`

## Top-level keys

A plan YAML file should be a mapping with these top-level keys:

- `settings` (optional)
- `definitions` (optional)
- `workouts` (required)
- `schedulePlan` (required)

## `settings`

- `deleteSameNameWorkout` (boolean, optional)
  - If `true`, delete any existing workout(s) with the same `workoutName` before creating.
  - If omitted, falls back to `sync_settings.toml` `workouts.deleteSameNameWorkout`.

- `sportType` (string or mapping, optional)
  - If omitted, workouts default to `running`.
  - Supported strings (for now):
    - `running`
    - `pool_swim`

Example:

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

## `definitions`

Mapping of `NAME -> string` used for substitution in workout step strings.

- Any `$NAME` appearing in a step value is replaced with the corresponding value.

Example:

```yaml
definitions:
  VO2MaxP: "3:30-4:00"
```

## `workouts`

Mapping of `workoutName -> steps`.

Two supported shapes for each workout value:

- A list of steps
- A mapping with:
  - `description` (string, optional)
  - `steps` (list of steps, required)

Each step is a single-key mapping. Supported step keys:

- `warmup`
- `cooldown`
- `run` (alias: `swim`)
- `recovery`
- `repeat(N)` where `N` is an integer (nested steps)

### Step value grammar (string)

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
```

### Advanced per-step overrides (optional)

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

## `schedulePlan`

Two supported formats:

### 1) Absolute date schedule (recommended when generating a fixed plan)

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

### 2) Relative weekday schedule (useful for "from today onward")

Omit `start_from`. Provide weekday-keyed entries in the list.
Weekdays are interpreted as the next occurrence from today onward.

```yaml
schedulePlan:
  workouts:
    - saturday: swim_endurance_400s
    - sunday: rest
    - monday: swim_technique_drills
```

## Gemini / Drive automation use case

A practical end-to-end flow is:

1) A Gem (Gemini) watches a Google Drive folder.
2) It inspects new files (e.g., plan notes, PDFs, text prompts).
3) It produces a plan YAML that matches this spec.
4) This repo runs:

- `python -m garmin_scheduler.sync_workouts <plan>.yaml`

You can store generated plans in Drive and use `--from-drive` to sync them.
