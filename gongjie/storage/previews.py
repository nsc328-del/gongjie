import hashlib,json
from gongjie.storage.saves import capture

def fingerprint(c, world_id):
    data=capture(c,world_id)
    if not data['worlds']: raise KeyError('world_not_found')
    return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def cached(c,world_id):
    row=c.execute('SELECT fingerprint,payload FROM world_time_previews WHERE world_id=?',(world_id,)).fetchone()
    if row and row['fingerprint']==fingerprint(c,world_id): return json.loads(row['payload'])
    return None
