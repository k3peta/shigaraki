from __future__ import annotations


# Keep defined/OEM VKs; reserved/unassigned slots can report unrelated state.
# WinUser.h defines navigation keys at 0x88..0x8F; kbd.h defines ABNT C1/C2.
# Preserve these and all IME, mouse/gamepad and OEM chord protection.
# https://github.com/microsoft/win32metadata/tree/main/generation/WinSDK/RecompiledIdlHeaders/um
SHORTCUT_KEYS = (
    *range(0x01, 0x07), 0x08, 0x09, 0x0C, 0x0D,
    *range(0x10, 0x3A), *range(0x41, 0x5E), *range(0x5F, 0x97),
    *range(0xA0, 0xB8), *range(0xBA, 0xE0),
    *range(0xE1, 0xE8), *range(0xE9, 0xFF),
)


class ControlGesture:
    """Isolated holds and double taps; never stop on a typing/chord event."""

    def __init__(self):
        self.pressed_at = None
        self.first_tap = None
        self.holding = False
        self.blocked = False
        self.released_at = None

    def update(self, now: float, down: bool, isolated: bool, *, busy: bool, allow_hold: bool) -> list[str]:
        if busy:
            self.first_tap = None
        if down:
            self.released_at = None
            if self.pressed_at is None:
                self.pressed_at = now
                self.blocked = not isolated or (busy and not allow_hold)
            if not isolated:
                self.first_tap = None
                if not self.holding:
                    self.blocked = True
            if allow_hold and not self.holding and not self.blocked and now - self.pressed_at >= 0.24:
                self.holding = True
                self.first_tap = None
                return ["hold_start"]
            return []
        if self.pressed_at is None:
            return []
        if self.holding:
            if self.released_at is None:
                self.released_at = now
            if now - self.released_at < 0.07:
                return []
            action = ["hold_end"]
        elif not busy and not self.blocked and isolated and now - self.pressed_at < 0.23:
            if self.first_tap is not None and self.pressed_at - self.first_tap <= 0.35:
                action = ["double_tap"]
                self.first_tap = None
            else:
                self.first_tap = now
                action = []
        else:
            self.first_tap = None
            action = []
        self.pressed_at = None
        self.released_at = None
        self.holding = False
        self.blocked = False
        return action


class WindowGesture:
    """Single/double taps for the visible editor, with one action per press.

    Idle singles wait for a possible second tap. Recording singles are emitted
    as soon as key-up is stable, while their second-tap opportunity remains
    alive through finalization. A fresh busy tap can arm only a double, never
    a later idle single.
    The caller must reset this classifier when its window context changes.
    """

    DOUBLE_SECONDS = 0.35
    SHORT_SECONDS = 0.23
    RELEASE_SECONDS = 0.07

    def __init__(self):
        self.reset()

    def reset(self, *, block_until_up: bool = False):
        self.pressed_at = None
        self.released_at = None
        self.first_tap = None
        self.blocked = block_until_up
        self._wait_for_up = block_until_up
        self._pending_single = False
        self._press_can_toggle = False
        self._press_recording = False
        self._second = False

    @property
    def pending(self) -> bool:
        """True while isolation checks still matter, including a release gap."""
        return self.pressed_at is not None or self.first_tap is not None

    def _clear_tap(self):
        self.first_tap = None
        self._pending_single = False

    def _expire(self, now, can_toggle, recording):
        if self.first_tap is None or now - self.first_tap <= self.DOUBLE_SECONDS:
            return []
        emit = self._pending_single and can_toggle and not recording
        self._clear_tap()
        return ["single_tap"] if emit else []

    def _finish_press(self, can_toggle, recording):
        released_at = self.released_at
        short = released_at - self.pressed_at < self.SHORT_SECONDS
        second = self._second and short and self.first_tap is not None
        eligible = (self._press_can_toggle and can_toggle and
                    self._press_recording == recording)
        blocked = self.blocked
        self.pressed_at = self.released_at = None
        self.blocked = self._second = False
        self._clear_tap()
        if blocked:
            return []
        if second:
            return ["double_tap"]
        if not eligible:
            if short and not recording and self._press_recording == recording:
                self.first_tap = released_at
            return []
        if not short:
            return ["single_tap"]
        self.first_tap = released_at
        self._pending_single = not recording
        return ["single_tap"] if recording else []

    def update(self, now: float, down: bool, isolated: bool, *,
               can_toggle: bool, recording: bool) -> list[str]:
        if self._wait_for_up:
            if down:
                self.released_at = None
            elif self.released_at is None:
                self.released_at = now
            elif now - self.released_at >= self.RELEASE_SECONDS:
                self.released_at = None
                self._wait_for_up = self.blocked = False
            return []
        actions = []
        if not isolated:
            self._clear_tap()
            if self.pressed_at is not None:
                self.blocked = True
        # An idle tap must not fire after another operation changed the mode.
        # A recording-stop tap has already fired and keeps only its double.
        if self._pending_single and (not can_toggle or recording):
            self._clear_tap()
        if self.pressed_at is not None and (not can_toggle or recording != self._press_recording):
            self._press_can_toggle = False
        if self.released_at is not None and now - self.released_at >= self.RELEASE_SECONDS:
            actions += self._finish_press(can_toggle, recording)
        if down:
            if self.pressed_at is None:
                actions += self._expire(now, can_toggle, recording)
                self.pressed_at = now
                self._press_can_toggle = can_toggle
                self._press_recording = recording
                self._second = (self.first_tap is not None and
                                now - self.first_tap <= self.DOUBLE_SECONDS)
                self.blocked = not isolated
            self.released_at = None
        elif self.pressed_at is not None:
            if self.released_at is None:
                self.released_at = now
        else:
            actions += self._expire(now, can_toggle, recording)
        return actions
