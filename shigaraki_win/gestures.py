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
