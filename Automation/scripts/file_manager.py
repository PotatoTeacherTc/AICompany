import os
import shutil


FILE_TYPES = {
    "Images": [".jpg", ".png", ".jpeg"],
    "Videos": [".mp4", ".mov"],
    "Music": [".mp3", ".wav"],
    "Documents": [".pdf", ".docx", ".xlsx"]
}


def organize_files(folder_path):

    for file in os.listdir(folder_path):

        file_path = os.path.join(folder_path, file)

        # 폴더는 제외
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

                    shutil.move(
                        file_path,
                        target_folder
                    )

                    print(
                        f"Moved {file} → {folder}"
                    )

                    moved = True
                    break

            if not moved:
                print(f"Skipped: {file}")