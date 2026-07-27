class TaskClassifier:
    """Single source of truth for keyword-based task classification."""

    KEYWORDS = {
        "FAIL": ("failure", "fail", "failure test", "intentional failure"),
        "HISTORY": ("history", "execution history", "recent history", "record", "records"),
        "FILE": ("organize", "organise", "file", "folder", "move", "sort", "document", "image", "testfiles"),
        "RESEARCH": ("research", "trend", "trends", "analyze", "analysis", "search", "investigate", "market"),
        "MUSIC": ("music", "song", "track", "beat", "melody", "audio", "compose"),
        "CONTENT": ("youtube", "video", "content", "blog", "article", "post", "thumbnail", "script"),
    }

    def classify(self, task):
        task_text = getattr(task, "task_text", task)
        if not isinstance(task_text, str):
            raise TypeError("TaskClassifier expects a Task or task string")

        text = task_text.lower()
        for task_type, keywords in self.KEYWORDS.items():
            if any(keyword in text for keyword in keywords):
                return task_type
        return "CONTENT"
