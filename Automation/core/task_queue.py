from collections import deque


class TaskQueue:

    def __init__(self, history=None):

        self.queue = deque()

        self.history = history


    def add(self, task):

        task.queue()

        self.queue.append(task)

        self._record(task)

        print(
            f"Queue: Task added "
            f"[{task.id}] {task.task_text}"
        )


    def get_next(self):

        if not self.queue:

            return None

        return self.queue.popleft()


    def skip(self, task, result=None):

        try:
            self.queue.remove(task)
        except ValueError:
            pass

        task.skip(result)

        self._record(task)


    def _record(self, task):

        if self.history is not None:
            self.history.record(task)


    def is_empty(self):

        return len(self.queue) == 0


    def size(self):

        return len(self.queue)
