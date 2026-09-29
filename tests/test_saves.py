import tempfile,unittest
from pathlib import Path
from gongjie.storage.store import Store
from gongjie.storage.saves import capture

class SaveTests(unittest.TestCase):
 def test_complete_restore_isolated_and_undoable(self):
  with tempfile.TemporaryDirectory() as d:
   s=Store(Path(d)/'db');s.initialize()
   world='world_daylight';other=s.create_world({'name':'另一个','template_id':'daylight'})['world']['id']
   conv=s.get_or_create_conversation(world,'daylight_lorne')
   s.add_message(conv,'user','旧消息')
   with s.read() as c: before=capture(c,world); untouched=capture(c,other)
   save=s.save_world(world)
   s.add_message(conv,'user','新消息')
   with self.assertRaises(KeyError): s.load_world_save(other,save['id'])
   self.assertEqual(s.list_saves(other),[])
   backup=s.load_world_save(world,save['id'])
   with s.read() as c:
    self.assertEqual(capture(c,world),before)
    self.assertEqual(capture(c,other),untouched)
   s.load_world_save(world,backup['id'])
   self.assertEqual(s.get_conversation(conv)['messages'][-1]['content'],'新消息')
   with s.world_operation(world):
    with self.assertRaises(ValueError): s.save_world(world)
