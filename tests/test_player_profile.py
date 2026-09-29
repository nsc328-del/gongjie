import tempfile
import unittest
from pathlib import Path
from pydantic import ValidationError
from gongjie.storage.store import Store
from gongjie.cognition.compiler import request_context,opening_request_context
from gongjie.api.schemas import PlayerProfileInput

class PlayerProfileTest(unittest.TestCase):
    def test_profile_is_per_world_persistent_and_used_in_dialogue(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'test.db');store.initialize()
            profile=PlayerProfileInput(name='测试女主',age=24,pronouns='她',appearance='黑发，绿色眼睛',identity='独立校时师').model_dump()
            store.save_player_profile('world_daylight',profile)
            conv=store.get_or_create_conversation('world_daylight','daylight_lorne')
            context=store.context_for_turn(conv)
            self.assertEqual(context['player_profile'],profile)
            self.assertIn('测试女主',request_context(context,{}).system)
            self.assertIn('测试女主',opening_request_context(context,{}).system)
            other=store.create_world({'name':'其他世界','template_id':'daylight'})
            self.assertEqual(other['player_profile'],{})
            store.reset_world('world_daylight','白昼将死')
            self.assertEqual(Store(store.path).get_world('world_daylight')['player_profile'],profile)
            with self.assertRaises(ValidationError): PlayerProfileInput(age=17)
