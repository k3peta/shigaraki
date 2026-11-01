from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from shigaraki_win.punctuation import PunctuationRuntime, render


class PunctuationTests(unittest.TestCase):
    def test_only_predicted_punctuation_is_added(self):
        self.assertEqual(render("今日は晴れです",[(0,3),(3,7)],[0,2]),"今日は晴れです。")
        self.assertEqual(render("参加できますか",[(0,4),(4,7)],[0,3]),"参加できますか？")

    def test_existing_punctuation_and_exclamations_are_not_duplicated(self):
        self.assertEqual(render("晴れです。",[(0,4),(4,5)],[2,2]),"晴れです。")
        self.assertEqual(render("晴れです",[(0,4)],[4]),"晴れです")

    def test_dictionary_terms_quotes_urls_numbers_and_spaces_are_held(self):
        samples=[("桜美林大学で授業",[(0,3),(3,5)],[1,0],("桜美林大学",)),
                 ("「原文」です",[(1,2),(2,3)],[2,1],()),
                 ("1000円です",[(0,2),(2,4)],[2,0],()),
                 ("https://example.comです",[(0,8)],[1],()),
                 ("前  後\n",[(0,3),(3,4)],[1,0],())]
        for text,offsets,labels,protected in samples:
            self.assertEqual(render(text,offsets,labels,protected),text)

    def test_bad_or_special_offsets_never_drop_or_replace_original_characters(self):
        text="ＡＢ１２😀が\n  原文"
        value=render(text,[(0,0),(-1,2),(50,51),(0,2)],[2,3,1,2])
        self.assertEqual(value,"ＡＢ。１２😀が\n  原文")

    def test_only_recent_window_is_sent_to_model(self):
        runtime=PunctuationRuntime()
        runtime.restore=Mock(side_effect=lambda text,**kwargs:text+"。")
        original="以前の文章"*20000+"末尾"*200
        result=runtime.restore_recent(original,limit=400)
        self.assertEqual(runtime.restore.call_args.args[0],original[-400:])
        self.assertEqual(result,original+"。")
        self.assertEqual(result[:-401],original[:-400])

    def test_invalid_asset_does_not_load_a_native_library(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);(path/'punct.int8.onnx').write_bytes(b'wrong model')
            with self.assertRaises(ValueError):
                PunctuationRuntime(path).load()

    def test_required_punctuation_failure_cannot_be_reported_ready(self):
        from shigaraki_win.setup_models import verify_both
        stages=[]
        with patch('shigaraki_win.setup_models.engine.CpuEngine') as asr, \
                patch('shigaraki_win.setup_models.PunctuationRuntime') as punctuation, \
                patch('shigaraki_win.proofread_runtime.ProofreadRuntime') as optional_qwen:
            asr.return_value.ready=True
            punctuation.return_value.load.side_effect=ValueError('invalid model')
            with self.assertRaises(ValueError):
                verify_both(stages.append,require_punctuation=True)
            self.assertNotIn('models_ready',stages)
            punctuation.return_value.close.assert_called_once()
            asr.return_value.close.assert_called_once()
            optional_qwen.assert_not_called()

    def test_required_punctuation_readiness_does_not_start_optional_qwen(self):
        from shigaraki_win.setup_models import verify_both
        stages=[]
        with patch('shigaraki_win.setup_models.engine.CpuEngine') as asr, \
                patch('shigaraki_win.setup_models.PunctuationRuntime') as punctuation, \
                patch('shigaraki_win.proofread_runtime.ProofreadRuntime') as optional_qwen:
            asr.return_value.ready=True
            punctuation.return_value.ready=True
            verify_both(stages.append,require_punctuation=True,allow_policy_block=True)
            self.assertEqual(stages,['check_asr','check_punctuation','models_ready'])
            punctuation.return_value.close.assert_called_once()
            optional_qwen.assert_not_called()


if __name__=='__main__': unittest.main()
