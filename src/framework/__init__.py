"""Framework: structured data science projects.

Import it as ``import framework as fw``. The functions here match the R
package's names, so the same documentation and skills apply in both
languages.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from framework._time import now
from framework.adopt import adopt, structure_import
from framework.ai import ai_skills_update
from framework.bootstrap import BootstrapResult, bootstrap
from framework.cache import (
    cache,
    cache_flush,
    cache_forget,
    cache_get,
    cache_list,
    cache_remember,
)
from framework.catalog import DataInfo, data_info, data_list
from framework.cloud import (
    CloudError,
    cloud_login,
    cloud_logout,
    cloud_settings,
    cloud_status,
    cloud_sync,
    data_verify,
    ledger_list,
    ledger_push,
)
from framework.config_home import (
    config_dir,
    config_sync,
    global_settings,
    project_list,
    project_register,
    project_unregister,
)
from framework.connections import (
    Connection,
    connections_list,
    db_connect,
    db_drivers_status,
    db_execute,
    db_list,
    db_query,
    db_transaction,
)
from framework.console import CapturedOutput, capture_output
from framework.create import (
    new,
    new_project,
    project_add_directory,
    project_create,
    project_create_from_layers,
)
from framework.data import data_add, data_read, data_save
from framework.errors import (
    CacheWarning,
    DataChangedWarning,
    DataNotFoundError,
    FrameworkError,
    FrameworkWarning,
    IntegrityError,
    ProjectNotFoundError,
    SettingsError,
    UnsupportedFormatError,
)
from framework.features import tools_available
from framework.global_config import (
    configure_global,
    framework_template_path,
    load_settings_catalog,
    read_framework_template,
    reset_framework_template,
    write_framework_template,
)
from framework.hooks import (
    git_hooks_disable,
    git_hooks_enable,
    git_hooks_install,
    git_hooks_list,
    git_hooks_uninstall,
)
from framework.notebooks import (
    make_notebook,
    make_presentation,
    make_qmd,
    make_revealjs,
    make_script,
    stubs_list,
    stubs_path,
    stubs_publish,
)
from framework.outputs import (
    result_list,
    save_figure,
    save_model,
    save_notebook,
    save_report,
    save_table,
)
from framework.packages import (
    PackagesStatus,
    package_add,
    packages_install,
    packages_list,
    packages_restore,
    packages_snapshot,
    packages_status,
    packages_update,
)
from framework.project import project_root
from framework.quarto import quarto_generate_all, quarto_regenerate
from framework.scaffold import project_info, scaffold, status
from framework.scratch import scratch_capture, scratch_clean
from framework.security import AuditResult, git_security_audit
from framework.settings import settings, settings_read, settings_write
from framework.skill_sync import skills_sync
from framework.spec.check import spec_check
from framework.spec.merge import SpecError
from framework.spec.resolve import ResolvedSpec, spec_resolve
from framework.storage import (
    publish,
    publish_data,
    publish_dir,
    publish_list,
    publish_notebook,
    storage_test,
)

try:
    __version__ = version("framework-py")
except PackageNotFoundError:  # pragma: no cover (running from a source tree)
    __version__ = "0.0.0"

__all__ = [
    "AuditResult",
    "BootstrapResult",
    "CacheWarning",
    "CapturedOutput",
    "CloudError",
    "Connection",
    "DataChangedWarning",
    "DataInfo",
    "DataNotFoundError",
    "FrameworkError",
    "FrameworkWarning",
    "IntegrityError",
    "PackagesStatus",
    "ProjectNotFoundError",
    "ResolvedSpec",
    "SettingsError",
    "SpecError",
    "UnsupportedFormatError",
    "__version__",
    "adopt",
    "ai_skills_update",
    "bootstrap",
    "cache",
    "cache_flush",
    "cache_forget",
    "cache_get",
    "cache_list",
    "cache_remember",
    "capture_output",
    "cloud_login",
    "cloud_logout",
    "cloud_settings",
    "cloud_status",
    "cloud_sync",
    "config_dir",
    "config_sync",
    "configure_global",
    "connections_list",
    "data_add",
    "data_info",
    "data_list",
    "data_read",
    "data_save",
    "data_verify",
    "db_connect",
    "db_drivers_status",
    "db_execute",
    "db_list",
    "db_query",
    "db_transaction",
    "framework_template_path",
    "git_hooks_disable",
    "git_hooks_enable",
    "git_hooks_install",
    "git_hooks_list",
    "git_hooks_uninstall",
    "git_security_audit",
    "global_settings",
    "ledger_list",
    "ledger_push",
    "load_settings_catalog",
    "make_notebook",
    "make_presentation",
    "make_qmd",
    "make_revealjs",
    "make_script",
    "new",
    "new_project",
    "now",
    "package_add",
    "packages_install",
    "packages_list",
    "packages_restore",
    "packages_snapshot",
    "packages_status",
    "packages_update",
    "project_add_directory",
    "project_create",
    "project_create_from_layers",
    "project_info",
    "project_list",
    "project_register",
    "project_root",
    "project_unregister",
    "publish",
    "publish_data",
    "publish_dir",
    "publish_list",
    "publish_notebook",
    "quarto_generate_all",
    "quarto_regenerate",
    "read_framework_template",
    "reset_framework_template",
    "result_list",
    "save_figure",
    "save_model",
    "save_notebook",
    "save_report",
    "save_table",
    "scaffold",
    "scratch_capture",
    "scratch_clean",
    "settings",
    "settings_read",
    "settings_write",
    "skills_sync",
    "spec_check",
    "spec_resolve",
    "status",
    "storage_test",
    "structure_import",
    "stubs_list",
    "stubs_path",
    "stubs_publish",
    "tools_available",
    "write_framework_template",
]
