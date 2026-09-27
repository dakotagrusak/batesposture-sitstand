from __future__ import annotations

from datetime import datetime, timedelta

from ..data.database import Database
from ..services import history_report as hr
from ..services.settings_service import SettingsService
from ..ui.history import HistoryDialog


def test_history_dialog_shows_empty_state_with_no_rows(qapp, tmp_path):
    settings = SettingsService.for_testing(tmp_path / "history_settings.ini")

    dialog = HistoryDialog(settings)
    dialog.show()
    qapp.processEvents()

    assert dialog.empty_label.isVisible()
    assert "Database Logging" in dialog.empty_label.text()
    assert dialog.table.rowCount() == 0
    dialog.close()


def test_history_dialog_loads_rows_and_splits_modes(qapp, tmp_path):
    settings = SettingsService.for_testing(tmp_path / "history_rows_settings.ini")
    database = Database.from_settings(settings)
    database.cursor.executemany(
        "INSERT INTO posture_scores (timestamp, score, mode) VALUES (?, ?, ?)",
        [
            ("2026-01-01T09:00:00", 60.0, "sit"),
            ("2026-01-01T10:00:00", 90.0, "stand"),
        ],
    )
    database.cursor.connection.commit()
    database.close()

    dialog = HistoryDialog(settings)
    dialog.show()
    dialog.lookback_combo.setCurrentText("All history")
    qapp.processEvents()

    assert not dialog.empty_label.isVisible()
    assert dialog.table.rowCount() == 2
    assert "75" in dialog.stat_cards["overall"].text()  # (60 + 90) / 2
    assert "60" in dialog.stat_cards["sit"].text()
    assert "90" in dialog.stat_cards["stand"].text()
    dialog.close()


def test_candle_tab_renders_for_both_chart_types_and_intervals(qapp, tmp_path):
    settings = SettingsService.for_testing(tmp_path / "history_candles_settings.ini")
    database = Database.from_settings(settings)
    now = datetime.now()
    rows = [
        ((now - timedelta(minutes=i)).isoformat(), 60.0 + (i % 7), "sit" if i % 2 else "stand")
        for i in range(60)
    ]
    database.cursor.executemany(
        "INSERT INTO posture_scores (timestamp, score, mode) VALUES (?, ?, ?)", rows
    )
    database.cursor.connection.commit()
    database.close()

    dialog = HistoryDialog(settings)
    dialog.show()
    dialog.lookback_combo.setCurrentText("All history")
    qapp.processEvents()

    # Distribution tab defaults to a sit/stand scatter; interval is candles-only.
    assert dialog.tabs.tabText(1) == "Distribution"
    assert dialog.chart_type_combo.currentText() == "Scatter"
    assert len(dialog.candle_figure.axes) == 2
    assert dialog.candle_figure.axes[0].get_ylim() == (0, 100)
    assert dialog.interval_combo.isHidden()
    dialog.chart_type_combo.setCurrentText("Candlestick")
    qapp.processEvents()
    assert not dialog.interval_combo.isHidden()

    for interval_label in ("1 minute", "5 minutes"):
        dialog.interval_combo.setCurrentText(interval_label)
        for chart_type in ("Candlestick", "Scatter"):
            dialog.chart_type_combo.setCurrentText(chart_type)
            qapp.processEvents()
            assert len(dialog.candle_figure.axes) > 0

    dialog.close()


def test_shape_tab_draws_a_violin_per_group_for_every_grouping(qapp, tmp_path):
    settings = SettingsService.for_testing(tmp_path / "history_shape_settings.ini")
    database = Database.from_settings(settings)
    now = datetime.now()
    rows = [
        (
            (now - timedelta(minutes=i)).isoformat(),
            40.0 + (i % 50),
            "sit" if i % 3 else "stand",
        )
        for i in range(120)
    ]
    database.cursor.executemany(
        "INSERT INTO posture_scores (timestamp, score, mode) VALUES (?, ?, ?)", rows
    )
    database.cursor.connection.commit()
    database.close()

    dialog = HistoryDialog(settings)
    dialog.show()
    dialog.lookback_combo.setCurrentText("All history")
    qapp.processEvents()

    assert dialog.tabs.tabText(3) == "Shape"
    for grouping in hr.SHAPE_GROUPINGS:
        dialog.shape_group_combo.setCurrentText(grouping)
        qapp.processEvents()
        (ax,) = dialog.shape_figure.axes
        labels = [t.get_text() for t in ax.get_xticklabels()]
        assert labels[0].startswith("All\nn=120")
        assert all("n=" in label for label in labels)
    dialog.close()
