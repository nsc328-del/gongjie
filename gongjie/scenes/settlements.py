from typing import Any

from gongjie.ai.providers import ProviderClient
from gongjie.domain.patches import PatchValidationError
from gongjie.scenes.adjudication import extract_patch, patch_skip_reason
from gongjie.storage.store import Store


async def settle_message(
    store: Store, client: ProviderClient, profile: dict[str, Any], source_id: str,
) -> dict[str, Any]:
    job = store.settlement_job(source_id)
    pending = store.pending_settlements(job["world_id"])
    if job["status"] != "complete" and (not pending or pending[0]["source_id"] != source_id):
        raise ValueError("请先记录前一段余波")
    context = job["context"]
    if job["room_id"]:
        current = store.room_character_context(job["room_id"], job["character_id"])
        # Earlier replies in this same round may have settled during recovery.
        # Rebase state only; the original dialogue and witness boundary stay frozen.
        context = {**context, **{key: current[key] for key in ("scene", "player", "relationships")}}
    feedback = ""
    for attempt in range(2):
        try:
            proposal = await extract_patch(client, context, job["reply"], profile, feedback=feedback)
            if job["room_id"]:
                result = store.apply_room_patch(
                    job["room_id"], job["character_id"], source_id, proposal, context=context,
                )
            else:
                result = store.apply_world_patch(job["conversation_id"], source_id, proposal, context=context)
            return {"patch": proposal, "result": result}
        except Exception as error:
            store.fail_settlement(source_id, patch_skip_reason(error))
            if isinstance(error, PatchValidationError):
                feedback = "; ".join([str(error), *error.issues])
            if attempt == 1:
                raise
    raise AssertionError("unreachable")


async def recover_settlements(
    store: Store, client: ProviderClient, profile: dict[str, Any], world_id: str,
) -> dict[str, Any]:
    with store.world_operation(world_id, recovery=True):
        completed = []
        for job in store.pending_settlements(world_id):
            try:
                await settle_message(store, client, profile, job["source_id"])
            except Exception:
                break
            completed.append(job["source_id"])
        return {"completed": completed, "pending_settlements": store.pending_settlements(world_id)}
