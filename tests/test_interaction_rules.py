import tempfile
import unittest
from pathlib import Path

from gongjie.storage.store import Store
from gongjie.cognition.compiler import (
    INTERACTION_RULES, request_context, opening_request_context, room_turn_system,
)
from gongjie.scenes.adjudication import patch_context


class InteractionRulesTest(unittest.TestCase):
    def test_sparse_card_and_existing_waiting_thread_use_shared_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'test.db')
            store.initialize()
            conversation = store.get_or_create_conversation('world_daylight', 'daylight_lorne')
            context = store.context_for_turn(conversation)
            for field in ('persona', 'speech_style', 'opening_hook', 'loss_of_control'):
                context['character'][field] = ''
            context['scene']['active_threads'] = ['等待玩家说明来意', '授权书尚未签署']
            context['messages'] = [{'role': 'user', 'content': '我不知道，随便看看。'}]
            context['room'] = {'name': '测试同场'}
            context['room_characters'] = [context['character']]
            systems = [request_context(context, {}).system,
                       opening_request_context(context, {}).system,
                       opening_request_context(context, {}, reentry=True).system,
                       room_turn_system(context)]
            for system in systems:
                self.assertIn(INTERACTION_RULES, system)
            self.assertIn('授权书尚未签署', systems[0])
            self.assertIn('普通问话、等待来意或表态应移除', patch_context(context, '他继续整理文件。', {}).system)
            self.assertEqual(context['scene']['active_threads'], ['等待玩家说明来意', '授权书尚未签署'])
