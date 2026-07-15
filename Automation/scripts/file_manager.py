import os


def show_files(folder_path):
    print(f"Checking folder: {folder_path}")

    files = os.listdir(folder_path)

    for file in files:
        print(file)