class Manager:


    def __init__(self, registry):

        self.registry = registry


    def handle(self, task):

        print(

            "Manager: Analyzing task..."

        )


        # ====================================================
        # TASK TEXT 추출
        # ====================================================

        if isinstance(task, str):

            task_text = task

        else:

            task_text = task.task_text


        # ====================================================
        # TASK TYPE 판별
        # ====================================================

        task_type = self.detect_task_type(

            task_text

        )


        print(

            f"Manager: Task type = {task_type}"

        )


        # ====================================================
        # PIPELINE 조회
        # ====================================================

        pipeline = self.registry.get(

            task_type

        )


        if pipeline is None:

            return {

                "status": "FAILED",

                "task": task_text,

                "task_type": task_type,

                "error": (

                    f"No pipeline registered "

                    f"for task type: {task_type}"

                )

            }


        # ====================================================
        # PIPELINE NAME
        # ====================================================

        pipeline_name = getattr(

            pipeline,

            "name",

            pipeline.__class__.__name__

        )


        print(

            "Manager: Routing task to "

            f"{pipeline_name}..."

        )


        # ====================================================
        # PIPELINE 실행
        # ====================================================

        try:


            result = pipeline.run(

                task

            )


            if result.get(

                "status"

            ) == "SUCCESS":


                print(

                    "Manager: Task completed successfully"

                )


            else:


                print(

                    "Manager: Task completed "

                    f"with status: "

                    f"{result.get('status')}"

                )


            return result


        except Exception as error:


            print(

                "Manager: Pipeline execution failed"

            )


            return {

                "status": "FAILED",

                "pipeline": pipeline_name,

                "task": task_text,

                "task_type": task_type,

                "data": {},

                "error": str(error)

            }


    def detect_task_type(

        self,

        task_text

    ):


        text = task_text.lower()


        # ====================================================
        # FAIL
        # ====================================================

        if (

            "failure" in text

            or "fail" in text

            or "실패" in text

        ):

            return "FAIL"


        # ====================================================
        # HISTORY
        # ====================================================

        if (

            "history" in text

            or "execution history" in text

            or "record" in text

            or "기록" in text

            or "이력" in text

        ):

            return "HISTORY"


        # ====================================================
        # FILE
        # ====================================================

        if (

            "organize" in text

            or "file" in text

            or "folder" in text

            or "파일" in text

            or "폴더" in text

        ):

            return "FILE"


        # ====================================================
        # MUSIC
        # ====================================================

        if (

            "music" in text

            or "song" in text

            or "음악" in text

            or "노래" in text

        ):

            return "MUSIC"


        # ====================================================
        # RESEARCH
        # ====================================================

        if (

            "research" in text

            or "trend" in text

            or "analyze" in text

            or "조사" in text

            or "검색" in text

            or "분석" in text

        ):

            return "RESEARCH"


        # ====================================================
        # CONTENT
        # ====================================================

        if (

            "youtube" in text

            or "video" in text

            or "content" in text

            or "영상" in text

            or "콘텐츠" in text

        ):

            return "CONTENT"


        # ====================================================
        # DEFAULT
        # ====================================================

        return "CONTENT"