"""Plugin system – loads and manages extension plugins."""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class PluginType(Enum):
    FILESYSTEM = auto()   # virtual filesystem
    LISTER = auto()       # file viewer/previewer
    CONTENT = auto()      # metadata extractor
    PACKER = auto()       # archive handler


@dataclass
class PluginMeta:
    name: str
    version: str
    description: str
    author: str
    plugin_type: PluginType
    enabled: bool = True


class BasePlugin(ABC):
    """Abstract base class for all plugin types."""

    @property
    @abstractmethod
    def meta(self) -> PluginMeta: ...

    def on_load(self) -> None:
        """Called when the plugin is loaded."""

    def on_unload(self) -> None:
        """Called when the plugin is unloaded."""


class ListerPlugin(BasePlugin):
    """Plugin that can render a file preview."""

    @abstractmethod
    def can_handle(self, file_path: str, mime_type: str) -> bool: ...

    @abstractmethod
    def get_widget(self, file_path: str, parent: Any) -> Any:
        """Return a QWidget showing the file preview."""


class ContentPlugin(BasePlugin):
    """Plugin that extracts metadata from files."""

    @abstractmethod
    def can_handle(self, file_path: str) -> bool: ...

    @abstractmethod
    def get_metadata(self, file_path: str) -> dict[str, str]: ...


class PackerPlugin(BasePlugin):
    """Plugin that handles a custom archive format."""

    @abstractmethod
    def can_handle(self, file_path: str) -> bool: ...

    @abstractmethod
    def list_contents(self, archive_path: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    def extract(self, archive_path: str, destination: str, members: list[str] | None = None) -> None: ...


@dataclass
class _LoadedPlugin:
    meta: PluginMeta
    instance: BasePlugin
    module_path: str


class PluginManager:
    """Discovers, loads and manages plugins from a plugin directory."""

    def __init__(self, plugin_dir: Path) -> None:
        self._plugin_dir = plugin_dir
        self._plugins: dict[str, _LoadedPlugin] = {}

    def discover(self) -> None:
        """Scan plugin directory and load all valid plugins."""
        if not self._plugin_dir.is_dir():
            return
        for path in self._plugin_dir.glob("*.py"):
            if path.name.startswith("_"):
                continue
            try:
                self._load_module(path)
            except Exception as exc:
                logger.warning("Failed to load plugin %s: %s", path.name, exc)

    def _load_module(self, path: Path) -> None:
        spec = importlib.util.spec_from_file_location(path.stem, str(path))
        if spec is None or spec.loader is None:
            return
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"mm_plugin_{path.stem}"] = module
        spec.loader.exec_module(module)  # type: ignore[arg-type]

        plugin_class = getattr(module, "Plugin", None)
        if plugin_class is None or not issubclass(plugin_class, BasePlugin):
            return

        instance: BasePlugin = plugin_class()
        instance.on_load()
        loaded = _LoadedPlugin(
            meta=instance.meta,
            instance=instance,
            module_path=str(path),
        )
        self._plugins[instance.meta.name] = loaded
        logger.info("Loaded plugin: %s v%s", instance.meta.name, instance.meta.version)

    def get_plugins(self, plugin_type: PluginType | None = None) -> list[BasePlugin]:
        result = [p.instance for p in self._plugins.values() if p.meta.enabled]
        if plugin_type:
            result = [p for p in result if p.meta.plugin_type == plugin_type]
        return result

    def get_lister_for(self, file_path: str, mime_type: str = "") -> ListerPlugin | None:
        for plugin in self.get_plugins(PluginType.LISTER):
            if isinstance(plugin, ListerPlugin) and plugin.can_handle(file_path, mime_type):
                return plugin
        return None

    def get_content_for(self, file_path: str) -> ContentPlugin | None:
        for plugin in self.get_plugins(PluginType.CONTENT):
            if isinstance(plugin, ContentPlugin) and plugin.can_handle(file_path):
                return plugin
        return None

    def unload_all(self) -> None:
        for loaded in self._plugins.values():
            try:
                loaded.instance.on_unload()
            except Exception as exc:
                logger.warning("Error unloading plugin %s: %s", loaded.meta.name, exc)
        self._plugins.clear()

    @property
    def plugin_count(self) -> int:
        return len(self._plugins)
