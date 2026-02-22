"""Garmin workout payload builder.

Converts YAML plan step specs into Garmin Connect workout JSON payloads.
Extracted from sync_workouts.py to keep domain logic separate from the
CLI and plan-loading code.
"""

from __future__ import annotations

import re
from typing import Any

from garmin_constants import (
    COND_CALORIES,
    COND_DISTANCE,
    COND_ITER_END,
    COND_LAP,
    COND_REPS,
    COND_TIME,
    EQUIPMENT_TYPES,
    PACE_CONST,
    POOL_UNIT_METER,
    POOL_UNIT_YARD,
    SPORT_RUNNING,
    SPORT_SWIMMING,
    STEP_COOLDOWN,
    STEP_INTERVAL,
    STEP_RECOVERY,
    STEP_REPEAT,
    STEP_REST,
    STEP_WARMUP,
    STROKE_TYPES,
    SWIM_INSTRUCTION_CODES,
    TARGET_CADENCE,
    TARGET_HR_ZONE,
    TARGET_NO,
    TARGET_PACE,
    TARGET_SPEED,
    TARGET_SWIM_CSS_OFFSET,
    TARGET_SWIM_INSTRUCTION,
    StepType,
)


def parse_bracket(s: str) -> tuple[str | None, str | None]:
    match = re.match(r"([\w@]+)(?:\(([^()]+)\))?", s.lower().strip())
    if not match:
        return None, None
    return match.group(1), match.group(2)


def parse_time_to_minutes(time_string: str) -> float:
    parts = time_string.strip().split(":")
    if len(parts) != 2:
        raise ValueError("Expected M:SS or MM:SS")
    minutes = int(parts[0])
    seconds = int(parts[1])
    return minutes + (seconds / 60)


def _normalize_sport_key(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def resolve_sport_from_string(
    sport: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Resolve a human-friendly sport string to Garmin sportType/subSportType objects.

    Supported values:
    - running
    - swimming / pool_swim

    Values are fixed based on the public reverse-engineering approach used by:
    https://github.com/sydspost/Garmin-Connect-Workout-and-Schedule-creator
    """

    key = _normalize_sport_key(sport)
    if key in {"run", "running"}:
        return SPORT_RUNNING, None

    if key in {"swim", "swimming", "pool_swim", "pool_swimming", "lap_swimming"}:
        return SPORT_SWIMMING, None

    raise SystemExit(
        f"Unsupported sportType string: {sport!r}. Supported: running, swimming (pool_swim)."
    )


def _validate_pool_length_unit(value: Any, *, where: str) -> dict[str, Any]:
    if isinstance(value, str):
        key = value.strip().lower()
        if key in {"m", "meter", "meters", "metre", "metres"}:
            return dict(POOL_UNIT_METER)
        if key in {"yd", "yard", "yards"}:
            return dict(POOL_UNIT_YARD)
        raise SystemExit(
            f"{where} unsupported unit string: {value!r}. Use 'meter' or 'yard', or provide a mapping."
        )

    if isinstance(value, dict):
        unit_id = value.get("unitId")
        unit_key = value.get("unitKey")
        factor = value.get("factor")
        if not isinstance(unit_id, int):
            raise SystemExit(f"{where}.unitId must be an int")
        if not isinstance(unit_key, str) or not unit_key.strip():
            raise SystemExit(f"{where}.unitKey must be a non-empty string")
        if not isinstance(factor, (int, float)):
            raise SystemExit(f"{where}.factor must be a number")
        return {"unitId": unit_id, "unitKey": unit_key.strip(), "factor": float(factor)}

    raise SystemExit(
        f"{where} must be a string (e.g. 'meter') or a mapping like {{unitId: 1, unitKey: meter, factor: 100.0}}"
    )


def parse_pool_settings(
    settings: dict[str, Any],
    *,
    source_label: str,
) -> tuple[float | None, dict[str, Any] | None]:
    raw_length = settings.get("poolLength")
    raw_unit = settings.get("poolLengthUnit")

    pool_length: float | None = None
    pool_unit: dict[str, Any] | None = None

    if raw_length is not None:
        if not isinstance(raw_length, (int, float)):
            raise SystemExit(f"{source_label}: settings.poolLength must be a number")
        if float(raw_length) <= 0:
            raise SystemExit(f"{source_label}: settings.poolLength must be > 0")
        pool_length = float(raw_length)

    if raw_unit is not None:
        pool_unit = _validate_pool_length_unit(
            raw_unit, where=f"{source_label}: settings.poolLengthUnit"
        )

    return pool_length, pool_unit


def _parse_zone_range(value: str) -> tuple[int, int]:
    raw = value.strip().lower().replace("z", "")
    if "-" in raw:
        lo_s, hi_s = [x.strip() for x in raw.split("-", 1)]
        lo, hi = int(lo_s), int(hi_s)
    else:
        lo = hi = int(raw.strip())
    if lo <= 0 or hi <= 0:
        raise ValueError("Zone numbers must be positive")
    if lo > hi:
        lo, hi = hi, lo
    return lo, hi


def _parse_mmss_to_seconds(value: str) -> int:
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError("Expected M:SS or MM:SS")
    minutes = int(parts[0])
    seconds = int(parts[1])
    total = minutes * 60 + seconds
    if total <= 0:
        raise ValueError("Time must be > 0")
    return total


def _speed_mps_from_per_100m(mmss: str) -> float:
    secs = _parse_mmss_to_seconds(mmss)
    return 100.0 / float(secs)


def parse_step_detail(
    detail: str,
    *,
    sport_type_key: str | None = None,
    swim_pace_unit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Parse a compact step string into Garmin workout step fields.

    Grammar (intentionally minimal):
    - Duration:
      - lap
      - <N>sec
      - <N>min
      - <N>m
            - <N>yd (converted to meters)
            - <N>cal
            - <N>reps
        - Target (optional):
            - @H(z2)  -> heart rate zone
            - @H(z1-2) -> heart rate zone range
            - @P(6:35-7:00) -> pace range in min/km (running-style)
            - @SP(1:40-1:30) -> swim pace range in M:SS per 100m (converted to m/s)
                        - @C(180-195) -> cadence range (spm)
                        - @W(250-280) -> power range (watts)
    """

    step: dict[str, Any] = {}

    tokens = [t for t in detail.strip().split() if t]
    for token in tokens:
        # Duration
        if token.lower() == "lap":
            step.update(
                {
                    "endCondition": COND_LAP.__dict__,
                    "endConditionValue": 1,
                }
            )
            continue

        if token.lower().endswith("sec"):
            value = int(token[:-3])
            step.update(
                {
                    "endCondition": COND_TIME.__dict__,
                    "endConditionValue": value,
                }
            )
            continue

        if token.lower().endswith("min"):
            value = int(token[:-3])
            step.update(
                {
                    "endCondition": COND_TIME.__dict__,
                    "endConditionValue": value * 60,
                }
            )
            continue

        # Distance (km -> meters)
        if token.lower().endswith("km"):
            raw = token[:-2]
            if raw.replace(".", "", 1).isdigit():
                km = float(raw)
            else:
                km = None
            if km is not None:
                step.update(
                    {
                        "endCondition": COND_DISTANCE.__dict__,
                        "endConditionValue": km * 1000.0,
                    }
                )
                continue

        if token.lower().endswith("m"):
            raw = token[:-1]
            if raw.replace(".", "", 1).isdigit():
                value = float(raw)
            else:
                value = None
            if value is not None:
                step.update(
                    {
                        "endCondition": COND_DISTANCE.__dict__,
                        "endConditionValue": value,
                    }
                )
                continue

        if token.lower().endswith("yd"):
            raw = token[:-2]
            if raw.replace(".", "", 1).isdigit():
                yards = float(raw)
            else:
                yards = None
            if yards is not None:
                meters = yards * 0.9144
                step.update(
                    {
                        "endCondition": COND_DISTANCE.__dict__,
                        "endConditionValue": meters,
                    }
                )
                continue

        if token.lower().endswith("cal") and token[:-3].isdigit():
            value = int(token[:-3])
            step.update(
                {
                    "endCondition": COND_CALORIES.__dict__,
                    "endConditionValue": value,
                }
            )
            continue

        if token.lower().endswith("cals") and token[:-4].isdigit():
            value = int(token[:-4])
            step.update(
                {
                    "endCondition": COND_CALORIES.__dict__,
                    "endConditionValue": value,
                }
            )
            continue

        if token.lower().endswith("reps") and token[:-4].isdigit():
            value = int(token[:-4])
            step.update(
                {
                    "endCondition": COND_REPS.__dict__,
                    "endConditionValue": value,
                }
            )
            continue

        # Target
        if token.startswith("@"):
            key, value = parse_bracket(token)
            if not key or value is None:
                continue

            if key.upper() == "@HR":
                # Custom heart-rate range in bpm (Connect stores this as targetType=heart.rate.zone
                # with targetValueOne/Two and no zoneNumber).
                lo_s, hi_s = (
                    [x.strip() for x in value.split("-", 1)]
                    if "-" in value
                    else (value.strip(), value.strip())
                )
                lo = float(lo_s)
                hi = float(hi_s)
                if lo > hi:
                    lo, hi = hi, lo
                step.update(
                    {
                        "targetType": TARGET_HR_ZONE.__dict__,
                        "targetValueOne": lo,
                        "targetValueTwo": hi,
                    }
                )
                continue

            if key.upper() == "@H":
                low, high = _parse_zone_range(value)
                step.update(
                    {
                        "targetType": TARGET_HR_ZONE.__dict__,
                        "zoneNumber": low,
                        **({"secondaryZoneNumber": high} if high != low else {}),
                    }
                )
                continue

            if key.upper() == "@SI":
                # Swim "Effort-Based" targets are stored as:
                # - targetType: no.target
                # - secondaryTargetType: swim.instruction
                # - secondaryTargetValueOne: numeric code
                if (sport_type_key or "").strip().lower() != "swimming":
                    raise ValueError("@SI(...) is only supported for swimming workouts")

                instruction_key = _normalize_sport_key(value)
                code = SWIM_INSTRUCTION_CODES.get(instruction_key)
                if code is None:
                    raise ValueError(
                        f"Unsupported swim instruction: {value!r}. Supported: {sorted(SWIM_INSTRUCTION_CODES.keys())}"
                    )

                step.update(
                    {
                        "targetType": TARGET_NO.__dict__,
                        "secondaryTargetType": TARGET_SWIM_INSTRUCTION.__dict__,
                        "secondaryTargetValueOne": float(code),
                    }
                )
                continue

            if key.upper() == "@CSS":
                # Swim CSS-based pace offset. Stored as secondaryTargetType=swim.css.offset.
                if (sport_type_key or "").strip().lower() != "swimming":
                    raise ValueError(
                        "@CSS(...) is only supported for swimming workouts"
                    )

                try:
                    offset = float(value.strip())
                except ValueError as exc:
                    raise ValueError(
                        "@CSS(value) expects a number (seconds offset)"
                    ) from exc

                step.update(
                    {
                        "targetType": TARGET_NO.__dict__,
                        "secondaryTargetType": TARGET_SWIM_CSS_OFFSET.__dict__,
                        "secondaryTargetValueOne": offset,
                    }
                )
                continue

            if key.upper() == "@P":
                floor, top = [x.strip() for x in value.split("-", 1)]
                floor_min = parse_time_to_minutes(floor)
                top_min = parse_time_to_minutes(top)
                step.update(
                    {
                        "targetType": TARGET_PACE.__dict__,
                        "targetValueOne": PACE_CONST / floor_min,
                        "targetValueTwo": PACE_CONST / top_min,
                    }
                )
                continue

            if key.upper() == "@SP":
                is_swim = (sport_type_key or "").strip().lower() == "swimming"

                # Garmin Connect mobile swim UI "Target Pace" appears to be a single pace,
                # not a low/high range. Allow ranges in YAML, but collapse to a single target
                # (use the slower bound) when building swimming workouts.
                pace_token = value
                if "-" in value:
                    pace_token = value.split("-", 1)[0].strip()

                mps = _speed_mps_from_per_100m(pace_token)

                # Reverse-engineered from real swim workouts: targetTypeKey is still "pace.zone",
                # but Connect also populates secondaryTarget* fields with a per-100 unit.
                payload: dict[str, Any] = {
                    "targetType": TARGET_PACE.__dict__,
                    "targetValueOne": mps,
                }
                if is_swim and isinstance(swim_pace_unit, dict):
                    payload.update(
                        {
                            "secondaryTargetType": TARGET_PACE.__dict__,
                            "secondaryTargetValueOne": mps,
                            "secondaryTargetValueUnit": swim_pace_unit,
                        }
                    )

                step.update(payload)
                continue

            if key.upper() == "@C":
                lo_s, hi_s = [x.strip() for x in value.split("-", 1)]
                lo = int(lo_s)
                hi = int(hi_s)
                if lo > hi:
                    lo, hi = hi, lo
                step.update(
                    {
                        "targetType": TARGET_CADENCE.__dict__,
                        "targetValueOne": lo,
                        "targetValueTwo": hi,
                    }
                )
                continue

            if key.upper() == "@W":
                lo_s, hi_s = [x.strip() for x in value.split("-", 1)]
                lo = float(lo_s)
                hi = float(hi_s)
                if lo > hi:
                    lo, hi = hi, lo
                step.update(
                    {
                        "targetType": TARGET_SPEED.__dict__,
                        "targetValueOne": lo,
                        "targetValueTwo": hi,
                    }
                )
                continue

    if "endCondition" not in step:
        raise ValueError(f"Missing duration in step detail: {detail!r}")

    if "targetType" not in step:
        step["targetType"] = TARGET_NO.__dict__

    return step


def build_steps(
    step_specs: list[Any],
    step_id_counter: list[int],
    *,
    default_equipment: dict[str, Any] | None = None,
    sport_type_key: str | None = None,
    swim_pace_unit: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []

    for spec in step_specs:
        if not isinstance(spec, dict) or len(spec) != 1:
            raise ValueError(f"Invalid step spec: {spec!r}")

        raw_step_name = next(iter(spec.keys()))
        raw_step_value = spec[raw_step_name]

        step_name, bracket_val = parse_bracket(str(raw_step_name))
        if not step_name:
            raise ValueError(f"Invalid step name: {raw_step_name!r}")

        if step_name == "repeat":
            if bracket_val is None:
                raise ValueError("repeat(N) is required")
            iterations = int(bracket_val)
            if not isinstance(raw_step_value, list):
                raise ValueError("repeat(N) value must be a list of nested steps")

            step_id_counter[0] += 1
            step_id = step_id_counter[0]
            nested = build_steps(
                raw_step_value,
                step_id_counter,
                default_equipment=default_equipment,
                sport_type_key=sport_type_key,
                swim_pace_unit=swim_pace_unit,
            )

            steps.append(
                {
                    "stepId": step_id,
                    "stepOrder": step_id,
                    "stepType": STEP_REPEAT.__dict__,
                    "type": "RepeatGroupDTO",
                    "numberOfIterations": iterations,
                    "workoutSteps": nested,
                    "smartRepeat": False,
                    "childStepId": 1,
                    "skipLastRestStep": False,
                    "endCondition": COND_ITER_END.__dict__,
                }
            )
            continue

        # Executable step
        step_type: StepType
        if step_name == "warmup":
            step_type = STEP_WARMUP
        elif step_name == "cooldown":
            step_type = STEP_COOLDOWN
        elif step_name in {"run", "swim", "main", "interval"}:
            step_type = STEP_INTERVAL
        elif step_name == "recovery":
            step_type = STEP_RECOVERY
        elif step_name == "rest":
            step_type = STEP_REST
        else:
            raise ValueError(f"Unsupported step type: {step_name!r}")

        notes: str | None = None
        stroke_obj: dict[str, Any] | None = None
        equipment_obj: dict[str, Any] | None = default_equipment

        explicit_overrides: dict[str, Any] = {}

        if isinstance(raw_step_value, str):
            detail_str = raw_step_value
        elif isinstance(raw_step_value, dict):
            detail = raw_step_value.get("detail")
            if not isinstance(detail, str):
                raise ValueError(
                    f"Step value for {raw_step_name!r} must include a string 'detail'"
                )
            detail_str = detail

            raw_notes = raw_step_value.get("notes")
            if raw_notes is not None:
                if not isinstance(raw_notes, str):
                    raise ValueError("notes must be a string")
                notes = raw_notes

            raw_stroke = raw_step_value.get("stroke")
            if raw_stroke is not None:
                if not isinstance(raw_stroke, str):
                    raise ValueError("stroke must be a string")
                stroke_key = _normalize_sport_key(raw_stroke)
                if stroke_key not in STROKE_TYPES:
                    raise ValueError(
                        f"Unsupported stroke: {raw_stroke!r}. Supported: {sorted(STROKE_TYPES.keys())}"
                    )
                stroke_obj = STROKE_TYPES[stroke_key]

            raw_equipment = raw_step_value.get("equipment")
            if raw_equipment is not None:
                if not isinstance(raw_equipment, str):
                    raise ValueError("equipment must be a string")
                equip_key = _normalize_sport_key(raw_equipment)
                if equip_key not in EQUIPMENT_TYPES:
                    raise ValueError(
                        f"Unsupported equipment: {raw_equipment!r}. Supported: {sorted(EQUIPMENT_TYPES.keys())}"
                    )
                equipment_obj = EQUIPMENT_TYPES[equip_key]

            # Optional low-level overrides for rare Garmin fields.
            # This allows expressing conditions/targets that aren't tokenized yet.
            # Expected shapes mirror Garmin Connect payloads.
            raw_end_condition = raw_step_value.get("endCondition")
            raw_end_value = raw_step_value.get("endConditionValue")
            if raw_end_condition is not None:
                if not isinstance(raw_end_condition, dict):
                    raise ValueError("endCondition must be a mapping if provided")

                if "conditionTypeId" in raw_end_condition:
                    cid = raw_end_condition.get("conditionTypeId")
                    if not isinstance(cid, int):
                        raise ValueError(
                            "endCondition.conditionTypeId must be an int if provided"
                        )
                    if cid == 999:
                        raise ValueError(
                            "endCondition.conditionTypeId=999 looks like a placeholder. "
                            "Remove the override or replace with a real Garmin conditionTypeId."
                        )
                if "conditionTypeKey" in raw_end_condition:
                    ckey = raw_end_condition.get("conditionTypeKey")
                    if not isinstance(ckey, str) or not ckey.strip():
                        raise ValueError(
                            "endCondition.conditionTypeKey must be a non-empty string if provided"
                        )

                explicit_overrides["endCondition"] = raw_end_condition
                if raw_end_value is not None and not isinstance(
                    raw_end_value, (int, float)
                ):
                    raise ValueError("endConditionValue must be a number if provided")
                if raw_end_value is not None:
                    explicit_overrides["endConditionValue"] = raw_end_value

            raw_target_type = raw_step_value.get("targetType")
            if raw_target_type is not None:
                if not isinstance(raw_target_type, dict):
                    raise ValueError("targetType must be a mapping if provided")
                explicit_overrides["targetType"] = raw_target_type

            for numeric_key in (
                "targetValueOne",
                "targetValueTwo",
                "zoneNumber",
                "secondaryZoneNumber",
            ):
                if numeric_key in raw_step_value:
                    v = raw_step_value.get(numeric_key)
                    if v is not None and not isinstance(v, (int, float)):
                        raise ValueError(f"{numeric_key} must be a number if provided")
                    explicit_overrides[numeric_key] = v
        else:
            raise ValueError(
                f"Step value for {raw_step_name!r} must be a string or mapping, got {type(raw_step_value)!r}"
            )

        parsed = parse_step_detail(
            detail_str,
            sport_type_key=sport_type_key,
            swim_pace_unit=swim_pace_unit,
        )
        if explicit_overrides:
            parsed = {**parsed, **explicit_overrides}

        step_id_counter[0] += 1
        step_id = step_id_counter[0]

        steps.append(
            {
                "stepId": step_id,
                "stepOrder": step_id,
                "stepType": step_type.__dict__,
                "type": "ExecutableStepDTO",
                **({"description": notes} if notes else {}),
                **({"strokeType": stroke_obj} if stroke_obj else {}),
                **({"equipmentType": equipment_obj} if equipment_obj else {}),
                **parsed,
            }
        )

    return steps


def _validate_sport_type(value: Any, *, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SystemExit(
            f"{where} must be a mapping like {{sportTypeId: 1, sportTypeKey: running, displayOrder: 1}}"
        )

    sport_type_id = value.get("sportTypeId")
    sport_type_key = value.get("sportTypeKey")
    display_order = value.get("displayOrder", 1)

    if not isinstance(sport_type_id, int):
        raise SystemExit(f"{where}.sportTypeId must be an int")
    if not isinstance(sport_type_key, str) or not sport_type_key.strip():
        raise SystemExit(f"{where}.sportTypeKey must be a non-empty string")
    if not isinstance(display_order, int):
        raise SystemExit(f"{where}.displayOrder must be an int")

    return {
        "sportTypeId": sport_type_id,
        "sportTypeKey": sport_type_key.strip(),
        "displayOrder": display_order,
    }


def parse_sport_settings(
    settings: dict[str, Any],
    *,
    source_label: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return (sportType, subSportType) from YAML settings.

    Supports:
    - settings.sportType: "running" | "swimming" | "pool_swim" (string)
    - settings.sportType: {sportTypeId: ..., sportTypeKey: ..., displayOrder: ...} (mapping)
    - settings.subSportType: {subSportTypeId: ..., subSportTypeKey: ..., displayOrder: ...} (mapping)

    If settings.sportType is omitted, returns (None, None) which means: keep defaults.
    """

    raw_sport = settings.get("sportType")
    raw_subsport = settings.get("subSportType")

    sport_type: dict[str, Any] | None = None
    sub_sport_type: dict[str, Any] | None = None

    if raw_sport is None and raw_subsport is None:
        return None, None

    if isinstance(raw_sport, str):
        sport_type, sub_sport_type = resolve_sport_from_string(raw_sport)
        return sport_type, sub_sport_type

    if raw_sport is not None:
        sport_type = _validate_sport_type(
            raw_sport, where=f"{source_label}: settings.sportType"
        )

    if raw_subsport is not None:
        if not isinstance(raw_subsport, dict):
            raise SystemExit(
                f"{source_label}: settings.subSportType must be a mapping if provided"
            )
        sub_sport_type = {
            "subSportTypeId": raw_subsport.get("subSportTypeId"),
            "subSportTypeKey": raw_subsport.get("subSportTypeKey"),
            "displayOrder": raw_subsport.get("displayOrder", 1),
        }
        if not isinstance(sub_sport_type["subSportTypeId"], int):
            raise SystemExit(
                f"{source_label}: settings.subSportType.subSportTypeId must be an int"
            )
        if (
            not isinstance(sub_sport_type["subSportTypeKey"], str)
            or not sub_sport_type["subSportTypeKey"].strip()
        ):
            raise SystemExit(
                f"{source_label}: settings.subSportType.subSportTypeKey must be a non-empty string"
            )
        if not isinstance(sub_sport_type["displayOrder"], int):
            raise SystemExit(
                f"{source_label}: settings.subSportType.displayOrder must be an int"
            )
        sub_sport_type["subSportTypeKey"] = str(
            sub_sport_type["subSportTypeKey"]
        ).strip()

    return sport_type, sub_sport_type


def build_workout_payload(
    workout_name: str,
    step_specs: list[Any],
    *,
    description: str | None = None,
    sport_type: dict[str, Any] | None = None,
    sub_sport_type: dict[str, Any] | None = None,
    pool_length: float | None = None,
    pool_length_unit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    counter = [0]
    default_equipment = None
    if (
        str((sport_type or SPORT_RUNNING).get("sportTypeKey", "")).strip().lower()
        == "swimming"
    ):
        default_equipment = EQUIPMENT_TYPES["none"]

    effective_sport = sport_type or SPORT_RUNNING
    effective_sport_key = str(effective_sport.get("sportTypeKey") or "")

    workout_steps = build_steps(
        step_specs,
        counter,
        default_equipment=default_equipment,
        sport_type_key=effective_sport_key,
        swim_pace_unit=(
            pool_length_unit if effective_sport_key.lower() == "swimming" else None
        ),
    )

    segment = {
        "segmentOrder": 1,
        "sportType": effective_sport,
        "workoutSteps": workout_steps,
    }

    extra_fields: dict[str, Any] = {}
    if str(effective_sport.get("sportTypeKey", "")).strip().lower() == "swimming":
        # Pool swim workouts generally require a pool length for Connect to accept/interpret distances.
        # This mirrors the reverse-engineered JSON produced by the reference repo.
        effective_pool_length = float(pool_length) if pool_length is not None else 25.0
        effective_pool_unit = pool_length_unit or POOL_UNIT_METER
        extra_fields.update(
            {
                "poolLength": effective_pool_length,
                "poolLengthUnit": effective_pool_unit,
                "avgTrainingSpeed": 0.0,
            }
        )

    # The create endpoint accepts several optional fields; keep minimal.
    return {
        "workoutName": workout_name,
        **({"description": description} if description else {}),
        "sportType": effective_sport,
        "subSportType": sub_sport_type,
        "workoutSegments": [segment],
        "estimatedDistanceUnit": None,
        "avgTrainingSpeed": None,
        "estimatedDurationInSecs": None,
        "estimatedDistanceInMeters": None,
        "estimateType": None,
        **extra_fields,
    }
