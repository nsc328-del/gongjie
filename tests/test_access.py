import asyncio
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from starlette.responses import StreamingResponse
from gongjie import runtime
from gongjie.api.routes import providers, worlds
from gongjie.ops.access import PasswordAccess

class AccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_password_spaces_isolate_worlds_keys_and_concurrent_streams(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(runtime.space_resources, {}, clear=True):
            root=Path(directory); spaces={}
            for name,password in [('owner','owner-pass'),('test','guest-pass')]:
                salt=os.urandom(16)
                spaces[name]={'salt':salt.hex(),'password_hash':hashlib.scrypt(password.encode(),salt=salt,n=16384,r=8,p=1).hex(),'label':name,'data_dir':str(root/name)}
                runtime.initialize_space(name,root/name,name)
            config={'session_secret':'test-secret','spaces':spaces}
            config_path=root/'access.json';config_path.write_text(json.dumps(config))
            with patch.dict(os.environ, {'GONGJIE_ACCESS_FILE':str(config_path)}), patch.object(runtime,'access_config',config):
                app=FastAPI();app.add_middleware(PasswordAccess)
                app.include_router(worlds.router);app.include_router(providers.router)
                @app.get('/api/stream')
                async def stream():
                    async def chunks():
                        yield runtime.space_resource('label')+'\n'
                        await asyncio.sleep(.02)
                        yield runtime.secrets.get('provider_default')+'\n'
                    return StreamingResponse(chunks())
                transport=httpx.ASGITransport(app=app,root_path='/gongjie')
                async with httpx.AsyncClient(transport=transport,base_url='https://example.test/gongjie/') as owner, httpx.AsyncClient(transport=transport,base_url='https://example.test/gongjie/') as guest:
                    self.assertEqual((await owner.get('api/stream')).status_code,401)
                    self.assertEqual((await owner.post('login',json={'password':'wrong'})).status_code,401)
                    self.assertEqual((await owner.post('login',json={'password':'owner-pass'},headers={'Origin':'https://evil.test'})).status_code,403)
                    for client,password in [(owner,'owner-pass'),(guest,'guest-pass')]:
                        result=await client.post('login',json={'password':password},headers={'Origin':'https://example.test'})
                        self.assertEqual(result.status_code,200)
                        self.assertIn('HttpOnly',result.headers['set-cookie']);self.assertIn('Secure',result.headers['set-cookie'])
                    body={'protocol':'openai-compatible','base_url':'https://example.test','path':'/v1/chat/completions','model':'test'}
                    for client,key in [(owner,'owner-key'),(guest,'guest-key')]:
                        response=await client.put('api/provider',json={**body,'api_key':key})
                        self.assertEqual(response.status_code,200);self.assertNotIn(key,response.text)
                    stale=await guest.put('api/worlds/world_daylight',json={'name':'错误存档'},headers={'X-Gongjie-Space':'owner'})
                    self.assertEqual(stale.status_code,409)
                    self.assertEqual((await guest.get('api/worlds/world_daylight')).json()['world']['name'],'白昼将死')
                    responses=await asyncio.gather(owner.get('api/stream'),guest.get('api/stream'))
                    self.assertEqual([r.text for r in responses],['owner\nowner-key\n','test\nguest-key\n'])
                    created=(await owner.post('api/worlds',json={'name':'仅主人可见'})).json()
                    world_id=created['world']['id']
                    self.assertEqual((await guest.get('api/worlds/'+world_id)).status_code,404)
                    self.assertEqual((await guest.put('api/worlds/'+world_id,json={'name':'不允许修改'})).status_code,404)
                    self.assertEqual((await owner.get('api/worlds/'+world_id)).json()['world']['name'],'仅主人可见')
                    cookie=owner.cookies.get('gongjie_access')
                    owner.cookies.clear();owner.cookies.set('gongjie_access',cookie.replace('owner.','test.',1))
                    self.assertEqual((await owner.get('api/stream')).status_code,401)
                    await guest.post('logout');self.assertEqual((await guest.get('api/stream')).status_code,401)
                    await guest.post('login',json={'password':'owner-pass'})
                    self.assertEqual((await guest.get('api/stream')).text,'owner\nowner-key\n')
                self.assertIsNone(runtime.current_space.get())
                with self.assertRaises(RuntimeError): runtime.space_resource('store')
