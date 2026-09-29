"""Plan geography before prose, and validate destination prose before publication."""
from __future__ import annotations

from dataclasses import replace

from gongjie.ai.providers import ProviderError
from gongjie.cognition.places import planning_request, plan_review_request, reply_review_request
from gongjie.domain.locations import validate_plan, validate_resolution
from gongjie.domain.patches import PatchValidationError
from gongjie.scenes.adjudication import parse_patch, extract_patch
from gongjie.storage.geography import check_plan
from gongjie.storage.location_knowledge import mentioned_locations
from gongjie.scenes.room_memory import observe_reply


def check_verdict(value: dict, *, resolution=False) -> None:
    allowed = {'approved', 'issues'} | ({'resolution'} if resolution else set())
    issues = value.get('issues')
    if (set(value) != allowed or not isinstance(issues, list)
            or not all(isinstance(i, str) for i in issues) or type(value.get('approved')) is not bool):
        raise PatchValidationError('scene_review_invalid')
    if not value['approved']:
        raise PatchValidationError('scene_review_rejected', issues[:3])


async def prepare_scene(client, context, profile, *, store):
    with store.read() as connection:
        context = mentioned_locations(connection, context)
    feedback = ''
    for _ in range(2):
        try:
            snapshot = await client.response(planning_request(context, feedback), profile)
            plan = validate_plan(parse_patch(snapshot.content), context)
            with store.read() as connection:
                check_plan(connection, context, plan)
            if plan.get('create'):
                review = await client.response(plan_review_request(context, plan), profile)
                check_verdict(parse_patch(review.content))
            locations = list(context['locations'])
            if plan.get('create'):
                locations.append({**plan['target'], 'world_id': context['world']['id']})
            return {**context, 'scene_plan': plan, 'locations': locations}
        except (PatchValidationError, ValueError) as error:
            feedback = '; '.join([str(error), *getattr(error, 'issues', [])])
    raise ProviderError('scene_plan', '这次去向还没有理清，请再试一次', retryable=True)


async def destination_reply(client, context, request, profile, *, parse_reply=None):
    feedback = ''
    for _ in range(2):
        context['scene_plan'].pop('resolution', None)
        context.pop('prepared_patch', None)
        candidate = await client.response(replace(request, system=request.system + feedback), profile)
        # Buffered replies follow the same completeness rule as streamed replies.
        if candidate.finish_reason not in {'stop', 'end_turn'} or not candidate.content.strip():
            raise ProviderError('incomplete_reply', '这段回应尚未完整生成，请再试一次', retryable=True)
        reply = candidate.content
        if parse_reply:
            decision = parse_reply(reply)
            if decision.get('silent'):
                return candidate
            reply = decision['reply']
        try:
            review = await client.response(reply_review_request(context, reply), profile)
            verdict = parse_patch(review.content)
            check_verdict(verdict, resolution=True)
            cited = verdict['resolution']
            sources = {f'p{i}': p for i,p in enumerate(reply.split('\n')) if p.strip()}
            if not isinstance(cited, dict) or set(cited) != {'confirmed', 'arrived', 'source_id'} or not isinstance(cited['source_id'], str):
                raise PatchValidationError('location_resolution_invalid')
            if cited['confirmed'] and cited['source_id'] not in sources:
                raise PatchValidationError('location_resolution_source')
            resolution = validate_resolution({'confirmed': cited['confirmed'], 'arrived': cited['arrived'], 'quote': sources.get(cited['source_id'], '')}, context['scene_plan'], reply)
            context['scene_plan']['resolution'] = resolution
            break
        except (PatchValidationError, ValueError) as error:
            feedback = '\n待发布正文未通过校对，请重新生成完整正文，修正这些问题：' + '; '.join([str(error), *getattr(error, 'issues', [])])
    else:
        raise ProviderError('scene_reply', '这段回应尚未衔接好，请再试一次', retryable=True)

    if context.get('room'):
        await observe_reply(client,profile,context,reply)
    # A faulty proposal cannot change a character's already checked response.
    # The durable queue later applies this same patch without another LLM vote.
    repair = ''
    repair_proposal = None
    for attempt in range(2):
        try:
            proposal = await extract_patch(client, context, reply, profile,
                                           feedback=repair, repair_proposal=repair_proposal)
            context['prepared_patch'] = {'reply': reply, 'patch': proposal}
            return candidate
        except (PatchValidationError, ValueError) as error:
            if attempt:
                raise ProviderError('scene_settlement', '这段回应的变化尚未确认，请再试一次', retryable=True) from error
            repair = '; '.join([str(error), *getattr(error, 'issues', [])])
            repair_proposal = getattr(error, 'repair_proposal', None)
    raise AssertionError('unreachable')
