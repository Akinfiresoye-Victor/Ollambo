"""
Automated Project Summary and Walkthrough Generator.

Generates a detailed summary, architectural breakdown, dependency installation
guide, and concrete walkthrough instructions for running and testing generated code.
"""

from __future__ import annotations

import re
from pathlib import Path
from core.planner import CodingPlan

_STD_LIB_MODULES = {
    "os", "sys", "re", "json", "pathlib", "math", "random", "time", "datetime",
    "typing", "collections", "functools", "itertools", "dataclasses", "unittest",
    "subprocess", "hashlib", "threading", "multiprocessing", "queue", "socket",
    "http", "urllib", "sqlite3", "csv", "tempfile", "shutil", "io", "string",
    "copy", "enum", "uuid", "logging", "platform", "argparse", "inspect",
    "textwrap", "glob", "xml", "html", "base64", "struct", "traceback", "abc",
    "ast", "asyncio", "contextlib", "operator", "weakref", "warnings", "zipfile",
}

_PACKAGE_MAPPING = {
    "pil": "pillow",
    "cv2": "opencv-python",
    "bs4": "beautifulsoup4",
    "yaml": "pyyaml",
    "dotenv": "python-dotenv",
    "sklearn": "scikit-learn",
}


def detect_dependencies(files: dict[str, str]) -> list[str]:
    """Detect non-standard third-party dependencies imported across all files."""
    dependencies: set[str] = set()
    internal_names: set[str] = set()

    for path_str in files.keys():
        clean_path = path_str.replace("\\", "/").strip("/")
        parts = clean_path.split("/")
        internal_names.add(parts[0].replace(".py", ""))
        if len(parts) > 1:
            internal_names.add(parts[-1].replace(".py", ""))

    import_pattern = re.compile(r"^(?:from|import)\s+([a-zA-Z0-9_]+)", re.MULTILINE)

    for code in files.values():
        for match in import_pattern.finditer(code):
            module = match.group(1).lower()
            if module in _STD_LIB_MODULES or module in internal_names:
                continue
            pip_name = _PACKAGE_MAPPING.get(module, module)
            dependencies.add(pip_name)

    return sorted(dependencies)


def detect_entrypoint(files: dict[str, str], plan: CodingPlan | None = None) -> str:
    """Identify the primary entrypoint script to execute."""
    file_list = list(files.keys())
    if not file_list:
        return "main.py"

    priority = [
        "main.py", "app.py", "run.py", "cli.py", "gui.py", "index.py",
        "src/main.py", "src/app.py", "src/run.py",
    ]
    norm_map = {f.replace("\\", "/").lower(): f for f in file_list}

    for p in priority:
        if p in norm_map:
            return norm_map[p]

    # Look for if __name__ == "__main__"
    for name, code in files.items():
        if re.search(r'if\s+__name__\s*==\s*[\'"]__main__[\'"]', code):
            return name

    # First non-test python file
    for name in file_list:
        clean = name.replace("\\", "/").lower()
        if clean.endswith(".py") and "test" not in clean:
            return name

    return file_list[0]


def generate_project_walkthrough(plan: CodingPlan | None, files: dict[str, str]) -> str:
    """Generate a clean project summary, architecture guide, and walkthrough on running it."""
    total_files = len(files)
    goal = plan.goal if plan and plan.goal else "Project implementation"
    entrypoint = detect_entrypoint(files, plan)
    deps = detect_dependencies(files)

    # Map file descriptions from plan steps if available
    step_descriptions: dict[str, str] = {}
    if plan and plan.steps:
        for s in plan.steps:
            norm = s.file.replace("\\", "/").lower()
            step_descriptions[norm] = s.description

    lines = [
        f"## Build complete: {goal}\n",
        f"Generated **{total_files} {'file' if total_files == 1 else 'files'}** organized with full directory structure.\n",
        "### Architecture & components\n",
    ]

    for fname in sorted(files.keys()):
        norm = fname.replace("\\", "/").lower()
        desc = step_descriptions.get(norm, "")
        if not desc:
            if "test" in norm:
                desc = "Automated test suite and validation cases"
            elif norm.endswith(entrypoint.replace("\\", "/").lower()):
                desc = "Main application entrypoint"
            elif norm.endswith(".md"):
                desc = "Documentation and setup guide"
            else:
                desc = "Core module and implementation logic"
        lines.append(f"- `{fname}` — {desc}")

    lines.append("\n### Walkthrough: how to run\n")

    step_num = 1
    if deps:
        lines.append(f"**Step {step_num}: Install Dependencies**")
        lines.append("Open your terminal and install the required packages:")
        lines.append(f"```bash\npip install {' '.join(deps)}\n```\n")
        step_num += 1

    lines.append(f"**Step {step_num}: Launch the Application**")
    lines.append(f"Execute the main entrypoint file `{entrypoint}`:")
    lines.append(f"```bash\npython {entrypoint}\n```\n")
    step_num += 1

    test_files = [f for f in files.keys() if "test" in f.lower() and f.endswith(".py")]
    if test_files:
        lines.append(f"**Step {step_num}: Run Automated Tests**")
        lines.append("To verify the code and run test suites:")
        if any("tests" in f.replace("\\", "/") for f in test_files):
            lines.append("```bash\npython -m unittest discover tests\n```\n")
        else:
            lines.append(f"```bash\npython -m unittest {test_files[0]}\n```\n")
        step_num += 1

    lines.append("**Usage & next steps**")
    lines.append(
        "- You can inspect and copy each file in the **Artifacts** tab.\n"
        "- Click **Save All to Folder** in the Artifacts tab to export the full project hierarchy to disk.\n"
        "- If you want to modify features, add tests, or tweak logic, simply let me know!"
    )

    return "\n".join(lines)
