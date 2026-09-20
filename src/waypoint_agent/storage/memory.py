from uuid import UUID

from waypoint_agent.schemas import Checkpoint, TrajectoryEvent


class MemoryRepository:
    def __init__(self):
        self.records: dict[UUID, tuple[Checkpoint, list[TrajectoryEvent]]] = {}

    async def save(self, checkpoint: Checkpoint, event: TrajectoryEvent) -> None:
        task_id = checkpoint.state.task.id
        previous, events = self.records.get(task_id, (None, []))
        expected = previous.last_sequence + 1 if previous else 1
        if (
            event.task_id != task_id
            or event.sequence != expected
            or checkpoint.last_sequence != expected
        ):
            raise ValueError("Conflicting checkpoint sequence")
        self.records[task_id] = (
            checkpoint.model_copy(deep=True),
            [*events, event.model_copy(deep=True)],
        )

    async def load(self, task_id: UUID):
        checkpoint, events = self.records[task_id]
        return checkpoint.model_copy(deep=True), [e.model_copy(deep=True) for e in events]
