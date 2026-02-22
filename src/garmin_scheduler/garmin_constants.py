"""Centralized Garmin Connect "constants" for workout payloads.

These values are reverse-engineered from Garmin Connect JSON payloads.
They are not guaranteed to be stable across Garmin accounts/regions.

This module is intentionally small and data-focused so the rest of the
codebase can import a single source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Converts running pace in min/km to speed in m/s, matching common reverse-engineered payloads.
# Example: 3:20 min/km -> 5.0 m/s because 16.66666 / 3.3333... ~= 5.0
PACE_CONST = 16.66666


@dataclass(frozen=True)
class StepType:
    stepTypeId: int
    stepTypeKey: str
    displayOrder: int


@dataclass(frozen=True)
class ConditionType:
    conditionTypeId: int
    conditionTypeKey: str
    displayOrder: int
    displayable: bool


@dataclass(frozen=True)
class TargetType:
    workoutTargetTypeId: int
    workoutTargetTypeKey: str
    displayOrder: int


# Sports
#
# Notes:
# - This repo currently only "officially" supports running and pool swimming in the CLI.
# - The other sportTypeId values below are included because they are widely observed
#   in reverse-engineered Connect payloads and match the reference tooling we used.
SPORT_RUNNING = {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1}
SPORT_CYCLING = {"sportTypeId": 2, "sportTypeKey": "cycling", "displayOrder": 1}
SPORT_SWIMMING = {"sportTypeId": 4, "sportTypeKey": "swimming", "displayOrder": 1}
SPORT_STRENGTH_TRAINING = {
    "sportTypeId": 5,
    "sportTypeKey": "strength_training",
    "displayOrder": 1,
}
SPORT_CARDIO_TRAINING = {
    "sportTypeId": 6,
    "sportTypeKey": "cardio_training",
    "displayOrder": 1,
}
SPORT_YOGA = {"sportTypeId": 7, "sportTypeKey": "yoga", "displayOrder": 1}
SPORT_PILATES = {"sportTypeId": 8, "sportTypeKey": "pilates", "displayOrder": 1}
SPORT_HIIT = {"sportTypeId": 9, "sportTypeKey": "hiit", "displayOrder": 1}

SUPPORTED_SPORTS: dict[str, dict[str, Any]] = {
    "running": SPORT_RUNNING,
    "cycling": SPORT_CYCLING,
    "swimming": SPORT_SWIMMING,
    "pool_swim": SPORT_SWIMMING,
    "strength_training": SPORT_STRENGTH_TRAINING,
    "cardio_training": SPORT_CARDIO_TRAINING,
    "yoga": SPORT_YOGA,
    "pilates": SPORT_PILATES,
    "hiit": SPORT_HIIT,
}


# Units
POOL_UNIT_METER = {"unitId": 1, "unitKey": "meter", "factor": 100.0}
POOL_UNIT_YARD = {"unitId": 2, "unitKey": "yard", "factor": 91.44}

# Step types
STEP_WARMUP = StepType(1, "warmup", 1)
STEP_COOLDOWN = StepType(2, "cooldown", 2)
STEP_INTERVAL = StepType(3, "interval", 3)
STEP_RECOVERY = StepType(4, "recovery", 4)
STEP_REST = StepType(5, "rest", 5)
STEP_REPEAT = StepType(6, "repeat", 6)


# End conditions
COND_LAP = ConditionType(1, "lap.button", 1, True)
COND_TIME = ConditionType(2, "time", 2, True)
COND_DISTANCE = ConditionType(3, "distance", 3, True)
COND_CALORIES = ConditionType(4, "calories", 4, True)
COND_REPS = ConditionType(10, "reps", 10, True)
COND_ITER_END = ConditionType(7, "iterations", 7, False)


# Targets
TARGET_NO = TargetType(1, "no.target", 1)
TARGET_CADENCE = TargetType(3, "cadence", 3)
TARGET_HR_ZONE = TargetType(4, "heart.rate.zone", 4)
# Reverse-engineered from this account's running workouts (2026-02): the target type id=5
# is stored as key "speed.zone" in fetched workout JSON.
TARGET_SPEED = TargetType(5, "speed.zone", 5)

# Backward-compat with earlier versions of this repo that used @W(...) and called it "power".
# Keep the name but point at the observed Connect target type.
TARGET_POWER = TARGET_SPEED
TARGET_PACE = TargetType(6, "pace.zone", 6)

# Swimming-only "secondary target" types observed in fetched workouts.
TARGET_SWIM_CSS_OFFSET = TargetType(17, "swim.css.offset", 17)
TARGET_SWIM_INSTRUCTION = TargetType(18, "swim.instruction", 18)

# Swim instruction codes observed in fetched workouts (secondaryTargetValueOne).
SWIM_INSTRUCTION_CODES: dict[str, float] = {
    "recovery": 1.0,
    "easy": 3.0,
    "moderate": 4.0,
    "hard": 5.0,
    "very_hard": 6.0,
    "all_out": 7.0,
    "ascending": 9.0,
    "descending": 10.0,
}


# Swimming-specific fields
STROKE_TYPES: dict[str, dict[str, Any]] = {
    # Fetched from real workouts (2026-02).
    "freestyle": {"strokeTypeId": 6, "strokeTypeKey": "free", "displayOrder": 6},
    "free": {"strokeTypeId": 6, "strokeTypeKey": "free", "displayOrder": 6},
    "backstroke": {"strokeTypeId": 2, "strokeTypeKey": "backstroke", "displayOrder": 2},
    "back": {"strokeTypeId": 2, "strokeTypeKey": "backstroke", "displayOrder": 2},
    "breaststroke": {
        "strokeTypeId": 3,
        "strokeTypeKey": "breaststroke",
        "displayOrder": 3,
    },
    # Garmin stores butterfly as key "fly".
    "butterfly": {"strokeTypeId": 5, "strokeTypeKey": "fly", "displayOrder": 5},
    "fly": {"strokeTypeId": 5, "strokeTypeKey": "fly", "displayOrder": 5},
    "drill": {"strokeTypeId": 4, "strokeTypeKey": "drill", "displayOrder": 4},
    "mixed": {"strokeTypeId": 8, "strokeTypeKey": "mixed", "displayOrder": 8},
    # "Choice" / "Any stroke"
    "choice": {"strokeTypeId": 1, "strokeTypeKey": "any_stroke", "displayOrder": 1},
    "any_stroke": {"strokeTypeId": 1, "strokeTypeKey": "any_stroke", "displayOrder": 1},
    # Medley variants
    "individual_medley": {
        "strokeTypeId": 7,
        "strokeTypeKey": "individual_medley",
        "displayOrder": 7,
    },
    "im": {"strokeTypeId": 7, "strokeTypeKey": "individual_medley", "displayOrder": 7},
    "individual_medley_by_round": {
        "strokeTypeId": 9,
        "strokeTypeKey": "individual_medley_by_round",
        "displayOrder": 9,
    },
    "im_by_round": {
        "strokeTypeId": 9,
        "strokeTypeKey": "individual_medley_by_round",
        "displayOrder": 9,
    },
    "reverse_individual_medley_by_round": {
        "strokeTypeId": 10,
        "strokeTypeKey": "reverse_individual_medley_by_round",
        "displayOrder": 10,
    },
    "reverse_im_by_round": {
        "strokeTypeId": 10,
        "strokeTypeKey": "reverse_individual_medley_by_round",
        "displayOrder": 10,
    },
}

EQUIPMENT_TYPES: dict[str, dict[str, Any]] = {
    # Fetched from real workouts (2026-02): "none" comes back with equipmentTypeKey=null.
    "none": {"equipmentTypeId": 0, "equipmentTypeKey": None, "displayOrder": 0},
    "fins": {"equipmentTypeId": 1, "equipmentTypeKey": "fins", "displayOrder": 0},
    "kickboard": {
        "equipmentTypeId": 2,
        "equipmentTypeKey": "kickboard",
        "displayOrder": 0,
    },
    "paddles": {"equipmentTypeId": 3, "equipmentTypeKey": "paddles", "displayOrder": 0},
    "pull_buoy": {
        "equipmentTypeId": 4,
        "equipmentTypeKey": "pull_buoy",
        "displayOrder": 0,
    },
    "snorkel": {"equipmentTypeId": 5, "equipmentTypeKey": "snorkel", "displayOrder": 0},
}
