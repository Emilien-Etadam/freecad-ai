#!/usr/bin/env python3
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = Path("/tmp/freecad_tools_orig.py")
OUT_SRC = ROOT / "freecad_ai/tools/freecad_tools.py"
HANDLERS = ROOT / "freecad_ai/tools/handlers"

GROUPS = {
    "part_creation.py": ["create_primitive", "create_body"],
    "sketch.py": [
        "create_sketch", "create_datum_plane", "edit_sketch",
        "pad_sketch", "pocket_sketch", "revolve_sketch", "loft_sketches", "sweep_sketch",
    ],
    "modifiers.py": [
        "boolean_operation", "transform_object", "fillet_edges", "chamfer_edges",
        "scale_object", "section_object", "linear_pattern", "polar_pattern",
        "shell_object", "mirror_feature", "multi_transform",
    ],
    "inspection.py": [
        "measure", "describe_model", "list_faces", "list_edges", "Edge / face filter keywords",
    ],
    "document.py": [
        "list_documents", "switch_document", "get_document_state",
        "create_variable_set", "create_spreadsheet", "set_expression", "modify_property",
        "export_model", "execute_code", "run_macro", "undo",
    ],
    "enclosure.py": [
        "create_inner_ridge", "create_snap_tabs", "create_enclosure_lid", "create_wedge",
    ],
    "view.py": ["Interactive selection", "capture_viewport", "set_view", "zoom_object"],
    "skills.py": ["report_skill_params", "use_skill"],
    "assembly.py": ["Assembly tools"],
}

HEADER = (
    '"""{doc}"""\n\nimport os\n\n'
    "from ..registry import ToolParam, ToolDefinition, ToolResult\n"
    "from ..core.executor import execute_code\n"
    "from ..tool_common import *  # noqa: F403\n\n"
)


def parse_sections(text: str) -> dict[str, str]:
    pattern = re.compile(r"^# ── (.+?) ─+", re.MULTILINE)
    matches = list(pattern.finditer(text))
    sections = {}
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[title] = text[start:end]
    return sections


def main():
    text = SRC.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    tool_common = "".join(lines[0:111]) + "".join(lines[265:447]) + "".join(lines[4791:4907])
    sections = parse_sections(text)
    HANDLERS.mkdir(parents=True, exist_ok=True)
    (ROOT / "freecad_ai/tools/tool_common.py").write_text(tool_common, encoding="utf-8")

    all_tools_order = []
    in_all = False
    for line in lines:
        if line.strip() == "ALL_TOOLS = [":
            in_all = True
            continue
        if in_all:
            if line.strip() == "]":
                break
            name = line.strip().rstrip(",")
            if name:
                all_tools_order.append(name)

    for filename, titles in GROUPS.items():
        doc = "FreeCAD " + filename.replace(".py", "").replace("_", " ") + "."
        body = HEADER.format(doc=doc)
        for title in titles:
            body += sections[title]
        (HANDLERS / filename).write_text(body, encoding="utf-8")

    facade = (
        '"""FreeCAD tool handlers (facade).\n\n'
        "Split across freecad_ai.tools.handlers; stable import path.\n'
        '"""\n\nfrom .tool_common import *  # noqa: F403, F401\n'
    )
    for filename in GROUPS:
        facade += f"from .handlers.{filename[:-3]} import *  # noqa: F403\n"
    facade += "\nALL_TOOLS = [\n" + "".join(f"    {n},\n" for n in all_tools_order) + "]\n"
    OUT_SRC.write_text(facade, encoding="utf-8")
    (HANDLERS / "__init__.py").write_text('"""Split tool handler modules."""\n', encoding="utf-8")
    print(f"OK: {len(all_tools_order)} tools, facade {OUT_SRC.stat().st_size} bytes")


if __name__ == "__main__":
    main()
