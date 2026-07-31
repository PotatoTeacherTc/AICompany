from dataclasses import asdict, dataclass
import re


_ID = re.compile(r"^[a-z][a-z0-9_.-]{1,127}$")


@dataclass(frozen=True)
class PackageDependency:
    package_id: str
    minimum_version: str
    maximum_version_exclusive: str | None = None

    def accepts(self, version):
        current = _version(version)
        minimum = _version(self.minimum_version)
        maximum = (
            _version(self.maximum_version_exclusive)
            if self.maximum_version_exclusive is not None else None
        )
        if current is None or minimum is None:
            return False
        return current >= minimum and (maximum is None or current < maximum)

    def validate(self):
        if (
            not _ID.fullmatch(self.package_id)
            or _version(self.minimum_version) is None
            or (
                self.maximum_version_exclusive is not None
                and (
                    _version(self.maximum_version_exclusive) is None
                    or _version(self.maximum_version_exclusive)
                    <= _version(self.minimum_version)
                )
            )
        ):
            raise ValueError("invalid_package_dependency")


@dataclass(frozen=True)
class PackageMetadata:
    package_id: str
    name: str
    version: str
    plugin_id: str
    sdk_version: str
    dependencies: tuple[PackageDependency, ...] = ()

    def validate(self):
        if (
            not _ID.fullmatch(self.package_id)
            or not _ID.fullmatch(self.plugin_id)
            or not isinstance(self.name, str)
            or not self.name.strip()
            or _version(self.version) is None
            or _version(self.sdk_version) is None
        ):
            raise ValueError("invalid_package")
        dependency_ids = []
        for dependency in self.dependencies:
            dependency.validate()
            dependency_ids.append(dependency.package_id)
        if self.package_id in dependency_ids:
            raise ValueError("self_dependency")
        if len(dependency_ids) != len(set(dependency_ids)):
            raise ValueError("duplicate_dependency")
        return self

    def to_dict(self):
        self.validate()
        return asdict(self)


class LocalMarketplaceRegistry:
    """Metadata-only registry; packages are injected, never downloaded."""

    def __init__(self):
        self._packages = {}

    def register(self, package):
        package.validate()
        key = (package.package_id, package.version)
        if key in self._packages:
            raise ValueError("duplicate_package")
        self._packages[key] = package

    def get(self, package_id, version):
        return self._packages.get((package_id, version))

    def versions(self, package_id):
        return sorted(
            (
                package for (identifier, _), package in self._packages.items()
                if identifier == package_id
            ),
            key=lambda package: _version(package.version),
            reverse=True,
        )

    def resolve(self, dependency):
        dependency.validate()
        return next(
            (
                package for package in self.versions(dependency.package_id)
                if dependency.accepts(package.version)
            ),
            None,
        )


class Marketplace:
    """Workspace-isolated Fake install/remove over local metadata."""

    def __init__(self, registry, sdk_version="1.0.0"):
        self.registry = registry
        self.sdk_version = sdk_version
        self._installed = {}

    def install(self, workspace_id, package_id, version):
        self._workspace(workspace_id)
        package = self.registry.get(package_id, version)
        if package is None:
            raise KeyError("package_not_found")
        if _version(package.sdk_version)[0] != _version(self.sdk_version)[0]:
            raise ValueError("sdk_incompatible")
        plan = []
        self._resolve(package, plan, set())
        workspace = self._installed.setdefault(workspace_id, {})
        for item in plan:
            workspace[item.package_id] = item.version
        return {
            "status": "INSTALLED",
            "workspace_id": workspace_id,
            "package_id": package_id,
            "version": version,
            "installed": [
                {"package_id": item.package_id, "version": item.version}
                for item in plan
            ],
        }

    def remove(self, workspace_id, package_id):
        self._workspace(workspace_id)
        installed = self._installed.get(workspace_id, {})
        if package_id not in installed:
            return {"status": "NOT_INSTALLED", "package_id": package_id}
        for other_id, version in installed.items():
            if other_id == package_id:
                continue
            other = self.registry.get(other_id, version)
            if other and any(
                dependency.package_id == package_id
                for dependency in other.dependencies
            ):
                raise ValueError("package_in_use")
        installed.pop(package_id)
        return {"status": "REMOVED", "package_id": package_id}

    def list_installed(self, workspace_id):
        self._workspace(workspace_id)
        return [
            {"package_id": package_id, "version": version}
            for package_id, version in sorted(
                self._installed.get(workspace_id, {}).items()
            )
        ]

    def _resolve(self, package, plan, visiting):
        if package.package_id in visiting:
            raise ValueError("dependency_cycle")
        visiting.add(package.package_id)
        for dependency in package.dependencies:
            resolved = self.registry.resolve(dependency)
            if resolved is None:
                raise ValueError("dependency_unavailable")
            self._resolve(resolved, plan, visiting)
        visiting.remove(package.package_id)
        if all(item.package_id != package.package_id for item in plan):
            plan.append(package)

    @staticmethod
    def _workspace(workspace_id):
        if not isinstance(workspace_id, str) or not workspace_id:
            raise ValueError("invalid_workspace")


def _version(value):
    if not isinstance(value, str):
        return None
    parts = value.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)
