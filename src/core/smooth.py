import time
from PySide6.QtCore import QObject
from time import perf_counter_ns

from services.events import event_bus, REFRESH_RATE_CHANGED
from imports import QApplication, QTimer
from core.models import AnimatingObject
import logging

_NANOSECONDS_PER_SECOND = 1_000_000_000


class _BaseSmoothTimer:
    def __init__(self, animation_time: float, power_number: int) -> None:
        self._target_value = 0.0
        self._current_value = 0.0
        self._difference = 0.0
        self._last_update = perf_counter_ns()
        self.anim_cycle = animation_time
        self.power_number = power_number

    def getDebugInfo(self) -> list[str]:
        return [
            '_BaseSmoothTimer (',
            f'  target_value={self._target_value:.3f}',
            f'  current_value={self._calculate_current_value():.3f}',
            f'  difference={self._difference:.3f}',
            ')',
        ]

    @property
    def target_value(self) -> float:
        return self._target_value

    @target_value.setter
    def target_value(self, value: float) -> None:
        if value != self._target_value:
            actual_current_value = self._calculate_current_value()
            self._difference = value - actual_current_value
            self._current_value = actual_current_value
            self._target_value = value
            self._last_update = perf_counter_ns()

    @property
    def current_value(self) -> float:
        calculated_value = self._calculate_current_value()
        actual = self._anim_cycle

        if self._elapsed_time >= actual or actual <= 0:
            self._current_value = self._target_value
            self._difference = 0.0

        return calculated_value

    @current_value.setter
    def current_value(self, value: float) -> None:
        if value != self._current_value:
            self._difference = self._target_value - value
            self._current_value = value
            self._last_update = perf_counter_ns()

    @property
    def anim_cycle(self) -> float:
        return self._anim_cycle

    @anim_cycle.setter
    def anim_cycle(self, value: float) -> None:
        self._anim_cycle = max(0.001, value)

    @property
    def power_number(self) -> int:
        return self._power_number

    @power_number.setter
    def power_number(self, value: int) -> None:
        self._power_number = max(1, value)

    @property
    def is_animating(self) -> bool:
        return (
            self._elapsed_time < self._anim_cycle
            and self._anim_cycle > 0
            and self._difference != 0.0
        )

    @property
    def animation_progress(self) -> float:
        if self._anim_cycle <= 0 or self._difference == 0.0:
            return 1.0

        return min(self._elapsed_time / self._anim_cycle, 1.0)

    @property
    def _elapsed_time(self) -> float:
        return (perf_counter_ns() - self._last_update) / _NANOSECONDS_PER_SECOND

    def reset(self) -> None:
        self._current_value = 0.0
        self._target_value = 0.0
        self._difference = 0.0
        self._last_update = perf_counter_ns()

    def _calculate_current_value(self) -> float:
        elapsed = self._elapsed_time

        actual = self._anim_cycle

        if elapsed >= actual or actual <= 0:
            return self._target_value

        progress = elapsed / actual
        return self._current_value + self._difference * self._ease_progress(progress)

    def _ease_progress(self, progress: float) -> float:
        raise NotImplementedError


class EaseOutBackTimer(_BaseSmoothTimer):
    def _ease_progress(self, progress: float) -> float:
        return (1.0 - pow(1.0 - progress, self._power_number)) * 1.5 - progress * 0.5


class EaseOutTimer(_BaseSmoothTimer):
    def _ease_progress(self, progress: float) -> float:
        return 1.0 - pow(1.0 - progress, self._power_number)


class EaseInOutTimer(_BaseSmoothTimer):
    def _ease_progress(self, progress: float) -> float:
        if progress < 0.5:
            return pow(2.0 * progress, self._power_number) / 2.0

        return 1.0 - pow(2.0 * (1.0 - progress), self._power_number) / 2.0


class SScrollTimer(QObject):
    def __init__(self, duration: int = 250):
        super().__init__()
        self._logger = logging.getLogger(__name__)
        self.animating_objs: list[AnimatingObject] = []
        self.refresh_rate = max(60, QApplication.primaryScreen().refreshRate() / 2)
        self._logger.info(f'{self.refresh_rate=}')
        self.delta = 1 / self.refresh_rate
        self.last_draw: int = time.perf_counter_ns()
        self._scroll_remainder = 0.0
        self.debug_forces: list[float] = []
        self.debug_total_force = 0.0
        self.debug_offset = 0.0
        self.debug_offset_target = 0.0
        self.duration = duration

        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self._tick)
        self.anim_timer.start(max(1, int(1000 / self.refresh_rate)))

        self._value = 0

        event_bus.subscribe(REFRESH_RATE_CHANGED, self._onRefreshRateChanged)

    def getValue(self):
        return self._value

    def setValue(self, v):
        self._value = v

    def _onRefreshRateChanged(self):
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        self.refresh_rate = max(60, screen.refreshRate() / 2)
        self._logger.info(f'{self.refresh_rate=}')
        self.delta = 1 / self.refresh_rate
        self.anim_timer.setInterval(max(1, int(1000 / self.refresh_rate)))

    @staticmethod
    def _smoothstep(t: float) -> float:
        t = max(0.0, min(1.0, t))
        return t * t * (3.0 - 2.0 * t)

    def _tick(self):
        now = time.perf_counter_ns()
        elapsed = min((now - self.last_draw) / 1_000_000_000, 0.1)
        self.last_draw = now
        multiple_factor = elapsed * self.refresh_rate

        new: list[AnimatingObject] = []
        total_delta = 0.0
        forces: list[float] = []
        for obj in self.animating_objs:
            obj.elapsed += self.delta * 1000 * multiple_factor
            progress = self._smoothstep(obj.elapsed / obj.duration)
            force = obj.total * (progress - obj.last_progress)
            forces.append(force)
            total_delta += force
            obj.last_progress = progress
            if obj.elapsed < obj.duration:
                new.append(obj)
        self.animating_objs = new
        if total_delta != 0:
            next_value = self._value + total_delta + self._scroll_remainder
            final_value = int(next_value)
            self._scroll_remainder = next_value - final_value
            self._value = final_value

    def scrollValue(self, delta: int):
        self.animating_objs.append(
            AnimatingObject(
                total=float(delta),
                elapsed=0.0,
                duration=self.duration,
                last_progress=0.0,
            )
        )

    def scrollTo(self, target: int):
        self.animating_objs.append(
            AnimatingObject(
                total=target - self._value,
                elapsed=0.0,
                duration=self.duration,
                last_progress=0.0,
            )
        )

    def reset(self):
        self.animating_objs.clear()
