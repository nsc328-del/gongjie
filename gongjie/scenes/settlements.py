import logging
import asyncio
from typing import Any

logger = logging.getLogger(__name__)

_workers: dict[tuple[str, str], asyncio.Task] = {}

from gongjie.ai.providers import ProviderClient, ProviderError
from gongjie.domain.patches import PatchValidationError
from gongjie.scenes.adjudication import extract_patch, patch_skip_reason
from gongjie.storage.store import Store
from gongjie.storage import information
from gongjie.scenes.room_memory import ensure_observations


async def settle_message(
    store: Store, client: ProviderClient, profile: dict[str, Any], source_id: str,
) -> dict[str, Any]:
    job = store.settlement_job(source_id)
    pending = store.pending_settlements(job["world_id"])
    if job["status"] != "complete" and (not pending or pending[0]["source_id"] != source_id):
        raise ValueError("请先记录前一段余波")
    context = job["context"]
    if job["status"] == "complete":
        with store.read() as connection:
            return {"patch": {}, "result": store._existing_patch_result(connection, source_id)}
    if job['room_id']:
        await ensure_observations(store,client,profile,job['room_id'])
        with store.read() as connection:
            context = information.recovery_context(connection,context,source_id)
    store.start_settlement(source_id)
    feedback = ""
    repair_proposal = None
    for attempt in range(2):
        try:
            prepared = context.get('prepared_patch')
            if prepared:
                if prepared['reply'] != job['reply']:
                    raise ValueError('prepared_reply_mismatch')
                proposal = prepared['patch']
            else:
                proposal = await extract_patch(client, context, job["reply"], profile,
                                               feedback=feedback, repair_proposal=repair_proposal)
        except (PatchValidationError, ValueError) as error:
            issues = error.issues if isinstance(error, PatchValidationError) else ["模型没有返回有效的结算对象"]
            feedback = "; ".join([str(error), *issues])
            repair_proposal = getattr(error, "repair_proposal", None)
            logger.warning("settlement_validation source=%s attempt=%s code=%s issues=%s", source_id, attempt + 1, str(error), issues)
            if attempt == 1:
                return {"patch": {}, "result": store.reject_settlement(source_id, patch_skip_reason(error), issues)}
            continue
        except ProviderError as error:
            if attempt == 1:
                store.fail_settlement(source_id, patch_skip_reason(error))
                raise
            continue
        # Storage errors never become a semantic rejection. The transaction either
        # commits both state and completion, or leaves the job recoverable.
        try:
            if job["room_id"]:
                result = store.apply_room_patch(job["room_id"], job["character_id"], source_id, proposal, context=context)
            else:
                result = store.apply_world_patch(job["conversation_id"], source_id, proposal, context=context)
            return {"patch": proposal, "result": result}
        except PatchValidationError as error:
            feedback = '; '.join([str(error), *error.issues])
            repair_proposal = getattr(error, "repair_proposal", None)
            if attempt == 1:
                return {'patch':{},'result':store.reject_settlement(source_id,patch_skip_reason(error),error.issues)}
        except Exception as error:
            store.fail_settlement(source_id, patch_skip_reason(error))
            raise
    raise AssertionError("unreachable")


async def recover_settlements(
    store: Store, client: ProviderClient, profile: dict[str, Any], world_id: str,
) -> dict[str, Any]:
    with store.world_operation(world_id, recovery=True):
        result = await asyncio.shield(start_settlements(store, client, profile, world_id))
        return {"completed": list(result['results']), "pending_settlements": store.pending_settlements(world_id)}


def start_settlements(store, client, profile, world_id):
    """Own the durable queue independently of a particular HTTP response."""
    key = (str(store.path), world_id)
    previous = _workers.get(key)
    if previous and not previous.done():
        return previous

    async def drain():
        results = {}
        while pending := store.pending_settlements(world_id):
            source = pending[0]['source_id']
            try:
                results[source] = await settle_message(store, client, profile, source)
            except Exception as error:
                store.fail_settlement(source, patch_skip_reason(error))
                return {'results': results, 'error': error}
        return {'results': results, 'error': None}

    task = asyncio.create_task(drain())
    _workers[key] = task

    def finished(completed):
        if _workers.get(key) is completed:
            _workers.pop(key, None)
        if not completed.cancelled() and completed.exception():
            logger.error('settlement_worker_failed world=%s', world_id)
    task.add_done_callback(finished)
    return task


async def settlement_result(task, store, source_id):
    result = await asyncio.shield(task)
    if source_id in result['results']:
        return result['results'][source_id]
    if result['error']:
        raise result['error']
    with store.read() as connection:
        existing = store._existing_patch_result(connection, source_id)
    if existing:
        return {'patch': {}, 'result': existing}
    raise ValueError('这段余波仍在等待记录')
