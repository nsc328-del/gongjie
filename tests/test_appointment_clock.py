import asyncio,json,tempfile,unittest
from pathlib import Path
from gongjie.storage.store import Store
from gongjie.ai.providers import ReplySnapshot
from gongjie.cognition.pulse import appointment_time,character_pulse_context,transition_context

class AppointmentClockTest(unittest.TestCase):
 def test_clock_uses_promise_history_without_sharing_it_or_moving_player(self):
  with tempfile.TemporaryDirectory() as d:
   s=Store(Path(d)/'db');s.initialize()
   conv=s.get_or_create_conversation('world_daylight','daylight_severin')
   s.add_message(conv,'assistant','明天第七声钟后到东侧检修门。')
   with s.transaction() as c:
    c.execute('INSERT INTO player_commitments VALUES (?,?,?,?,?)',('p','world_daylight','daylight_severin','参与校准','now'))
   context=s.cognition_sync_context('world_daylight')
   class Client:
    async def response(self,request,profile):
     self.request=request
     return ReplySnapshot(content='{"world_time":"校时日·第七声钟后","ready_commitment_ids":["p"]}')
   client=Client()
   plan=asyncio.run(appointment_time(s,client,context,{}))
   self.assertEqual(plan["ready_commitment_ids"],["p"])
   target=plan["world_time"]
   self.assertIn('明天第七声钟',client.request.messages[0].content)
   self.assertEqual(target,'校时日·第七声钟后')
   other=next(i for i in context['characters'] if i['character']['id']=='daylight_lorne')
   private=character_pulse_context(other,context,{},1024,target)
   public=transition_context(context,[],{},1024,target)
   for request in (private,public):
    self.assertIn(target,request.messages[0].content)
    self.assertNotIn('东侧检修门',request.messages[0].content)
    self.assertNotIn('参与校准',request.messages[0].content)
   self.assertEqual(s.cognition_sync_context('world_daylight')['player'],context['player'])
   self.assertEqual(s.get_world('world_daylight')['world']['current_time'],context['world']['current_time'])
