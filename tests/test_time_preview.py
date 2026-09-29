import asyncio,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,AsyncMock
from gongjie.storage.store import Store
from gongjie.cognition.pulse import prepare_time_preview,advance_world_pulse

class PreviewTests(unittest.IsolatedAsyncioTestCase):
 async def test_invalid_clock_is_never_published_as_a_preview(self):
  with tempfile.TemporaryDirectory() as d:
   s=Store(Path(d)/'db');s.initialize();w='world_daylight'
   current=s.get_world(w)['world']['current_time']
   for value in ['',current,'18:43:36','2026-09-10T18:43:36','暮'*121]:
    with self.subTest(value=value), self.assertRaises(ValueError):
     s.save_time_preview(w,s.preview_fingerprint(w),{'id':'invalid','world_time':value})
    self.assertIsNone(s.time_preview(w))
 async def test_cached_plan_consumption_invalidation_and_world_isolation(self):
  with tempfile.TemporaryDirectory() as d:
   s=Store(Path(d)/'db');s.initialize();w='world_daylight'
   plan={'world_time':'次日清晨','ready_commitment_ids':[],'appointment_times':{}}
   with patch('gongjie.cognition.pulse.appointment_time',new=AsyncMock(return_value=plan.copy())) as planner:
    first=await prepare_time_preview(s,None,{},w)
    second=await prepare_time_preview(s,None,{},w)
    self.assertEqual(first,second);self.assertEqual(planner.await_count,1)
   with patch('gongjie.cognition.pulse._advance_world_pulse',new=AsyncMock(return_value={})) as advance:
    await advance_world_pulse(store=s,client=None,profile={},world_id=w,preview_id=first['id'])
    self.assertEqual(advance.call_args.kwargs['clock'],first)
   save=s.save_world(w)
   conv=s.get_or_create_conversation(w,'daylight_lorne');s.add_message(conv,'user','约定有变')
   self.assertIsNone(s.time_preview(w))
   with self.assertRaises(ValueError): await advance_world_pulse(store=s,client=None,profile={},world_id=w,preview_id=first['id'])
   s.load_world_save(w,save['id']);self.assertIsNone(s.time_preview(w))
   other=s.create_world({'name':'其他世界','template_id':'daylight'})['world']['id']
   self.assertIsNone(s.time_preview(other))
 async def test_changed_state_during_planning_is_not_published(self):
  with tempfile.TemporaryDirectory() as d:
   s=Store(Path(d)/'db');s.initialize();w='world_daylight'
   async def changed(*args):
    s.add_message(s.get_or_create_conversation(w,'daylight_lorne'),'user','改变时间')
    return {'world_time':'次日清晨','ready_commitment_ids':[],'appointment_times':{}}
   with patch('gongjie.cognition.pulse.appointment_time',new=changed):
    with self.assertRaises(ValueError): await prepare_time_preview(s,None,{},w)
   self.assertIsNone(s.time_preview(w))
