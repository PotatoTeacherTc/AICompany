from collections import deque


class TaskQueue:

    def __init__(self, history=None, max_retries=0):

        self.queue = deque()

        self.history = history

        if not isinstance(max_retries, int) or isinstance(max_retries, bool) or max_retries < 0:
            raise ValueError("max_retries must be a non-negative integer")

        self.max_retries = max_retries


    def add(self, task):

        if task.max_retries == 0:
            task.max_retries = self.max_retries

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


    def cancel(self, task, result=None):

        if task.is_terminal():
            return False

        try:
            self.queue.remove(task)
        except ValueError:
            return False

        task.cancel(result)

        self._record(task)

        return True


    def retry(self, task, error_type):

        if not task.can_retry(error_type):
            return False

        task.schedule_retry(error_type)

        self.queue.append(task)

        self._record(task)

        return True


    def _record(self, task):

        if self.history is not None:
            self.history.record(task)


    def is_empty(self):

        return len(self.queue) == 0


    def size(self):

        return len(self.queue)
