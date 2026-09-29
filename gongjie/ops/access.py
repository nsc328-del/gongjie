from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
from pathlib import Path

from gongjie.runtime import current_space

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

LOGIN = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>进入共界</title><style>
*{box-sizing:border-box}body{margin:0;min-height:100svh;display:grid;place-items:center;background:#111512;color:#eee9db;font-family:system-ui,sans-serif;padding:24px}main{width:min(100%,360px)}small{letter-spacing:.3em;color:#a99d7a}h1{font-size:42px;font-weight:500;margin:18px 0 40px}input,button{width:100%;padding:16px;border-radius:12px;font:inherit}input{background:#1b211c;border:1px solid #525444;color:inherit;outline:none}input:focus{border-color:#c6b990}button{margin-top:16px;background:#d7c69d;color:#20251d;border:0;cursor:pointer}button:disabled{opacity:.5}#error{min-height:24px;color:#e9ac9e;font-size:14px}</style><main><small>BOUNDLESS WORLDS</small><h1>共界</h1><form><input type="password" name="password" aria-label="访问密码" placeholder="访问密码" autocomplete="current-password" required autofocus><button>进入世界</button><p id="error" role="alert"></p></form></main><script>
const f=document.querySelector('form'),b=document.querySelector('button'),e=document.querySelector('#error');f.onsubmit=async event=>{event.preventDefault();b.disabled=true;e.textContent='';try{const r=await fetch(location.pathname,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:f.password.value})});if(r.ok){location.replace('./');return}e.textContent=r.status===429?'稍后再试':'密码不正确';}catch{e.textContent='连接失败，请重试'}b.disabled=false};</script></html>'''


class PasswordAccess:
    """ASGI gate keeps streaming responses unbuffered; local mode has no gate."""
    def __init__(self, app):
        self.app = app
        path = os.environ.get('GONGJIE_ACCESS_FILE')
        self.config = json.loads(Path(path).read_text()) if path else None
        self.attempts = {}

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not self.config:
            return await self.app(scope, receive, send)
        request = Request(scope, receive)
        root = scope.get('root_path', '').rstrip('/')
        path = scope['path']
        if root and path.startswith(root + '/'):
            path = path[len(root):]
        now = int(time.time())
        secret = self.config['session_secret'].encode()
        def signature(value):
            return hmac.new(secret, value.encode(), hashlib.sha256).hexdigest()
        token = request.cookies.get('gongjie_access', '')
        parts = token.split('.')
        space_id, expiry, signed = parts if len(parts) == 3 else ('', '', '')
        valid = space_id in self.config['spaces'] and expiry.isdigit() and len(expiry) <= 12 and int(expiry) > now and hmac.compare_digest(signature(space_id + '.' + expiry), signed)
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('origin')
            if origin and origin != str(request.base_url).rstrip('/').removesuffix(root):
                return await JSONResponse({'detail':'来源无效'}, status_code=403)(scope, receive, send)
        if path == '/logout' and request.method == 'POST':
            response = JSONResponse({'ok': True})
            response.delete_cookie('gongjie_access', path=root+'/', secure=True, httponly=True, samesite='strict')
            response.headers['Cache-Control'] = 'no-store'
            return await response(scope, receive, send)
        if path == '/login':
            if request.method == 'GET':
                response = RedirectResponse(root + '/') if valid else HTMLResponse(LOGIN)
            elif request.method == 'POST':
                ip = request.client.host if request.client else ''
                self.attempts = {k:v for k,v in self.attempts.items() if v[1] > now - 60}
                count, start = self.attempts.get(ip, (0, now))
                if count >= 10:
                    response = JSONResponse({'detail':'稍后再试'}, status_code=429)
                else:
                    self.attempts[ip] = (count + 1, start)
                    try:
                        body = await request.body()
                        password = json.loads(body)['password'] if len(body) <= 4096 else ''
                        def match_password():
                            matched = None
                            for candidate, config in self.config['spaces'].items():
                                digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(config['salt']), n=16384, r=8, p=1).hex()
                                if hmac.compare_digest(digest, config['password_hash']):
                                    matched = candidate
                            return matched
                        matched = await asyncio.to_thread(match_password)
                        correct = matched is not None
                    except (ValueError, KeyError, TypeError, AttributeError):
                        correct = False
                    if correct:
                        self.attempts.pop(ip, None)
                        expiry = str(now + 30 * 86400)
                        response = JSONResponse({'ok':True})
                        payload = matched + '.' + expiry
                        response.set_cookie('gongjie_access', payload + '.' + signature(payload), max_age=30*86400, path=root+'/', secure=True, httponly=True, samesite='strict')
                    else:
                        response = JSONResponse({'detail':'密码不正确'}, status_code=401)
            else:
                response = JSONResponse({'detail':'不支持此请求'}, status_code=405)
            response.headers['Cache-Control'] = 'no-store'
            return await response(scope, receive, send)
        if not valid:
            response = JSONResponse({'detail':'请先输入访问密码'}, status_code=401) if path.startswith('/api/') else RedirectResponse(root + '/login', status_code=303)
            return await response(scope, receive, send)
        expected_space = request.headers.get('x-gongjie-space')
        if path.startswith('/api/') and expected_space and expected_space != space_id:
            response = JSONResponse({'detail':'存档已切换，请重新载入'}, status_code=409, headers={'X-Gongjie-Session':'changed'})
            return await response(scope, receive, send)
        context_token = current_space.set(space_id)
        try:
            await self.app(scope, receive, send)
        finally:
            current_space.reset(context_token)
