import unittest

from shigaraki_win.gestures import ControlGesture, WindowGesture


class WindowGestureTests(unittest.TestCase):
    def setUp(self):
        self.gesture = WindowGesture()

    def update(self, now, down=False, *, isolated=True, can_toggle=True, recording=False):
        return self.gesture.update(now, down, isolated,
                                   can_toggle=can_toggle, recording=recording)

    def first_tap(self, *, recording=False):
        self.assertEqual(self.update(0, True, recording=recording), [])
        self.assertEqual(self.update(.08, recording=recording), [])
        return self.update(.16, recording=recording)

    def test_idle_single_waits_for_double_then_fires_only_once(self):
        self.assertEqual(self.first_tap(), [])
        self.assertTrue(self.gesture.pending)
        self.assertEqual(self.update(.42), [])
        self.assertEqual(self.update(.44), ["single_tap"])
        self.assertEqual(self.update(1), [])
        self.assertFalse(self.gesture.pending)

    def test_idle_double_never_starts_recording(self):
        self.assertEqual(self.first_tap(), [])
        self.assertEqual(self.update(.20, True), [])
        self.assertEqual(self.update(.28), [])
        self.assertEqual(self.update(.36), ["double_tap"])
        self.assertEqual(self.update(.6), [])
        self.assertFalse(self.gesture.pending)

    def test_recording_single_stops_at_stable_release_without_double_wait(self):
        self.assertEqual(self.first_tap(recording=True), ["single_tap"])
        self.assertEqual(self.update(.3, can_toggle=False), [])
        self.assertEqual(self.update(.5), [])
        self.assertFalse(self.gesture.pending)

    def test_stop_then_second_tap_can_commit_while_finalizing(self):
        self.assertEqual(self.first_tap(recording=True), ["single_tap"])
        self.assertEqual(self.update(.20, True, can_toggle=False), [])
        self.assertEqual(self.update(.28, can_toggle=False), [])
        self.assertEqual(self.update(.36, can_toggle=False), ["double_tap"])
        self.assertEqual(self.update(.6), [])

    def test_stop_then_fast_finalization_does_not_reinterpret_second_tap_as_start(self):
        self.assertEqual(self.first_tap(recording=True), ["single_tap"])
        self.assertEqual(self.update(.20, True), [])
        self.assertEqual(self.update(.28), [])
        self.assertEqual(self.update(.36), ["double_tap"])
        self.assertEqual(self.update(.8), [])

    def test_finalizing_press_cannot_start_when_it_later_becomes_idle(self):
        self.assertEqual(self.update(0, True, can_toggle=False), [])
        self.assertEqual(self.update(.08, can_toggle=False), [])
        self.assertEqual(self.update(.16), [])
        self.assertEqual(self.update(.6), [])
        self.assertFalse(self.gesture.pending)

    def test_fresh_double_tap_during_finalization_can_commit_without_a_single(self):
        self.assertEqual(self.update(0, True, can_toggle=False), [])
        self.assertEqual(self.update(.08, can_toggle=False), [])
        self.assertEqual(self.update(.16, can_toggle=False), [])
        self.assertEqual(self.update(.20, True, can_toggle=False), [])
        self.assertEqual(self.update(.28, can_toggle=False), [])
        self.assertEqual(self.update(.36, can_toggle=False), ["double_tap"])
        self.assertEqual(self.update(.6), [])

    def test_finalizing_first_tap_can_complete_a_double_after_recognition_finishes(self):
        self.assertEqual(self.update(0, True, can_toggle=False), [])
        self.assertEqual(self.update(.08, can_toggle=False), [])
        self.assertEqual(self.update(.16), [])
        self.assertEqual(self.update(.20, True), [])
        self.assertEqual(self.update(.28), [])
        self.assertEqual(self.update(.36), ["double_tap"])
        self.assertEqual(self.update(.6), [])

    def test_finalizing_long_press_cannot_arm_a_double_or_single(self):
        self.assertEqual(self.update(0, True, can_toggle=False), [])
        self.assertEqual(self.update(.5, can_toggle=False), [])
        self.assertEqual(self.update(.58, can_toggle=False), [])
        self.assertFalse(self.gesture.pending)
        self.assertEqual(self.update(.6, True, can_toggle=False), [])
        self.assertEqual(self.update(.68, can_toggle=False), [])
        self.assertEqual(self.update(.76, can_toggle=False), [])
        self.assertEqual(self.update(1.2), [])

    def test_finalizing_chord_does_not_arm_a_double(self):
        self.assertEqual(self.update(0, True, can_toggle=False), [])
        self.assertEqual(self.update(.03, True, isolated=False, can_toggle=False), [])
        self.assertEqual(self.update(.08, can_toggle=False), [])
        self.assertEqual(self.update(.16, can_toggle=False), [])
        self.assertEqual(self.update(.20, True, can_toggle=False), [])
        self.assertEqual(self.update(.28, can_toggle=False), [])
        self.assertEqual(self.update(.36, can_toggle=False), [])
        self.assertEqual(self.update(.8), [])

    def test_new_busy_operation_cancels_pending_idle_single(self):
        self.assertEqual(self.first_tap(), [])
        self.assertEqual(self.update(.2, can_toggle=False), [])
        self.assertEqual(self.update(.6), [])

    def test_busy_during_a_press_is_not_rearmed_when_idle_returns(self):
        self.assertEqual(self.update(0, True), [])
        self.assertEqual(self.update(.03, True, can_toggle=False), [])
        self.assertEqual(self.update(.06, True), [])
        self.assertEqual(self.update(.10), [])
        self.assertEqual(self.update(.18), [])
        self.assertEqual(self.update(.6), [])

    def test_external_recording_start_does_not_turn_held_idle_key_into_stop(self):
        self.assertEqual(self.update(0, True), [])
        self.assertEqual(self.update(.08, recording=True), [])
        self.assertEqual(self.update(.16, recording=True), [])
        self.assertEqual(self.update(.6, recording=True), [])

    def test_long_press_is_one_single_and_never_a_double(self):
        self.assertEqual(self.update(0, True), [])
        for now in (.3, .7, 1.5):
            self.assertEqual(self.update(now, True), [])
        self.assertEqual(self.update(2), [])
        self.assertEqual(self.update(2.08), ["single_tap"])
        self.assertEqual(self.update(2.2, True), [])
        self.assertEqual(self.update(2.28), [])
        self.assertEqual(self.update(2.36), [])
        self.assertEqual(self.update(2.7), ["single_tap"])

    def test_long_second_press_replaces_pending_idle_single_without_double(self):
        self.assertEqual(self.first_tap(), [])
        self.assertEqual(self.update(.20, True), [])
        self.assertEqual(self.update(.7, True), [])
        self.assertEqual(self.update(.8), [])
        self.assertEqual(self.update(.88), ["single_tap"])
        self.assertEqual(self.update(1.5), [])

    def test_control_chord_does_not_start_or_stop(self):
        for recording in (False, True):
            with self.subTest(recording=recording):
                self.gesture.reset()
                self.assertEqual(self.update(0, True, recording=recording), [])
                self.assertEqual(self.update(.03, True, isolated=False, recording=recording), [])
                self.assertEqual(self.update(.08, recording=recording), [])
                self.assertEqual(self.update(.16, recording=recording), [])
                self.assertEqual(self.update(.6, recording=recording), [])

    def test_typing_during_idle_single_wait_cancels_recording_start(self):
        self.assertEqual(self.first_tap(), [])
        self.assertEqual(self.update(.25, isolated=False), [])
        self.assertEqual(self.update(.6), [])

    def test_second_tap_chord_cancels_double_and_pending_single(self):
        self.assertEqual(self.first_tap(), [])
        self.assertEqual(self.update(.20, True), [])
        self.assertEqual(self.update(.23, True, isolated=False), [])
        self.assertEqual(self.update(.28), [])
        self.assertEqual(self.update(.36), [])
        self.assertEqual(self.update(.6), [])

    def test_key_up_without_known_key_down_does_nothing(self):
        for now in (0, .08, .3, 1):
            self.assertEqual(self.update(now, recording=True), [])
        self.assertFalse(self.gesture.pending)

    def test_false_release_during_press_is_debounced(self):
        self.assertEqual(self.update(0, True, recording=True), [])
        self.assertEqual(self.update(.08, recording=True), [])
        self.assertEqual(self.update(.11, True, recording=True), [])
        self.assertEqual(self.update(.2, recording=True), [])
        self.assertEqual(self.update(.28, recording=True), ["single_tap"])
        self.assertEqual(self.update(.7), [])

    def test_unpolled_stable_key_up_gap_is_completed_before_second_down(self):
        self.assertEqual(self.update(0, True), [])
        self.assertEqual(self.update(.08), [])
        self.assertEqual(self.update(.20, True), [])
        self.assertEqual(self.update(.28), [])
        self.assertEqual(self.update(.36), ["double_tap"])

    def test_context_reset_discards_pending_tap_and_down(self):
        self.assertEqual(self.first_tap(), [])
        self.gesture.reset()
        self.assertEqual(self.update(.6), [])
        self.assertEqual(self.update(.8, True), [])
        self.gesture.reset()
        self.assertEqual(self.update(.88), [])
        self.assertEqual(self.update(1.5), [])

    def test_context_reset_can_block_an_already_held_key_until_stable_release(self):
        self.gesture.reset(block_until_up=True)
        self.assertTrue(self.gesture.blocked)
        self.assertEqual(self.update(0, True, recording=True), [])
        self.assertEqual(self.update(.5, True, recording=True), [])
        self.assertEqual(self.update(.6, recording=True), [])
        self.assertEqual(self.update(.68, recording=True), [])
        self.assertFalse(self.gesture.blocked)
        self.assertEqual(self.update(.8, True, recording=True), [])
        self.assertEqual(self.update(.88, recording=True), [])
        self.assertEqual(self.update(.96, recording=True), ["single_tap"])

    def test_one_false_key_up_does_not_release_context_reset_latch(self):
        self.gesture.reset(block_until_up=True)
        self.assertEqual(self.update(0, True, recording=True), [])
        self.assertEqual(self.update(.1, recording=True), [])
        self.assertEqual(self.update(.13, True, recording=True), [])
        self.assertTrue(self.gesture.blocked)
        self.assertEqual(self.update(.4, True, recording=True), [])
        self.assertEqual(self.update(.5, recording=True), [])
        self.assertEqual(self.update(.55, recording=True), [])
        self.assertTrue(self.gesture.blocked)
        self.assertEqual(self.update(.58, recording=True), [])
        self.assertFalse(self.gesture.blocked)
        self.assertEqual(self.update(.9, recording=True), [])

    def test_default_reset_does_not_require_an_initial_key_up(self):
        self.gesture.reset(block_until_up=True)
        self.gesture.reset()
        self.assertEqual(self.first_tap(recording=True), ["single_tap"])

    def test_small_window_hold_and_double_contract_stays_unchanged(self):
        gesture = ControlGesture()
        update = lambda t, d: gesture.update(t, d, True, busy=False, allow_hold=True)
        self.assertEqual(update(0, True), [])
        self.assertEqual(update(.08, False), [])
        self.assertEqual(update(.20, True), [])
        self.assertEqual(update(.28, False), ["double_tap"])
        self.assertEqual(update(1, True), [])
        self.assertEqual(update(1.3, True), ["hold_start"])
        self.assertEqual(update(1.4, False), [])
        self.assertEqual(update(1.48, False), ["hold_end"])


if __name__ == "__main__":
    unittest.main()
