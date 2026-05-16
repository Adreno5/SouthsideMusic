from time import perf_counter_ns

_NANOSECONDS_PER_SECOND = 1_000_000_000


class _BaseSmoothTimer:
    def __init__(self, animation_time: float, power_number: int) -> None:
        self._target_value = 0.0
        self._current_value = 0.0
        self._difference = 0.0
        self._last_update = perf_counter_ns()
        self.anim_cycle = animation_time
        self.power_number = power_number

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

        if self._elapsed_time >= self._anim_cycle or self._anim_cycle <= 0:
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
        return self._elapsed_time < self._anim_cycle and self._anim_cycle > 0 and self._difference != 0.0

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

        if elapsed >= self._anim_cycle or self._anim_cycle <= 0:
            return self._target_value

        progress = elapsed / self._anim_cycle
        return self._current_value + self._difference * self._ease_progress(progress)

    def _ease_progress(self, progress: float) -> float:
        raise NotImplementedError


class SmoothTimer(_BaseSmoothTimer):
    def _ease_progress(self, progress: float) -> float:
        return (
            (1.0 - pow(1.0 - progress, self._power_number)) * 1.5
            - progress * 0.5
        )


class EaseOutTimer(_BaseSmoothTimer):
    def _ease_progress(self, progress: float) -> float:
        return 1.0 - pow(1.0 - progress, self._power_number)
