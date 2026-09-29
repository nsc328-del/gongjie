import json
import unittest
from gongjie.ai.providers import ReplySnapshot
from gongjie.domain.patches import PatchValidationError
from gongjie.scenes.adjudication import extract_patch

class SettlementReviewContextTest(unittest.IsolatedAsyncioTestCase):
    async def test_saved_opening_is_available_without_becoming_new_evidence(self):
        opening='洛恩面前摊着尚未签名的授权书。'
        context={'character':{'name':'洛恩'},'world':{'current_time':'暮钟前'},
            'scene':{'world_time':'暮钟前','location_id':'hall','tension':0},
            'locations':[{'id':'hall','name':'金枝厅'}],
            'player':{'location_id':'hall','commitments':[]},
            'relationships':[{'to_user':True,'trust':8,'tension':22}],
            'messages':[{'role':'assistant','content':opening},{'role':'user','content':'早上好'}]}
        proposal={'event':None,'scene':{'physical_state':'他放下授权书，靠回椅背。','tension':0},'relationship':{},'player':{}}
        requests=[]
        class Client:
            async def response(self,request,profile):
                requests.append(request)
                return ReplySnapshot(content=json.dumps(proposal if len(requests)==1 else {'approved':True,'issues':[]}))
        await extract_patch(Client(),context,'他放下文件，靠回椅背。',{})
        self.assertIn('"trust": 8',requests[0].messages[0].content)
        audit=json.loads(requests[1].messages[0].content)
        self.assertEqual(audit['previous_dialogue'],[{'role':'assistant','content':opening}])
        self.assertEqual(audit['user'],'早上好')
        self.assertIn('两者没有相等或换算要求',requests[1].system)
        proposal['relationship']={'trust_delta':1}
        proposal['relationship']['evidence']={'role':'assistant','quote':opening,'kind':'occurred'}
        requests.clear()
        with self.assertRaises(PatchValidationError):
            await extract_patch(Client(),context,'他放下文件，靠回椅背。',{})
        self.assertEqual(len(requests),1)

    async def test_review_verdict_must_be_complete_consistent_and_keep_repair_issues(self):
        context = {
            'character': {'name': '洛恩'}, 'world': {'current_time': '暮钟前'},
            'scene': {'world_time': '暮钟前', 'location_id': 'hall'},
            'locations': [{'id': 'hall', 'name': '金枝厅'}],
            'player': {'location_id': 'hall', 'commitments': []},
            'messages': [{'role': 'user', 'content': '请坐。'}],
        }
        proposal = {'scene': {'physical_state': '洛恩坐在椅子上。'}}
        all_issues = [f'scene.physical_state 第{i}项硬事实没有来源' for i in range(6)]
        cases = (
            ({'approved': True, 'issues': all_issues}, 'hard_state_not_supported', all_issues),
            ({'approved': False, 'issues': all_issues}, 'hard_state_not_supported', all_issues),
            ({'approved': 1}, 'review_invalid', []),
        )
        for verdict, code, expected_issues in cases:
            with self.subTest(verdict=verdict):
                class Client:
                    async def response(self, request, profile):
                        value = proposal if '状态裁判' in request.system else verdict
                        return ReplySnapshot(content=json.dumps(value, ensure_ascii=False), finish_reason='stop')
                with self.assertRaises(PatchValidationError) as caught:
                    await extract_patch(Client(), context, '洛恩坐在椅子上。', {})
                self.assertEqual(str(caught.exception), code)
                self.assertEqual(caught.exception.issues, expected_issues)
                self.assertEqual(caught.exception.repair_proposal, proposal)
