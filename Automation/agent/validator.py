class TaskValidator:

    def validate(self, result):

        return isinstance(result, list) and "FAILED" not in result
