"""Compile room observations before any character request or settlement."""
from gongjie.scenes.observations import observe_message
from gongjie.storage import information


async def ensure_observations(store, client, profile, room_id):
    with store.read() as c:
        messages = information.pending_observations(c,room_id)
    for message in messages:
        views = await observe_message(client,profile,content=message['content'],
            author_id=message['character_id'],members=message['members']) if message['members'] else {}
        with store.transaction() as c:
            information.save_views(c,message['id'],views)


async def observe_reply(client, profile, context, reply):
    views = await observe_message(client,profile,content=reply,
        author_id=context['character']['id'],members=context['room_characters'])
    context['reply_observations'] = {'reply':reply,'views':views}
    for cid,text in views.items():
        context.setdefault('observation_sources',{}).setdefault(cid,{})['assistant'] = text
    return views
