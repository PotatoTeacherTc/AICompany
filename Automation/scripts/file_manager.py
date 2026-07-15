import os
import shutil

from scripts.logger import log
from scripts.error_handler import handle_error


FILE_TYPES = {
    "Images": [".jpg", ".png", ".jpeg"],
    "Videos": [".mp4", ".mov"],
    "Music": [".mp3", ".wav"],
    "Documents": [".pdf", ".docx", ".xlsx"]
}


def organize_files(folder_path):

    results = []

    for file in os.listdir(folder_path):

        file_path = os.path.join(folder_path, file)

        if os.path.isfile(file_path):

            ext = os.path.splitext(file)[1].lower()

            moved = False


            for folder, extensions in FILE_TYPES.items():

                if ext in extensions:

                    target_folder = os.path.join(
                        folder_path,
                        folder
                    )

                    os.makedirs(
                        target_folder,
                        exist_ok=True
                    )


                    try:

                        shutil.move(
                            file_path,
                            target_folder
                        )

                        message = f"SUCCESS | Moved {file} → {folder}"

                        print(message)
                        log(message)

                        results.append("SUCCESS")

                        moved = True
                        break


                    except Exception as e:

                        handle_error(e)

                        message = f"FAILED | {file}"

                        print(message)
                        log(message)

                        results.append("FAILED")

                        moved = True
                        break



            if not moved:

                message = f"SKIPPED | {file}"

                print(message)
                log(message)

                results.append("SKIPPED")


    return results