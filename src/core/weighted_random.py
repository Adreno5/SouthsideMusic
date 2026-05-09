import logging
import random

from typing import Any, Generic, TypeVar
_logger = logging.getLogger(__name__)

T = TypeVar("T")


class AdvancedRandom(Generic[T]):
    def __init__(self) -> None:
        self.list_len: int = 0
        self.list_weight: list[float] = []
        self.randomed_times: list[int] = []
        self.target_lst: list[T] = []

    def init(self, lst: list[T]):
        if not lst:
            return
        self.list_len = len(lst)
        self.list_weight = [(1 / self.list_len) for _ in lst]
        self.randomed_times = [0 for _ in lst]
        self.target_lst = lst
        _logger.info(f"inited {self.list_len} weights avg {self.list_weight[0]}")

    def random(self) -> T:
        total_weight: float = 0
        adjusted_weights = []

        for i, times in enumerate(self.randomed_times):
            weight = self.list_weight[i] / (times + 1)
            adjusted_weights.append(weight)
            total_weight += weight

        if total_weight <= 0:
            total_weight = len(self.target_lst)
            adjusted_weights = [1.0 for _ in self.target_lst]

        randomed = random.random() * total_weight
        current: float = 0

        for i, weight in enumerate(adjusted_weights):
            current += weight
            if randomed <= current:
                selected_item = self.target_lst[i]
                self.randomed_times[i] += 1
                _logger.info(f"randomed {selected_item} times {self.randomed_times[i]}")
                _logger.debug(self.randomed_times)
                _logger.debug(adjusted_weights)
                return selected_item

        selected_item = self.target_lst[-1]
        self.randomed_times[-1] += 1
        _logger.info(f"randomed {selected_item} times {self.randomed_times[-1]}")
        _logger.debug(self.randomed_times)
        _logger.debug(adjusted_weights)
        return selected_item
