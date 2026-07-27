from api.contracts import (
    CreateTaskRequest,
    ListTasksRequest,
    TaskListResponse,
    TaskResponse,
)


class TaskApi:
    """Transport-neutral task endpoints backed only by application services."""

    def __init__(self, automation_service, task_query_service):
        self.automation_service = automation_service
        self.task_query_service = task_query_service

    def create_task(self, request):
        request = self._create_request(request)
        task = self.automation_service.submit_text(
            request.task_text,
            parameters=request.parameters,
            parent_task_id=request.parent_task_id,
            max_retries=request.max_retries,
            timeout_seconds=request.timeout_seconds,
        )
        return TaskResponse(self.task_query_service.get(task.id)).to_dict()

    def get_task(self, task_id):
        return TaskResponse(self.task_query_service.get(task_id)).to_dict()

    def list_tasks(self, request=None):
        request = self._list_request(request)
        return TaskListResponse(
            self.task_query_service.list(**request.to_filters())
        ).to_dict()

    @staticmethod
    def _create_request(request):
        return request if isinstance(request, CreateTaskRequest) else CreateTaskRequest.from_dict(request)

    @staticmethod
    def _list_request(request):
        return request if isinstance(request, ListTasksRequest) else ListTasksRequest.from_dict(request)
