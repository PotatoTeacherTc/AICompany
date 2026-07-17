class TaskClassifier:


    def classify(self, task):

        task_lower = task.lower()


        # ====================================================
        # HISTORY
        # ====================================================

        history_keywords = [

            "history",

            "execution history",

            "recent history",

            "show history",

            "recent execution",

            "past execution",

            "previous execution",

            "records",

            "record",

        ]


        for keyword in history_keywords:

            if keyword in task_lower:

                return "HISTORY"


        # ====================================================
        # FILE
        # ====================================================

        file_keywords = [

            "file",

            "organize",

            "organise",

            "move",

            "sort",

            "folder",

            "document",

            "image",

            "music file",

            "testfiles",

        ]


        for keyword in file_keywords:

            if keyword in task_lower:

                return "FILE"


        # ====================================================
        # MUSIC
        # ====================================================

        music_keywords = [

            "music",

            "song",

            "track",

            "beat",

            "melody",

            "audio",

            "compose",

        ]


        for keyword in music_keywords:

            if keyword in task_lower:

                return "MUSIC"


        # ====================================================
        # CONTENT
        # ====================================================

        content_keywords = [

            "youtube",

            "video",

            "content",

            "blog",

            "article",

            "post",

            "thumbnail",

            "script",

        ]


        for keyword in content_keywords:

            if keyword in task_lower:

                return "CONTENT"


        # ====================================================
        # RESEARCH
        # ====================================================

        research_keywords = [

            "research",

            "trend",

            "trends",

            "analyze",

            "analysis",

            "search",

            "investigate",

            "market",

        ]


        for keyword in research_keywords:

            if keyword in task_lower:

                return "RESEARCH"


        # ====================================================
        # FAILURE TEST
        # ====================================================

        failure_keywords = [

            "failure test",

            "run failure",

            "intentional failure",

        ]


        for keyword in failure_keywords:

            if keyword in task_lower:

                return "FAIL"


        return "UNKNOWN"