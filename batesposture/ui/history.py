"""Local posture-history viewer: a lookback-filtered report over logged
scores, replacing the one-off "Stance" web report with a window inside
this app. Reads from the existing SQLite database; never talks to a
server."""

from __future__ import annotations

import logging
from datetime import datetime

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..data.database import Database, DatabaseInitializationError
from ..services import history_report as hr
from ..services.settings_service import SettingsService

logger = logging.getLogger(__name__)

BG = "#0c0c0d"
FG = "#e7e2da"
GRID = "#2a2a2d"
SIT_COLOR = "#c9b8a8"
STAND_COLOR = "#8aa4b0"
WEEKDAY_LABELS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


class HistoryDialog(QDialog):
    def __init__(self, settings: SettingsService, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("BatesPosture · History")
        self.resize(980, 780)
        self.setStyleSheet(f"background: {BG}; color: {FG};")

        self._rows: list[hr.HistoryRow] = self._load_from_database()

        layout = QVBoxLayout(self)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Lookback:"))
        self.lookback_combo = QComboBox()
        self.lookback_combo.addItems(list(hr.LOOKBACKS))
        self.lookback_combo.setCurrentText("Last 7 days")
        self.lookback_combo.currentTextChanged.connect(self._refresh)
        controls.addWidget(self.lookback_combo)
        controls.addStretch(1)

        add_export_btn = QPushButton("Add export…")
        add_export_btn.clicked.connect(self._add_export)
        controls.addWidget(add_export_btn)

        export_btn = QPushButton("Export history CSV")
        export_btn.clicked.connect(self._export_history)
        controls.addWidget(export_btn)
        layout.addLayout(controls)

        self.empty_label = QLabel()
        self.empty_label.setWordWrap(True)
        layout.addWidget(self.empty_label)

        stats_row = QHBoxLayout()
        self.stat_cards: dict[str, QLabel] = {}
        for key, title in (
            ("overall", "Overall"),
            ("sit", "Sitting"),
            ("stand", "Standing"),
            ("delta", "Early → late"),
        ):
            card = QLabel(title)
            card.setAlignment(Qt.AlignmentFlag.AlignCenter)
            card.setStyleSheet(
                f"background: #17171a; border: 1px solid {GRID}; "
                "border-radius: 6px; padding: 8px;"
            )
            self.stat_cards[key] = card
            stats_row.addWidget(card)
        layout.addLayout(stats_row)

        self.figure = Figure(figsize=(9, 6.5), facecolor=BG)
        self.canvas = FigureCanvasQTAgg(self.figure)
        layout.addWidget(self.canvas, stretch=1)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Timestamp", "Score", "Mode", "Note"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMaximumHeight(220)
        layout.addWidget(self.table)

        self._refresh()

    # -- data loading ---------------------------------------------------
    def _load_from_database(self) -> list[hr.HistoryRow]:
        try:
            database = Database.from_settings(self._settings)
        except DatabaseInitializationError:
            logger.exception("Could not open database for history view")
            return []
        try:
            return hr.parse_rows(database.fetch_scores())
        finally:
            database.close()

    def _add_export(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Add BatesPosture export", "", "CSV files (*.csv)"
        )
        if not path:
            return
        try:
            new_rows = hr.load_csv(path)
        except OSError as exc:
            QMessageBox.warning(self, "Could not read file", str(exc))
            return
        self._rows = hr.merge_dedupe(self._rows, new_rows)
        self._refresh()

    def _export_history(self) -> None:
        if not self._rows:
            QMessageBox.information(
                self, "Nothing to export", "There is no history to export yet."
            )
            return
        default_name = f"posture_history_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export history CSV", default_name, "CSV files (*.csv)"
        )
        if not path:
            return
        try:
            hr.export_csv(self._rows, path)
        except OSError as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
            return
        QMessageBox.information(self, "Export complete", f"History exported to:\n{path}")

    # -- rendering --------------------------------------------------------
    def _refresh(self, *_args) -> None:
        if not self._rows:
            interval = self._settings.runtime.db_write_interval_seconds
            self.empty_label.setText(
                "No posture history yet. Turn on Database Logging (tray menu → "
                f"Settings → Data) to start collecting it — scores save every "
                f"{interval}s while tracking is on."
            )
            self.empty_label.show()
        else:
            self.empty_label.hide()

        label = self.lookback_combo.currentText()
        visible = hr.filter_lookback(self._rows, label)
        self._update_stat_cards(hr.compute_stats(visible))
        self._update_charts(visible, label)
        self._update_table(visible)

    def _update_stat_cards(self, stats: dict) -> None:
        def fmt(agg):
            if not agg:
                return "—"
            return f"{agg['avg']:.0f} avg ({agg['count']} pts)\n{agg['min']:.0f}–{agg['max']:.0f}"

        self.stat_cards["overall"].setText(f"Overall\n{fmt(stats['overall'])}")
        self.stat_cards["sit"].setText(f"Sitting\n{fmt(stats['sit'])}")
        self.stat_cards["stand"].setText(f"Standing\n{fmt(stats['stand'])}")
        delta = stats["delta"]
        delta_text = "—" if delta is None else f"{delta:+.1f}"
        self.stat_cards["delta"].setText(f"Early → late\n{delta_text}")

    def _update_charts(self, rows: list[hr.HistoryRow], label: str) -> None:
        self.figure.clear()
        self.figure.set_facecolor(BG)
        if not rows:
            self.canvas.draw()
            return

        bucket = hr.bucket_minutes_for(rows, label)
        timeline = hr.timeseries_by_mode(rows, bucket)
        daily = hr.daily_means(rows)
        hourly = hr.hour_of_day_means(rows)
        heat = hr.weekday_hour_heatmap(rows)
        hist = hr.histogram(rows)

        gs = self.figure.add_gridspec(
            3, 3, height_ratios=[2, 1.3, 1.3], hspace=0.6, wspace=0.45
        )

        ax_time = self.figure.add_subplot(gs[0, :])
        self._style_axes(ax_time)
        for mode, color in (("sit", SIT_COLOR), ("stand", STAND_COLOR)):
            series = timeline[mode]
            if series:
                xs, ys = zip(*series)
                ax_time.plot(xs, ys, color=color, label=mode.capitalize(), linewidth=1.6)
        ax_time.set_title("Score over time", color=FG, fontsize=10)
        ax_time.legend(facecolor=BG, labelcolor=FG, fontsize=8, framealpha=0)

        ax_daily = self.figure.add_subplot(gs[1, 0])
        self._style_axes(ax_daily)
        for mode, color in (("sit", SIT_COLOR), ("stand", STAND_COLOR)):
            series = daily[mode]
            if series:
                xs, ys = zip(*series)
                ax_daily.plot(xs, ys, color=color, marker="o", markersize=3, linewidth=1.2)
        ax_daily.set_title("Daily mean", color=FG, fontsize=9)
        ax_daily.tick_params(axis="x", rotation=45, labelsize=6)

        ax_hourly = self.figure.add_subplot(gs[1, 1])
        self._style_axes(ax_hourly)
        for mode, color in (("sit", SIT_COLOR), ("stand", STAND_COLOR)):
            series = hourly[mode]
            if series:
                xs, ys = zip(*series)
                ax_hourly.plot(xs, ys, color=color, marker="o", markersize=3, linewidth=1.2)
        ax_hourly.set_title("Hour-of-day mean", color=FG, fontsize=9)
        ax_hourly.set_xlim(0, 23)

        ax_hist = self.figure.add_subplot(gs[1, 2])
        self._style_axes(ax_hist)
        for mode, color in (("sit", SIT_COLOR), ("stand", STAND_COLOR)):
            series = hist[mode]
            if series:
                xs = [b for b, _ in series]
                ys = [c for _, c in series]
                ax_hist.bar(xs, ys, width=8, color=color, alpha=0.65, label=mode.capitalize())
        ax_hist.set_title("Score histogram", color=FG, fontsize=9)

        ax_heat_sit = self.figure.add_subplot(gs[2, 0:2])
        self._render_heatmap(ax_heat_sit, heat["sit"], "Sitting — weekday x hour")

        ax_heat_stand = self.figure.add_subplot(gs[2, 2])
        self._render_heatmap(ax_heat_stand, heat["stand"], "Standing")

        self.canvas.draw()

    def _style_axes(self, ax) -> None:
        ax.set_facecolor(BG)
        ax.tick_params(colors=FG, labelsize=7)
        for spine in ax.spines.values():
            spine.set_color(GRID)
        ax.grid(color=GRID, linewidth=0.5, alpha=0.5)

    def _render_heatmap(self, ax, grid: list[list[float | None]], title: str) -> None:
        self._style_axes(ax)
        data = np.array([[v if v is not None else np.nan for v in day] for day in grid])
        ax.imshow(data, aspect="auto", cmap="magma", vmin=0, vmax=100)
        ax.set_yticks(range(7))
        ax.set_yticklabels(WEEKDAY_LABELS, fontsize=6)
        ax.set_title(title, color=FG, fontsize=8)

    def _update_table(self, rows: list[hr.HistoryRow]) -> None:
        recent = rows[-40:]
        self.table.setRowCount(len(recent))
        for i, row in enumerate(reversed(recent)):
            self.table.setItem(i, 0, QTableWidgetItem(row.dt.strftime("%Y-%m-%d %H:%M:%S")))
            self.table.setItem(i, 1, QTableWidgetItem(f"{row.score:.1f}"))
            self.table.setItem(i, 2, QTableWidgetItem(row.mode))
            note = "Lost pose" if row.lost else ""
            self.table.setItem(i, 3, QTableWidgetItem(note))
