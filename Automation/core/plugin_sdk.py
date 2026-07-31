from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
import re


SDK_VERSION = "1.0.0"
_ID = re.compile(r"^[a-z][a-z0-9_.-]{1,127}$")


@dataclass(frozen=True)
class Capability:
    name: str
    version: str = "1.0.0"

    def validate(self):
        if not _ID.fullmatch(self.name) or not compatible_version(self.version):
            raise ValueError("invalid_capability")


@dataclass(frozen=True)
class PluginManifest:
    plugin_id: str
    name: str
    version: str
    sdk_version: str
    capabilities: tuple[Capability, ...]

    def validate(self):
        if (
            not _ID.fullmatch(self.plugin_id)
            or not isinstance(self.name, str)
            or not self.name.strip()
            or not compatible_version(self.version)
            or not compatible_version(self.sdk_version, SDK_VERSION)
            or not self.capabilities
        ):
            raise ValueError("invalid_plugin_manifest")
        names = []
        for capability in self.capabilities:
            capability.validate()
            names.append(capability.name)
        if len(names) != len(set(names)):
            raise ValueError("duplicate_capability")
        return self

    def to_dict(self):
        self.validate()
        return asdict(self)


class Plugin(ABC):
    @property
    @abstractmethod
    def manifest(self):
        pass

    @abstractmethod
    def invoke(self, capability, request):
        pass


class PluginLoader:
    """Loads only explicitly injected local factories."""

    def __init__(self, factories=None):
        self.factories = dict(factories or {})
        self.loaded = {}

    def register(self, plugin_id, factory):
        if not isinstance(plugin_id, str) or not callable(factory):
            raise ValueError("invalid_plugin_factory")
        if plugin_id in self.factories:
            raise ValueError("duplicate_plugin_factory")
        self.factories[plugin_id] = factory

    def load(self, plugin_id):
        factory = self.factories.get(plugin_id)
        if factory is None:
            raise KeyError("plugin_not_registered")
        plugin = factory()
        if not isinstance(plugin, Plugin):
            raise TypeError("invalid_plugin")
        manifest = plugin.manifest.validate()
        if manifest.plugin_id != plugin_id:
            raise ValueError("plugin_identity_mismatch")
        self.loaded[plugin_id] = plugin
        return plugin

    def invoke(self, plugin_id, capability, request):
        plugin = self.loaded.get(plugin_id) or self.load(plugin_id)
        allowed = {item.name for item in plugin.manifest.capabilities}
        if capability not in allowed:
            raise ValueError("capability_not_declared")
        if not isinstance(request, dict):
            raise ValueError("invalid_plugin_request")
        return plugin.invoke(capability, _safe(request))


class FakePlugin(Plugin):
    def __init__(self, plugin_id="fake.example"):
        self._manifest = PluginManifest(
            plugin_id,
            "Offline Fake Plugin",
            "1.0.0",
            SDK_VERSION,
            (Capability("echo.safe"),),
        )

    @property
    def manifest(self):
        return self._manifest

    def invoke(self, capability, request):
        if capability != "echo.safe":
            raise ValueError("unsupported_capability")
        return {"status": "SUCCESS", "capability": capability, "data": request}


def compatible_version(value, required=SDK_VERSION):
    parsed = _version(value)
    expected = _version(required)
    return parsed is not None and expected is not None and parsed[0] == expected[0]


def _version(value):
    if not isinstance(value, str):
        return None
    parts = value.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def _safe(value):
    sensitive = ("prompt", "objective", "token", "secret", "password", "key")
    clean = {}
    for key, item in value.items():
        if not isinstance(key, str) or any(
            word in key.lower() for word in sensitive
        ):
            continue
        if isinstance(item, (str, int, float, bool, type(None))):
            clean[key] = item
    return clean
