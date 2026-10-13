# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path

block_cipher = None
root_dir = Path.cwd()

# Bundle application icon and assets
datas = []
for asset_name in ("Favicon.ico", "favicon.ico", "icon.png"):
    p = root_dir / asset_name
    if p.exists():
        datas.append((str(p), "."))

skills_dir = root_dir / "skills"
if skills_dir.exists():
    datas.append((str(skills_dir), "skills"))

assets_dir = root_dir / "assets"
if assets_dir.exists():
    datas.append((str(assets_dir), "assets"))

icon_path = None
for candidate in ("Favicon.ico", "favicon.ico", "icon.png"):
    cand_path = root_dir / candidate
    if cand_path.exists():
        icon_path = str(cand_path)
        break

a = Analysis(
    ["main.py"],
    pathex=[str(root_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "PySide6",
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebEngineCore",
        "markdown_it",
        "httpx",
        "platformdirs",
        "core",
        "core.ollama_client",
        "core.planner",
        "core.code_writer",
        "core.code_tester",
        "core.task_classifier",
        "core.context_manager",
        "core.build_session",
        "core.storage",
        "core.walkthrough",
        "core.markdown_formatter",
        "core.setup_checker",
        "core.installer",
        "core.prompts",
        "core.assets",
        "core.web_research",
        "core.research_store",
        "core.research_planner",
        "core.research_budget",
        "core.research_engine",
        "core.research_writer",
        "ui",
        "ui.main_window",
        "ui.chat_page",
        "ui.chat_sidebar",
        "ui.chat_top_bar",
        "ui.message_widget",
        "ui.plan_panel",
        "ui.research_panel",
        "ui.research_plan_panel",
        "ui.report_viewer",
        "ui.artifacts_panel",
        "ui.settings_dialog",
        "ui.setup_wizard",
        "ui.terminal_widget",
        "workers",
        "workers.chat_worker",
        "workers.code_worker",
        "workers.plan_worker",
        "workers.research_worker",
        "workers.setup_worker",
        "workers.install_worker",
        "workers.pull_worker",
        "workers.models_worker",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "scipy", "pytest", "unittest"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Ollambo",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Ollambo",
)
