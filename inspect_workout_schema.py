import argparse
import sys

import garth

import utils


def _walk_steps(steps: list[dict]) -> list[dict]:
    out: list[dict] = []
    stack = list(steps)
    while stack:
        item = stack.pop()
        if not isinstance(item, dict):
            continue
        out.append(item)
        nested = item.get("workoutSteps")
        if isinstance(nested, list):
            stack.extend([x for x in nested if isinstance(x, dict)])
    return out


def _paceish_target_key(step: dict) -> str | None:
    tt = step.get("targetType")
    if not isinstance(tt, dict):
        return None
    key = tt.get("workoutTargetTypeKey")
    if not isinstance(key, str):
        return None
    if "pace" not in key.lower():
        return None
    return key


def _target_key(step: dict) -> str | None:
    tt = step.get("targetType")
    if not isinstance(tt, dict):
        return None
    key = tt.get("workoutTargetTypeKey")
    if not isinstance(key, str):
        return None
    return key


def _obj_key(obj: dict) -> tuple[tuple[str, object], ...]:
    """Stable key for deduping small JSON dicts."""

    # Note: values can be nested dicts; we only use this for small leaf-ish objects
    # like strokeType/equipmentType, which are shallow.
    return tuple(sorted(obj.items(), key=lambda kv: str(kv[0])))


def main() -> None:
    # Windows terminals often default to cp1252; workout names/notes can contain emoji.
    # Force UTF-8 so printing fetched JSON is robust.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description=(
            "Inspect workout step schema and target types from Garmin (read-only). "
            "Useful for reverse-engineering which targetType keys Garmin actually uses."
        )
    )
    parser.add_argument(
        "--sport-key",
        type=str,
        default="swimming",
        help=(
            "Which sportTypeKey to focus on when summarizing targets/endConditions "
            "(default: swimming). Example: running"
        ),
    )
    parser.add_argument(
        "--max-fetch",
        type=int,
        default=50,
        help="Max workouts to fetch in detail (default: 50)",
    )
    parser.add_argument(
        "--workout-id",
        type=int,
        default=None,
        help="Inspect this workoutId (skips auto-selection)",
    )
    parser.add_argument(
        "--name-contains",
        type=str,
        default=None,
        help="Prefer workouts whose name contains this substring (case-insensitive)",
    )
    parser.add_argument(
        "--description-contains",
        type=str,
        default=None,
        help=(
            "If set, print any step whose 'description' contains this substring "
            "(case-insensitive). Useful for locating MANUAL placeholder steps after you edit them in the app."
        ),
    )
    args = parser.parse_args()

    focus_sport = str(args.sport_key or "").strip().lower()
    desc_needle = (
        str(args.description_contains or "").strip().lower()
        if args.description_contains
        else None
    )

    utils.garmin_authenticate()
    workouts = utils.garmin_list_workouts()
    print("workouts:", len(workouts))
    if not workouts:
        raise SystemExit("No workouts")

    # Scan workouts and summarize targetType/endCondition objects for the focused sport.
    focus_target_pairs: dict[tuple[int | None, str | None], int] = {}
    focus_end_pairs: dict[tuple[int | None, str | None], int] = {}
    focus_pace_examples: dict[str, dict] = {}
    focus_target_examples: dict[str, dict] = {}

    focus_stroke_types: dict[tuple[tuple[str, object], ...], dict] = {}
    focus_equipment_types: dict[tuple[tuple[str, object], ...], dict] = {}

    any_target_pairs: dict[tuple[int | None, str | None], int] = {}

    def bump(d: dict, key: tuple[int | None, str | None]) -> None:
        d[key] = d.get(key, 0) + 1

    max_fetch = int(args.max_fetch)
    fetched = 0

    # Build a quick index for name lookup.
    workout_summaries: dict[int, dict] = {}
    for w in workouts:
        wid0 = w.get("workoutId")
        if isinstance(wid0, int):
            workout_summaries[wid0] = w

    # If user requested a specific workoutId, just dump that one.
    if args.workout_id is not None:
        wid = args.workout_id
        wsum = workout_summaries.get(wid, {})
        print("\nforced workoutId:", wid)
        if wsum:
            print("workoutName:", wsum.get("workoutName"))
        full = garth.connectapi(f"/workout-service/workout/{wid}")
        print("sportType:", full.get("sportType"))
        segs = full.get("workoutSegments") or []
        for seg in segs:
            steps = (seg or {}).get("workoutSteps") or []
            for step in _walk_steps(steps):
                tkey = _paceish_target_key(step)
                if tkey:
                    print(f"\n--- pace-like step (targetTypeKey={tkey}) ---")
                    print(step)
        return

    focus_workouts_with_pace: list[tuple[int, str, set[str]]] = []
    focus_workouts_with_targets: list[tuple[int, str, set[str]]] = []

    for w in workouts:
        wid = w.get("workoutId")
        if not isinstance(wid, int):
            continue

        fetched += 1
        if fetched > max_fetch:
            break

        full = garth.connectapi(f"/workout-service/workout/{wid}")
        sport_key = str((full.get("sportType") or {}).get("sportTypeKey") or "").lower()
        workout_name = str(full.get("workoutName") or w.get("workoutName") or "")

        segs = full.get("workoutSegments") or []
        found_pace_keys: set[str] = set()
        found_target_keys: set[str] = set()
        for seg in segs:
            steps = (seg or {}).get("workoutSteps") or []
            for step in _walk_steps(steps):
                if sport_key == focus_sport:
                    st = step.get("strokeType")
                    if isinstance(st, dict):
                        focus_stroke_types.setdefault(_obj_key(st), st)

                    eq = step.get("equipmentType")
                    if isinstance(eq, dict):
                        focus_equipment_types.setdefault(_obj_key(eq), eq)

                if desc_needle and sport_key == focus_sport:
                    desc = step.get("description")
                    if isinstance(desc, str) and desc_needle in desc.lower():
                        print(
                            f"\n--- MATCH step description contains '{desc_needle}' "
                            f"(workoutId={wid}, workoutName={workout_name!r}) ---"
                        )
                        print(
                            "stepType:", (step.get("stepType") or {}).get("stepTypeKey")
                        )
                        print("endCondition:", step.get("endCondition"))
                        print("endConditionValue:", step.get("endConditionValue"))
                        print("targetType:", step.get("targetType"))
                        print("targetValueOne:", step.get("targetValueOne"))
                        print("targetValueTwo:", step.get("targetValueTwo"))
                        print("zoneNumber:", step.get("zoneNumber"))
                        print("secondaryTargetType:", step.get("secondaryTargetType"))
                        print(
                            "secondaryTargetValueOne:",
                            step.get("secondaryTargetValueOne"),
                        )
                        print(
                            "secondaryTargetValueTwo:",
                            step.get("secondaryTargetValueTwo"),
                        )
                        print(
                            "secondaryTargetValueUnit:",
                            step.get("secondaryTargetValueUnit"),
                        )
                        print("secondaryZoneNumber:", step.get("secondaryZoneNumber"))
                        print("strokeType:", step.get("strokeType"))
                        print("equipmentType:", step.get("equipmentType"))
                        print("FULL STEP:")
                        print(step)

                tt = step.get("targetType")
                if isinstance(tt, dict):
                    pair = (
                        tt.get("workoutTargetTypeId"),
                        tt.get("workoutTargetTypeKey"),
                    )
                    bump(any_target_pairs, pair)

                tkey = _paceish_target_key(step)
                if sport_key == focus_sport and tkey:
                    found_pace_keys.add(tkey)

                skey = _target_key(step)
                if sport_key == focus_sport and skey and skey != "no.target":
                    found_target_keys.add(skey)
                    if skey not in focus_target_examples:
                        focus_target_examples[skey] = step

                if sport_key != focus_sport:
                    continue

                ec = step.get("endCondition")
                if isinstance(tt, dict):
                    pair = (
                        tt.get("workoutTargetTypeId"),
                        tt.get("workoutTargetTypeKey"),
                    )
                    bump(focus_target_pairs, pair)

                    tkey = str(tt.get("workoutTargetTypeKey") or "")
                    if "pace" in tkey and tkey not in focus_pace_examples:
                        focus_pace_examples[tkey] = step

                if isinstance(ec, dict):
                    epair = (ec.get("conditionTypeId"), ec.get("conditionTypeKey"))
                    bump(focus_end_pairs, epair)

        if sport_key == focus_sport and found_pace_keys:
            focus_workouts_with_pace.append((wid, workout_name, found_pace_keys))

        if sport_key == focus_sport and found_target_keys:
            focus_workouts_with_targets.append((wid, workout_name, found_target_keys))

    if focus_workouts_with_pace:
        print(f"\n{focus_sport.upper()} workouts that contain pace-like targets:")
        for wid, name, keys in sorted(focus_workouts_with_pace, key=lambda x: x[0]):
            print(f"  {wid}\t{name}\tkeys={sorted(keys)}")

    if focus_workouts_with_targets:
        print(
            f"\n{focus_sport.upper()} workouts that contain any non-no.target targets:"
        )
        for wid, name, keys in sorted(focus_workouts_with_targets, key=lambda x: x[0]):
            print(f"  {wid}\t{name}\tkeys={sorted(keys)}")

    def dump_counts(title: str, d: dict[tuple[int | None, str | None], int]) -> None:
        print(title)
        for (id_val, key_val), count in sorted(
            d.items(), key=lambda kv: (-kv[1], str(kv[0][1] or ""))
        ):
            print(f"  {count:4d}  id={id_val!s:>3}  key={key_val}")

    dump_counts(
        f"\n{focus_sport.upper()} distinct targetType pairs (id/key):",
        focus_target_pairs,
    )
    dump_counts(
        f"\n{focus_sport.upper()} distinct endCondition pairs (id/key):",
        focus_end_pairs,
    )

    if focus_stroke_types:
        print(f"\n{focus_sport.upper()} distinct strokeType objects:")
        for obj in sorted(
            focus_stroke_types.values(),
            key=lambda x: (
                int(x.get("strokeTypeId") or 0),
                str(x.get("strokeTypeKey") or ""),
            ),
        ):
            print("  ", obj)

    if focus_equipment_types:
        print(f"\n{focus_sport.upper()} distinct equipmentType objects:")
        for obj in sorted(
            focus_equipment_types.values(),
            key=lambda x: (
                int(x.get("equipmentTypeId") or 0),
                str(x.get("equipmentTypeKey") or ""),
            ),
        ):
            print("  ", obj)

    print(f"\n{focus_sport.upper()} example steps for pace-related targets:")
    if not focus_pace_examples:
        print("  (none found in scanned workouts)")
    else:
        for tkey in sorted(focus_pace_examples.keys()):
            step = focus_pace_examples[tkey]
            print(f"\n--- example for {tkey} ---")
            print(step)

    print(f"\n{focus_sport.upper()} example steps for ALL non-no.target target keys:")
    if not focus_target_examples:
        print("  (none found in scanned workouts)")
    else:
        for tkey in sorted(focus_target_examples.keys()):
            print(f"\n--- example for {tkey} ---")
            print(focus_target_examples[tkey])

    # If the user hinted a name, try to dump the best matching workout with pace.
    if args.name_contains and focus_workouts_with_targets:
        needle = args.name_contains.strip().lower()
        preferred = [x for x in focus_workouts_with_targets if needle in x[1].lower()]
        if preferred:
            wid, name, keys = preferred[0]
            print(f"\n\nPreferred match by name: {wid} {name} keys={sorted(keys)}")
            full = garth.connectapi(f"/workout-service/workout/{wid}")
            print("sportType:", full.get("sportType"))

            per_key: dict[str, dict] = {}
            end_pairs: dict[tuple[int | None, str | None], int] = {}

            segs = full.get("workoutSegments") or []
            for seg in segs:
                steps = (seg or {}).get("workoutSteps") or []
                for step in _walk_steps(steps):
                    tkey = _target_key(step)
                    if tkey and tkey != "no.target" and tkey not in per_key:
                        per_key[tkey] = step

                    ec = step.get("endCondition")
                    if isinstance(ec, dict):
                        epair = (ec.get("conditionTypeId"), ec.get("conditionTypeKey"))
                        end_pairs[epair] = end_pairs.get(epair, 0) + 1

            print("\nDistinct endCondition pairs in preferred workout:")
            for (cid, ckey), count in sorted(
                end_pairs.items(), key=lambda kv: (-kv[1], str(kv[0][1] or ""))
            ):
                print(f"  {count:4d}  id={cid!s:>3}  key={ckey}")

            print(
                "\nExample steps per non-no.target targetTypeKey in preferred workout:"
            )
            for tkey in sorted(per_key.keys()):
                print(f"\n--- example for {tkey} ---")
                print(per_key[tkey])

    # Keep the old single-sample dump for quick eyeballing.

    # Pick a workout to sample (prefer swimming).
    wid = None
    for w in workouts:
        st = w.get("sportType")
        if (
            isinstance(st, dict)
            and str(st.get("sportTypeKey", "")).lower() == "swimming"
        ):
            wid = w.get("workoutId")
            break
    if wid is None:
        wid = workouts[0].get("workoutId")

    print("sample workoutId:", wid)
    full = garth.connectapi(f"/workout-service/workout/{wid}")

    seg = (full.get("workoutSegments") or [{}])[0]
    steps = seg.get("workoutSteps") or []

    first = None
    for st in steps:
        if isinstance(st, dict) and st.get("type") in (
            "ExecutableStepDTO",
            "ExerciseStepDTO",
        ):
            first = st
            break
        if (
            isinstance(st, dict)
            and isinstance(st.get("workoutSteps"), list)
            and st.get("workoutSteps")
        ):
            nested = st.get("workoutSteps")
            if nested and isinstance(nested[0], dict):
                first = nested[0]
                break

    print("first step dtoType:", (first or {}).get("type"))
    keys = sorted(list((first or {}).keys()))
    print("first step keys:", keys)
    print("stepType:", (first or {}).get("stepType"))
    print("endCondition:", (first or {}).get("endCondition"))
    print("targetType:", (first or {}).get("targetType"))

    # Summarize distinct stepType and targetType objects.
    step_types = []
    target_types = []

    for item in _walk_steps(steps):
        if isinstance(item.get("stepType"), dict):
            step_types.append(item["stepType"])
        if isinstance(item.get("targetType"), dict):
            target_types.append(item["targetType"])

    def uniq(objs):
        seen = set()
        out = []
        for obj in objs:
            key = tuple(sorted(obj.items()))
            if key in seen:
                continue
            seen.add(key)
            out.append(obj)
        return out

    print("distinct stepType objects:")
    for obj in uniq(step_types):
        print("  ", obj)

    print("distinct targetType objects:")
    for obj in uniq(target_types):
        print("  ", obj)

    # Search for an HR-zone step in any workout.
    for w in workouts:
        wid2 = w.get("workoutId")
        if not isinstance(wid2, int):
            continue
        full2 = garth.connectapi(f"/workout-service/workout/{wid2}")
        segs2 = full2.get("workoutSegments") or []
        for seg2 in segs2:
            steps2 = (seg2 or {}).get("workoutSteps") or []
            stack = list(steps2)
            while stack:
                item = stack.pop()
                if not isinstance(item, dict):
                    continue
                tt = item.get("targetType")
                if (
                    isinstance(tt, dict)
                    and tt.get("workoutTargetTypeKey") == "heart.rate.zone"
                ):
                    print("found heart.rate.zone step in workoutId:", wid2)
                    print("step:", item)
                    return
                nested = item.get("workoutSteps")
                if isinstance(nested, list):
                    stack.extend(nested)


if __name__ == "__main__":
    main()
