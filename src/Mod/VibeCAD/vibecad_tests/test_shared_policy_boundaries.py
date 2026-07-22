# SPDX-License-Identifier: LGPL-2.1-or-later
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[4]


def test_standard_export_checks_managed_policy_before_file_dialog() -> None:
    source = (REPOSITORY / "src/Gui/CommandDoc.cpp").read_text(encoding="utf-8")
    method = source[source.index("void StdCmdExport::activated"):]
    guard = method.index("enforce_action(load_managed_policy(), 'export')")
    rbac = method.index("get_service().authorize('export')")
    dialog = method.index("FileDialog::getSaveFileName")
    assert guard < rbac < dialog


def test_shared_gui_export_checks_policy_before_export_module() -> None:
    source = (REPOSITORY / "src/Gui/Application.cpp").read_text(encoding="utf-8")
    method = source[source.index("void Application::exportTo"):]
    guard = method.index("enforce_action(load_managed_policy(), 'export')")
    rbac = method.index("get_service().authorize('export')")
    module_import = method.index('str << "import " << Module')
    assert guard < rbac < module_import


def test_addon_manager_checks_external_plugin_policy_before_network() -> None:
    source = (
        REPOSITORY / "src/Mod/VibeCAD/VibeCADAddonManagerPolicy.py"
    ).read_text(
        encoding="utf-8"
    )
    wrapper = source[source.index("    def guarded("):]
    guard = wrapper.index('action_enforcer(policy, "external_plugin")')
    original = wrapper.index("return original(self, *args, **kwargs)")
    assert guard < original


def test_addon_manager_guard_is_installed_from_parent_owned_init_gui() -> None:
    source = (REPOSITORY / "src/Mod/VibeCAD/InitGui.py").read_text(
        encoding="utf-8"
    )
    module = source.index("import AddonManager as _AddonManager")
    adapter = source.index(
        "from VibeCADAddonManagerPolicy import install_addon_manager_policy_guard"
    )
    install = source.index("install_addon_manager_policy_guard(_AddonManager)")
    assert module < adapter < install


def test_startup_checks_policy_before_user_module_discovery() -> None:
    source = (REPOSITORY / "src/App/FreeCADInit.py").read_text(encoding="utf-8")
    scan = source[source.index("    def scan(self) -> None:"):]
    policy = scan.index("self.managed_external_plugins_enabled(std_mod)")
    user_mod = scan.index("mods.scan_and_override(user_mod)")
    added_packages = scan.index("self.added_python_packages()")
    assert policy < user_mod < added_packages
    assert "if Config.ExternalPluginsEnabled:" in scan[:user_mod]
    assert "search_paths.sys_path = PathSet(retained)" in scan
    assert "resolved.is_relative_to(root)" in scan


def test_extension_package_scan_is_disabled_by_managed_policy() -> None:
    source = (REPOSITORY / "src/App/FreeCADInit.py").read_text(encoding="utf-8")
    load = source[source.index("    def load_mods(self) -> None:"):]
    condition = load.index("if Config.ExternalPluginsEnabled:")
    scan = load.index("self.ext_mod_scanner.scan()")
    assert condition < scan


def test_export_guards_refresh_after_all_application_modules_load() -> None:
    source = (REPOSITORY / "src/App/FreeCADInit.py").read_text(encoding="utf-8")
    load = source[source.index("    def load_mods(self) -> None:"):]
    extension_load = load.index("for mod in self.ext_mod_scanner.iter()")
    refresh = load.index("refresh_export_guards(App)")
    cache = load.index("App.__ModCache__ = module_cache")
    assert extension_load < refresh < cache
