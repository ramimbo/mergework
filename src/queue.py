import asyncio

class TaskQueue:
    def __init__(self, timeout=30):
        self.timeout = timeout
        self.tasks = {}

    async def execute(self, task_id, coro):
        try:
            result = await asyncio.wait_for(coro, timeout=self.timeout)
            return result
        except asyncio.TimeoutError:
            self.tasks[task_id] = {"status": "timeout", "error": "Task timed out"}
            raise