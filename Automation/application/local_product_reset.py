"""Explicit local-owner password reset entry point for the product launcher."""

from application.local_product import reset_local_owner_password


def main():
    reset_local_owner_password()
    print("Local owner credential reset completed.")


if __name__ == "__main__":
    main()
