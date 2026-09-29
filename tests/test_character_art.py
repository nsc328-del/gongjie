import tempfile
import unittest
from pathlib import Path
from gongjie.storage.store import Store

class CharacterArtTest(unittest.TestCase):
    def test_art_survives_new_instance_rename_reset_and_existing_world_upgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'world.db');store.initialize()
            world=store.create_world({'name':'私人副本','template_id':'daylight'})
            characters=world['characters']
            self.assertEqual(len({c['art_key'] for c in characters}),4)
            character=characters[0]
            store.update_character(character['id'],{'name':'新名字'})
            self.assertEqual(store.get_character(character['id'])['art_key'],character['art_key'])
            store.reset_world(world['world']['id'], '私人副本')
            self.assertEqual(store.get_character(character['id'])['art_key'],character['art_key'])
            with store.transaction() as db:
                db.execute('ALTER TABLE characters DROP COLUMN art_key')
            store.initialize()
            seeded=store.get_character('daylight_lorne')
            self.assertEqual(seeded['art_key'],'daylight_lorne')
            self.assertEqual(store.get_character(character['id'])['name'],'新名字')
            self.assertEqual(len(store.list_worlds()),2)
