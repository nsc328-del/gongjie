import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from gongjie.cognition.compiler import request_context, opening_request_context, scene_entry_grounding_context
from gongjie.domain.patches import PatchValidationError, validate_patch
from gongjie.scenes.adjudication import source_catalog
from gongjie.scenes.settlements import settle_message
from gongjie.ai.providers import ReplySnapshot
from gongjie.storage.store import Store


class ReviewBoundaries(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(Path(self.directory.name) / 'db')
        self.store.initialize()
        self.conv = self.store.get_or_create_conversation('world_daylight', 'daylight_lorne')

    def test_malformed_model_types_end_as_rejected_without_blocking_world(self):
        self.store.add_message(self.conv, 'user', '我答应明天来。')
        context = self.store.context_for_turn(self.conv)
        bad = {'relationship': {'trust_delta': 1, 'evidence': {'source_id': 'user_0', 'kind': []}}}
        class Client:
            async def response(self, request, profile):
                return ReplySnapshot(content=json.dumps(bad))
        message = self.store.add_message(self.conv, 'assistant', '他点头。')
        self.store.update_message(message['id'], content=message['content'], settlement_context=context)
        result = asyncio.run(settle_message(self.store, Client(), {}, message['id']))
        self.assertEqual(result['result']['settlement']['decision']['outcome'], 'rejected')
        self.assertFalse(self.store.pending_settlements('world_daylight'))
        for field in ('kind', 'role'):
            with self.subTest(field=field), self.assertRaises(PatchValidationError):
                validate_patch({'relationship': {'trust_delta': 1}, 'evidence': {'relationship': {
                    'kind': 'expressed', 'role': 'user', 'quote': '我答应明天来。', field: []}}}, context, '他点头。')
        with self.assertRaises(PatchValidationError):
            validate_patch({'event': {'title': '测试', 'details': '测试', 'visibility': []}}, context, '他点头。')

    def test_continuation_cannot_reuse_old_player_consent_as_new_evidence(self):
        self.store.add_message(self.conv, 'user', '我答应明天来。')
        self.store.add_message(self.conv, 'assistant', '他点头。')
        context = self.store.context_for_turn(self.conv)
        self.assertFalse(any(s['role'] == 'user' for s in source_catalog(context, '他继续说话。').values()))
        with self.assertRaises(PatchValidationError):
            validate_patch({'player': {'commitments': ['明天来']}, 'evidence': {'player.commitments': {
                'kind': 'expressed', 'role': 'user', 'quote': '我答应明天来。'}}}, context, '他继续说话。')
        context['messages'] = context['messages'][:1]  # The prior response may also have failed.
        context['continuation'] = True
        self.assertFalse(any(s['role'] == 'user' for s in source_catalog(context, '他继续说话。').values()))

    def test_rejected_memory_follows_filtered_history_and_entry_review(self):
        context = self.store.context_for_turn(self.conv)
        context['messages'] = [
            {'role': 'assistant', 'content': '失败的半句', 'status': 'error'},
            *[{'role': 'user', 'content': f'问候{i}'} for i in range(7)],
            {'role': 'assistant', 'content': '他声称调查已经完成。', 'settlement_outcome': 'rejected'},
        ]
        request = request_context(context, {})
        self.assertIn('[8]', request.system)
        self.assertNotIn('[9]', request.system)
        entry = opening_request_context(context, {}, reentry=True)
        self.assertIn('[6]', entry.system)
        self.assertNotIn('[9]', entry.system)
        ledger = json.loads(scene_entry_grounding_context(context, '他站在窗前。', {}).messages[0].content)['memory_ledger']
        self.assertEqual(ledger['historical_dialogue'][-1]['settlement_outcome'], 'rejected')
