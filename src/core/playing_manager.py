from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from core.models import SongStorable
from core.weighted_random import AdvancedRandom

PlayMode = Literal['Repeat one', 'Repeat list', 'Shuffle', 'Play in order']


@dataclass(frozen=True)
class PlaySelection:
    index: int
    song: SongStorable
    mode: PlayMode
    by_user: bool
    base_index: int


class PlayingManager:
    def __init__(self) -> None:
        self.playlist: list[SongStorable] = []
        self.current_index = -1
        self._randomer = AdvancedRandom[SongStorable]()
        self._reserved_next: PlaySelection | None = None

    def setPlaylist(self, playlist: list[SongStorable]) -> None:
        self.playlist = playlist
        self.refreshRandom()
        self.clearReservedNext()

    def refreshRandom(self) -> None:
        self._randomer.init(self.playlist)

    def setCurrentIndex(self, index: int) -> None:
        self.current_index = index
        self.clearReservedNext()

    def clearReservedNext(self) -> None:
        self._reserved_next = None

    def isSelectionCurrent(self, selection: PlaySelection | None) -> bool:
        if selection is None:
            return False
        if self.current_index != selection.base_index:
            return False
        if selection.index < 0 or selection.index >= len(self.playlist):
            return False
        return self.playlist[selection.index] is selection.song

    def getNextSelection(
        self,
        mode: PlayMode,
        by_user: bool = False,
        reserve: bool = False,
    ) -> PlaySelection | None:
        if (
            self._reserved_next is not None
            and self._reserved_next.mode == mode
            and self._reserved_next.by_user == by_user
            and self.isSelectionCurrent(self._reserved_next)
        ):
            return self._reserved_next

        selection = self._buildNextSelection(mode, by_user)
        if reserve:
            self._reserved_next = selection
        return selection

    def getNextSong(
        self,
        mode: PlayMode,
        by_user: bool = False,
        reserve: bool = False,
    ) -> SongStorable | None:
        selection = self.getNextSelection(mode, by_user, reserve)
        return selection.song if selection is not None else None

    def consumeNextSelection(
        self,
        mode: PlayMode,
        by_user: bool = False,
    ) -> PlaySelection | None:
        selection = self.getNextSelection(mode, by_user)
        if selection is None:
            return None

        self.current_index = selection.index
        self.clearReservedNext()
        return selection

    def getPreviousSelection(self, mode: PlayMode) -> PlaySelection | None:
        if not self.playlist or self.current_index < 0:
            return None

        if self.current_index > 0:
            index = self.current_index - 1
        elif mode == 'Repeat list':
            index = len(self.playlist) - 1
        else:
            return None

        return PlaySelection(
            index=index,
            song=self.playlist[index],
            mode=mode,
            by_user=True,
            base_index=self.current_index,
        )

    def consumePreviousSelection(self, mode: PlayMode) -> PlaySelection | None:
        selection = self.getPreviousSelection(mode)
        if selection is None:
            return None

        self.current_index = selection.index
        self.clearReservedNext()
        return selection

    def _buildNextSelection(
        self,
        mode: PlayMode,
        by_user: bool,
    ) -> PlaySelection | None:
        if not self.playlist:
            return None

        if self.current_index < 0 or self.current_index >= len(self.playlist):
            if mode == 'Play in order':
                return None
            index = 0
            return self._makeSelection(index, mode, by_user)

        if mode == 'Repeat one' and not by_user:
            return self._makeSelection(self.current_index, mode, by_user)

        if mode == 'Shuffle':
            return self._makeShuffleSelection(mode, by_user)

        next_index = self.current_index + 1
        if next_index < len(self.playlist):
            return self._makeSelection(next_index, mode, by_user)

        if mode == 'Repeat list':
            return self._makeSelection(0, mode, by_user)

        return None

    def _makeShuffleSelection(
        self,
        mode: PlayMode,
        by_user: bool,
    ) -> PlaySelection | None:
        if len(self.playlist) == 1:
            return self._makeSelection(0, mode, by_user)

        start_index = self.current_index
        for _ in range(len(self.playlist) * 2):
            song = self._randomer.random()
            try:
                index = self.playlist.index(song)
            except ValueError:
                self.refreshRandom()
                continue
            if index != start_index:
                return self._makeSelection(index, mode, by_user)

        index = (start_index + 1) % len(self.playlist)
        return self._makeSelection(index, mode, by_user)

    def _makeSelection(
        self,
        index: int,
        mode: PlayMode,
        by_user: bool,
    ) -> PlaySelection:
        return PlaySelection(
            index=index,
            song=self.playlist[index],
            mode=mode,
            by_user=by_user,
            base_index=self.current_index,
        )
