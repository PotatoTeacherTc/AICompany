from core.access_token_provider import SignedAccessTokenProvider


class LoginService:
    def __init__(self, user_service, credential_service, token_provider=None, session_service=None):
        self.user_service = user_service
        self.credential_service = credential_service
        self.token_provider = token_provider or SignedAccessTokenProvider()
        self.session_service = session_service

    def login(self, email, password):
        user = self.user_service.get_by_email(email)
        if (
            user is None
            or not self.user_service.is_active(user["user_id"])
            or not self.credential_service.verify_password(user["user_id"], password)
        ):
            raise ValueError("invalid_credentials")
        result={"access_token": self.token_provider.issue(user["user_id"]), "token_type": "bearer"}
        if self.session_service:
            session, refresh_token=self.session_service.create(user["user_id"]); result.update({"refresh_token":refresh_token,"session_id":session["session_id"]})
        return result

    def refresh(self, refresh_token):
        rotated=self.session_service.rotate(refresh_token) if self.session_service else None
        if not rotated: raise ValueError("invalid_refresh_token")
        session, token=rotated
        if not self.user_service.is_active(session["user_id"]):
            raise ValueError("invalid_refresh_token")
        return {"access_token":self.token_provider.issue(session['user_id']),"refresh_token":token,"token_type":"bearer","session_id":session['session_id']}

    def current_user(self, token):
        claims = self.token_provider.verify(token)
        if not claims or not self.user_service.is_active(claims["user_id"]):
            return None
        return self.user_service.get(claims["user_id"])
