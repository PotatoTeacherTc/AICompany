from core.access_token_provider import SignedAccessTokenProvider


class LoginService:
    def __init__(self, user_service, credential_service, token_provider=None):
        self.user_service = user_service
        self.credential_service = credential_service
        self.token_provider = token_provider or SignedAccessTokenProvider()

    def login(self, email, password):
        user = self.user_service.get_by_email(email)
        if user is None or not self.credential_service.verify_password(user["user_id"], password):
            raise ValueError("invalid_credentials")
        return {"access_token": self.token_provider.issue(user["user_id"]), "token_type": "bearer"}

    def current_user(self, token):
        claims = self.token_provider.verify(token)
        return self.user_service.get(claims["user_id"]) if claims else None
