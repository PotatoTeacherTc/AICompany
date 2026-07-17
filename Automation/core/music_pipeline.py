from pathlib import Path

from datetime import datetime


class MusicPipeline:

    name = "Music Pipeline"


    def __init__(self):

        self.music_root = (

            Path(__file__).resolve().parent.parent

            / "Music"

        )


        self.music_root.mkdir(

            exist_ok=True

        )


    def run(self, task):

        print(

            "Music Pipeline: Starting music creation..."

        )


        print(

            "Music Pipeline: Analyzing request..."

        )


        timestamp = datetime.now().strftime(

            "%Y%m%d_%H%M%S"

        )


        project_name = (

            f"music_project_{timestamp}"

        )


        project_path = (

            self.music_root

            / project_name

        )


        project_path.mkdir(

            exist_ok=True

        )


        print(

            f"Music Pipeline: Project created "

            f"-> {project_name}"

        )


        print(

            "Music Pipeline: Generating song structure..."

        )


        song_structure = [

            "INTRO",

            "VERSE 1",

            "PRE-CHORUS",

            "CHORUS",

            "VERSE 2",

            "CHORUS",

            "BRIDGE",

            "FINAL CHORUS",

            "OUTRO",

        ]


        metadata = {

            "project_name": project_name,

            "request": task,

            "genre": "AI Generated Music",

            "bpm": 100,

            "key": "C Major",

            "structure": song_structure,

            "created_at": datetime.now().isoformat(),

        }


        structure_file = (

            project_path

            / "song_structure.txt"

        )


        with open(

            structure_file,

            "w",

            encoding="utf-8"

        ) as file:


            file.write(

                "AICompany Music Project\n"

            )


            file.write(

                "=======================\n\n"

            )


            file.write(

                f"Project: "

                f"{project_name}\n"

            )


            file.write(

                f"Request: "

                f"{task}\n\n"

            )


            file.write(

                "Song Structure:\n"

            )


            for index, section in enumerate(

                song_structure,

                start=1

            ):


                file.write(

                    f"{index}. "

                    f"{section}\n"

                )


        metadata_file = (

            project_path

            / "metadata.txt"

        )


        with open(

            metadata_file,

            "w",

            encoding="utf-8"

        ) as file:


            for key, value in metadata.items():


                file.write(

                    f"{key}: "

                    f"{value}\n"

                )


        print(

            "Music Pipeline: Verifying result..."

        )


        files_created = [

            structure_file,

            metadata_file,

        ]


        valid = all(

            file.exists()

            for file in files_created

        )


        if not valid:


            return {

                "status": "FAILED",

                "pipeline": self.name,

                "task": task,

                "data": {},

                "error": (

                    "Music project "

                    "verification failed"

                ),

            }


        print(

            "Music Pipeline: Music project "

            "created successfully"

        )


        return {

            "status": "SUCCESS",

            "pipeline": self.name,

            "task": task,

            "data": {

                "project_name": project_name,

                "project_path": str(

                    project_path

                ),

                "files_created": [

                    str(file)

                    for file in files_created

                ],

                "metadata": metadata,

            },

            "error": None,

        }