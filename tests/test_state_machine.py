import pytest

from backend.engine.config import AlertConfig
from backend.engine.models import AlertStatus, NotificationKind, Severity, TruckState
from backend.engine.state_machine import evaluate_truck
from tests.helpers import at, every_5_min, readings


def run(points, now=None, config=None):
    return evaluate_truck("T-99", readings(points), config or AlertConfig(), now)


def kinds(result):
    return [n.kind for n in result.notifications]


# --- blips and the 20-minute limit -------------------------------------------------


def test_single_reading_blip_never_alerts():
    result = run(every_5_min([6.0, 8.9, 6.5, 6.3, 6.1]))
    assert result.alerts == []
    assert result.state is TruckState.OK


def test_out_of_range_for_15_minutes_does_not_alert():
    result = run(every_5_min([6.0, 9.0, 9.0, 9.0, 9.0, 6.0, 6.0]))  # 08:05-08:20
    assert result.alerts == []


def test_exactly_20_minutes_alerts_at_the_20_minute_reading():
    result = run(every_5_min([6.0, 9.0, 9.0, 9.0, 9.0, 9.0]))  # 08:05-08:25
    [alert] = result.alerts
    assert alert.opened_at == at(25)
    assert alert.duration_min == 20
    assert result.state is TruckState.ALERT
    assert kinds(result) == [NotificationKind.ALERT_OPENED]


def test_exactly_on_the_limit_is_in_range():
    result = run(every_5_min([8.0] * 12))
    assert result.alerts == []
    assert result.state is TruckState.OK


def test_too_cold_alerts_too():
    result = run(every_5_min([1.5] * 5))
    [alert] = result.alerts
    assert alert.direction == "below"
    assert alert.limit_c == 2.0
    assert alert.peak_c == 1.5


def test_exactly_2_degrees_is_in_range():
    assert run(every_5_min([2.0] * 12)).alerts == []


def test_duration_uses_timestamps_not_reading_count():
    # Only 3 readings, but they span 20 minutes.
    result = run([(0, 9.0), (10, 9.0), (20, 9.0)])
    [alert] = result.alerts
    assert alert.duration_min == 20
    assert alert.reading_count == 3


# --- dips, resets and recovery -----------------------------------------------------


def test_sawtooth_around_the_limit_still_alerts():
    # 8.5 / 7.9 / 8.5 ... : each dip is a single reading, so the timer keeps running.
    result = run(every_5_min([8.5, 7.9, 8.5, 7.9, 8.5]))
    [alert] = result.alerts
    assert alert.opened_at == at(20)


def test_suspect_resets_after_10_minutes_in_range():
    # 5-min blip, 10 min in range, then 15 min out: no alert (T-04's early blip shape).
    result = run(every_5_min([9.0, 9.0, 6.0, 6.0, 9.0, 9.0, 9.0, 9.0]))
    assert result.alerts == []
    assert result.state is TruckState.SUSPECT


def test_recovery_needs_two_in_range_readings_even_after_10_minutes():
    # Alert, then a single in-range reading 10 min after the last warm one.
    result = run([*every_5_min([9.0] * 5), (30, 6.0)])
    assert result.state is TruckState.RECOVERING
    assert result.alerts[0].status is AlertStatus.RECOVERING


def test_alert_closes_after_10_minutes_back_in_range():
    result = run(every_5_min([9.0, 9.0, 9.0, 9.0, 9.0, 6.0, 6.0]))  # warm until 08:20
    [alert] = result.alerts
    assert alert.status is AlertStatus.CLOSED
    assert alert.closed_at == at(30)
    assert result.state is TruckState.OK


def test_flapping_during_recovery_is_the_same_alert():
    temps = [9.0] * 5 + [7.5, 9.0, 7.5, 9.0, 7.5, 7.0, 6.5]
    result = run(every_5_min(temps))
    [alert] = result.alerts
    assert alert.started_at == at(0)
    assert alert.last_out_at == at(40)
    assert alert.closed_at == at(50)  # 08:45 and 08:50 in range, 10 min after 08:40
    # No second "opened": it keeps going back to 9.0, so the 10-min checks say "not improving".
    assert kinds(result) == [
        NotificationKind.ALERT_OPENED,
        NotificationKind.ALERT_NOT_IMPROVING,
        NotificationKind.ALERT_NOT_IMPROVING,
    ]


def test_new_episode_after_close_is_a_new_alert():
    temps = [9.0] * 5 + [6.0, 6.0] + [9.0] * 5
    result = run(every_5_min(temps))
    assert len(result.alerts) == 2
    assert result.alerts[0].alert_id != result.alerts[1].alert_id
    assert kinds(result) == [NotificationKind.ALERT_OPENED, NotificationKind.ALERT_OPENED]


def test_long_improving_alert_notifies_only_once():
    # Opens at 08:20 at 9.6 and cools 0.3-0.4 °C per 10 min: someone is fixing it.
    temps = [9.6, 9.6, 9.6, 9.6, 9.6, 9.5, 9.3, 9.1, 8.9, 8.7, 8.5]
    result = run(every_5_min(temps))
    assert kinds(result) == [NotificationKind.ALERT_OPENED]
    assert result.alerts[0].duration_min == 50


# --- missing data -------------------------------------------------------------------


def test_one_missing_reading_is_bridged_and_reported():
    # T-04's real problem: 08:25, 08:30, (08:35 missing), 08:40, 08:45.
    result = run([(25, 8.7), (30, 8.9), (40, 9.1), (45, 8.5), (50, 7.2)])
    [alert] = result.alerts
    assert alert.opened_at == at(45)
    assert alert.duration_min == 20
    assert [(g.start, g.end) for g in alert.gaps] == [(at(30), at(40))]
    assert "missing data: 08:30–08:40 (10 min)" in alert.reason()


def test_15_minute_gap_is_still_bridged_without_silence():
    result = run([(0, 9.0), (15, 9.0), (20, 9.0)])
    assert len(result.alerts) == 1
    assert result.silences == []


def test_long_gap_with_both_sides_out_of_range_is_bridged_and_flagged():
    result = run([(0, 9.0), (5, 9.0), (30, 9.0)])
    [alert] = result.alerts
    assert alert.duration_min == 30
    assert alert.missing_data_min == 25
    [silence] = result.silences
    assert silence.was_out_of_range
    assert silence.notify


def test_long_gap_with_an_in_range_side_resets_suspect():
    # Dip just before going quiet: we can't prove it stayed warm, so the timer restarts.
    result = run([(0, 9.0), (5, 9.0), (10, 7.0), (40, 9.0), (45, 9.0)])
    assert result.alerts == []
    assert result.state is TruckState.SUSPECT


def test_open_alert_never_closes_because_of_missing_data():
    # Alert, one in-range reading, then silence and a single in-range reading.
    result = run([*every_5_min([9.0] * 5), (25, 7.0), (60, 7.0)])
    assert result.alerts[0].status is AlertStatus.CLOSED  # two confirmed in-range readings
    result = run([*every_5_min([9.0] * 5), (60, 7.0)])
    assert result.alerts[0].status is AlertStatus.RECOVERING  # only one


# --- silent sensor ----------------------------------------------------------------


def test_silence_under_30_minutes_is_shown_but_not_notified():
    result = run([(0, 6.0), (5, 6.0)], now=at(25))  # 20 min quiet
    assert result.silent_now
    [silence] = result.silences
    assert not silence.notify
    assert result.notifications == []


def test_silence_of_30_minutes_notifies_once():
    result = run([(0, 6.0), (5, 6.0)], now=at(35))
    [note] = result.notifications
    assert note.kind is NotificationKind.SENSOR_SILENT
    assert note.at == at(35)
    assert note.severity is Severity.WARNING
    assert "no data for 30+ min" in note.message


def test_warm_truck_going_quiet_notifies_after_15_minutes():
    result = run([(0, 9.0), (5, 9.0)], now=at(25))
    [note] = result.notifications
    assert note.kind is NotificationKind.SENSOR_SILENT
    assert note.at == at(20)
    assert note.severity is Severity.CRITICAL
    assert "while out of range" in note.message


def test_silence_that_ended_is_recorded_with_its_end():
    result = run([(0, 6.0), (40, 6.0)], now=at(40))
    [silence] = result.silences
    assert silence.resumed_at == at(40)
    assert silence.minutes == 40
    assert not result.silent_now


def test_no_silence_when_now_is_close_to_last_reading():
    result = run([(0, 6.0), (5, 6.0)], now=at(20))  # exactly 15 min: not yet silent
    assert not result.silent_now
    assert result.silences == []


# --- severity ---------------------------------------------------------------------


def test_very_hot_episode_opens_directly_as_critical_with_one_notification():
    result = run(every_5_min([9.0, 11.5, 9.0, 9.0, 9.0]))
    [alert] = result.alerts
    assert alert.severity is Severity.CRITICAL
    assert alert.escalated_at is None
    assert kinds(result) == [NotificationKind.ALERT_OPENED]


def test_escalates_to_critical_after_60_minutes_exactly_once():
    result = run(every_5_min([9.0] * 15))  # 70 min
    [alert] = result.alerts
    assert alert.severity is Severity.CRITICAL
    assert alert.escalated_at == at(60)
    assert kinds(result).count(NotificationKind.ALERT_ESCALATED) == 1
    assert kinds(result)[-1] is NotificationKind.ALERT_ESCALATED  # nothing after Critical


# --- "not improving" updates (rule A11) -----------------------------------------------


def test_stuck_truck_gets_updates_at_30_40_50_then_critical_at_60():
    result = run(every_5_min([9.5] * 15))  # opens 08:20, stuck at 9.5 °C
    notes = result.notifications
    assert [(n.kind, n.at) for n in notes] == [
        (NotificationKind.ALERT_OPENED, at(20)),
        (NotificationKind.ALERT_NOT_IMPROVING, at(30)),
        (NotificationKind.ALERT_NOT_IMPROVING, at(40)),
        (NotificationKind.ALERT_NOT_IMPROVING, at(50)),
        (NotificationKind.ALERT_ESCALATED, at(60)),
    ]
    assert notes[1].notification_id.endswith(":update:1")
    assert notes[3].notification_id.endswith(":update:3")
    assert notes[1].message == (
        "UPDATE: Truck T-99 still above 8 °C after 30 min and not improving "
        "(9.5 → 9.5 °C since 08:20)."
    )
    assert result.alerts[0].updates_sent == 3


def test_rising_temperature_counts_as_not_improving():
    result = run(every_5_min([9.0, 9.0, 9.0, 9.0, 9.0, 9.2, 9.4]))
    assert kinds(result)[-1] is NotificationKind.ALERT_NOT_IMPROVING
    assert "(9.0 → 9.4 °C since 08:20)" in result.notifications[-1].message


def test_small_wobble_down_is_not_improving():
    # 9.5 → 9.3 is only 0.2 °C better: sensor jitter, not a fix.
    result = run(every_5_min([9.5, 9.5, 9.5, 9.5, 9.5, 9.4, 9.3]))
    assert kinds(result)[-1] is NotificationKind.ALERT_NOT_IMPROVING


def test_drop_of_exactly_0_3_counts_as_improving():
    result = run(every_5_min([9.5, 9.5, 9.5, 9.5, 9.5, 9.4, 9.2]))
    assert kinds(result) == [NotificationKind.ALERT_OPENED]


def test_compares_with_the_previous_check_not_the_start():
    # 20→30 improves 9.6 → 9.0 (no update); 30→40 flat at 9.0 (update).
    temps = [9.6, 9.6, 9.6, 9.6, 9.6, 9.3, 9.0, 9.0, 9.0]
    result = run(every_5_min(temps))
    assert [(n.kind, n.at) for n in result.notifications][1:] == [
        (NotificationKind.ALERT_NOT_IMPROVING, at(40))
    ]


def test_too_cold_truck_not_warming_up_gets_updates():
    result = run(every_5_min([1.0] * 7))
    assert kinds(result) == [NotificationKind.ALERT_OPENED, NotificationKind.ALERT_NOT_IMPROVING]
    assert "still below 2 °C after 30 min" in result.notifications[-1].message


def test_no_update_while_recovering():
    # Opens 08:20, back in range from 08:25; the 08:30 check never happens out of range.
    result = run(every_5_min([9.0] * 5 + [7.5, 7.5]))
    assert kinds(result) == [NotificationKind.ALERT_OPENED]


def test_check_after_a_gap_happens_at_the_next_reading():
    # Opens 08:20; readings resume at 08:35 (gap 15 min), still 9.0: one update, not two.
    result = run([*every_5_min([9.0] * 5), (35, 9.0), (40, 9.0)])
    updates = [n for n in result.notifications if n.kind is NotificationKind.ALERT_NOT_IMPROVING]
    assert [n.at for n in updates] == [at(35)]


def test_update_interval_and_threshold_are_configurable():
    points = every_5_min([9.5] * 9)  # 40 min
    assert run(points).alerts[0].updates_sent == 2
    assert run(points, config=AlertConfig(update_every_min=20)).alerts[0].updates_sent == 1


def test_escalates_when_temperature_climbs_past_11():
    result = run(every_5_min([9.0, 9.0, 9.0, 9.0, 9.0, 10.0, 11.0]))
    assert result.alerts[0].escalated_at == at(30)


def test_notification_ids_are_stable_across_runs():
    points = every_5_min([9.0] * 15)
    first = [n.notification_id for n in run(points).notifications]
    second = [n.notification_id for n in run(points).notifications]
    assert first == second
    assert len(set(first)) == len(first)


# --- config -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"min_safe_c": 8.0, "max_safe_c": 2.0},
        {"alert_after_min": 0},
        {"clear_min_readings": 0},
        {"improve_min_c": -0.1},
        {"update_every_min": 0},
        {"silent_notify_min": 10},
        {"sensor_max_c": 7.0},
    ],
)
def test_bad_config_is_refused(kwargs):
    with pytest.raises(ValueError):
        AlertConfig(**kwargs)


def test_limit_is_configurable():
    points = every_5_min([6.0, 9.0, 9.0, 9.0, 9.0, 9.0, 6.0, 6.0])  # 20 min out
    assert len(run(points).alerts) == 1
    assert run(points, config=AlertConfig(alert_after_min=25)).alerts == []
