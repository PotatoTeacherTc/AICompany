from abc import ABC, abstractmethod
import ctypes
from ctypes import wintypes
import hashlib
import json
import secrets
import sys


_FIELDS = {"access_token", "refresh_token", "expires_at", "token_type", "granted_scopes"}


class SecureTokenStoreError(RuntimeError):
    def __init__(self, code): self.code = code; super().__init__(f"SecureTokenStoreError: {code}")


class SecureTokenStore(ABC):
    @abstractmethod
    def put(self, workspace_id, connection_id, token_payload): pass
    @abstractmethod
    def get(self, workspace_id, connection_id, token_reference): pass
    @abstractmethod
    def replace(self, workspace_id, connection_id, token_reference, token_payload): pass
    @abstractmethod
    def delete(self, workspace_id, connection_id, token_reference): pass
    @abstractmethod
    def exists(self, workspace_id, connection_id, token_reference): pass


class FakeSecureTokenStore(SecureTokenStore):
    def __init__(self, records=None): self._records = records if records is not None else {}
    def put(self, workspace_id, connection_id, token_payload):
        _scope(workspace_id, connection_id); payload = _payload(token_payload)
        reference = "ytsec_" + secrets.token_hex(24)
        self._records[_key(workspace_id, connection_id, reference)] = payload
        return reference
    def get(self, workspace_id, connection_id, token_reference):
        key = _key(workspace_id, connection_id, token_reference)
        if key not in self._records: raise SecureTokenStoreError("NOT_FOUND")
        return _copy(self._records[key])
    def replace(self, workspace_id, connection_id, token_reference, token_payload):
        key = _key(workspace_id, connection_id, token_reference)
        if key not in self._records: raise SecureTokenStoreError("NOT_FOUND")
        self._records[key] = _payload(token_payload); return token_reference
    def delete(self, workspace_id, connection_id, token_reference):
        return self._records.pop(_key(workspace_id, connection_id, token_reference), None) is not None
    def exists(self, workspace_id, connection_id, token_reference):
        return _key(workspace_id, connection_id, token_reference) in self._records


class WindowsLocalSecureTokenStore(SecureTokenStore):
    """Current-user Windows Credential Manager adapter; no filesystem fallback."""
    _TYPE_GENERIC = 1
    _PERSIST_LOCAL_MACHINE = 2

    def __init__(self, api=None):
        if sys.platform != "win32" and api is None: raise SecureTokenStoreError("UNSUPPORTED_SECURE_STORE")
        self.api = api or _WindowsCredentialApi()

    def put(self, workspace_id, connection_id, token_payload):
        _scope(workspace_id, connection_id); payload = _payload(token_payload)
        reference = "ytsec_" + secrets.token_hex(24)
        self._write(workspace_id, connection_id, reference, payload)
        return reference
    def get(self, workspace_id, connection_id, token_reference):
        target = _target(workspace_id, connection_id, token_reference)
        try: raw = self.api.read(target)
        except Exception: raise SecureTokenStoreError("NOT_FOUND") from None
        try:
            value = json.loads(raw.decode("utf-8"))
            if value.pop("binding") != _binding(workspace_id, connection_id, token_reference): raise ValueError
            return _payload(value)
        except Exception: raise SecureTokenStoreError("SECURE_STORE_DATA_INVALID") from None
    def replace(self, workspace_id, connection_id, token_reference, token_payload):
        if not self.exists(workspace_id, connection_id, token_reference): raise SecureTokenStoreError("NOT_FOUND")
        self._write(workspace_id, connection_id, token_reference, _payload(token_payload)); return token_reference
    def delete(self, workspace_id, connection_id, token_reference):
        try: return bool(self.api.delete(_target(workspace_id, connection_id, token_reference)))
        except Exception: raise SecureTokenStoreError("SECURE_STORE_DELETE_FAILED") from None
    def exists(self, workspace_id, connection_id, token_reference):
        try: self.get(workspace_id, connection_id, token_reference); return True
        except SecureTokenStoreError as error:
            if error.code == "NOT_FOUND": return False
            raise
    def _write(self, workspace_id, connection_id, reference, payload):
        value = {**payload, "binding": _binding(workspace_id, connection_id, reference)}
        encoded = json.dumps(value, separators=(",", ":")).encode("utf-8")
        if len(encoded) > 2400: raise SecureTokenStoreError("TOKEN_PAYLOAD_TOO_LARGE")
        try: self.api.write(_target(workspace_id, connection_id, reference), encoded)
        except Exception: raise SecureTokenStoreError("SECURE_STORE_WRITE_FAILED") from None


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]


class _WindowsCredentialApi:
    def __init__(self):
        self.dll = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        self.dll.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
        self.dll.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(_CREDENTIALW))]
        self.dll.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self.dll.CredFree.argtypes = [ctypes.c_void_p]
    def write(self, target, content):
        blob = (ctypes.c_ubyte * len(content)).from_buffer_copy(content)
        credential = _CREDENTIALW(0, 1, target, None, wintypes.FILETIME(), len(content), blob, 2, 0, None, None, "AICompany")
        if not self.dll.CredWriteW(ctypes.byref(credential), 0): raise OSError
    def read(self, target):
        pointer = ctypes.POINTER(_CREDENTIALW)()
        if not self.dll.CredReadW(target, 1, 0, ctypes.byref(pointer)): raise OSError
        try: return ctypes.string_at(pointer.contents.CredentialBlob, pointer.contents.CredentialBlobSize)
        finally: self.dll.CredFree(pointer)
    def delete(self, target):
        if self.dll.CredDeleteW(target, 1, 0): return True
        if ctypes.get_last_error() == 1168: return False
        raise OSError


def _payload(value):
    if not isinstance(value, dict) or set(value) != _FIELDS: raise SecureTokenStoreError("TOKEN_PAYLOAD_INVALID")
    if not all(isinstance(value[key], str) and value[key].strip() for key in ("access_token", "refresh_token", "expires_at", "token_type")): raise SecureTokenStoreError("TOKEN_PAYLOAD_INVALID")
    scopes = value["granted_scopes"]
    if not isinstance(scopes, (list, tuple)) or not scopes or any(not isinstance(item, str) or not item for item in scopes): raise SecureTokenStoreError("TOKEN_PAYLOAD_INVALID")
    clean = {key: value[key].strip() for key in ("access_token", "refresh_token", "expires_at", "token_type")}
    clean["granted_scopes"] = tuple(scopes); return clean
def _scope(workspace, connection):
    if not all(isinstance(value, str) and value and len(value) <= 128 and all(c.isalnum() or c in "._:-" for c in value) for value in (workspace, connection)): raise SecureTokenStoreError("SCOPE_INVALID")
def _key(workspace, connection, reference): _scope(workspace, connection); _reference(reference); return workspace, connection, reference
def _reference(value):
    if not isinstance(value, str) or not re_full_reference(value): raise SecureTokenStoreError("REFERENCE_INVALID")
def re_full_reference(value): return len(value) == 54 and value.startswith("ytsec_") and all(c in "0123456789abcdef" for c in value[6:])
def _binding(workspace, connection, reference): _key(workspace, connection, reference); return hashlib.sha256(f"{workspace}\0{connection}\0{reference}".encode()).hexdigest()
def _target(workspace, connection, reference): return "AICompany/YouTube/" + _binding(workspace, connection, reference)
def _copy(value): return {**value, "granted_scopes": tuple(value["granted_scopes"])}
