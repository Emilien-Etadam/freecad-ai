"""Constants shared by chat UI modules."""

# Known binary file magic bytes — prevents misdetecting binary files as text
_BINARY_MAGIC = (
    b"%PDF",          # PDF
    b"PK\x03\x04",    # ZIP, DOCX, XLSX, PPTX, ODT, JAR
    b"PK\x05\x06",    # ZIP (empty archive)
    b"\x89PNG",        # PNG
    b"\xff\xd8\xff",   # JPEG
    b"GIF8",           # GIF
    b"RIFF",           # WEBP, AVI, WAV
    b"\x7fELF",        # ELF binary
    b"\xd0\xcf\x11",   # MS Office legacy (DOC, XLS, PPT)
    b"\x1f\x8b",       # gzip
    b"BZ",             # bzip2
    b"\xfd7zXZ",       # xz
    b"Rar!",           # RAR
    b"\x00\x00\x01\x00",  # ICO
    b"\x00asm",        # WebAssembly
)

# Themes that ship a global QPushButton stylesheet which overrides
# padding/margins and clips the labels of buttons in this dock.
_STYLESHEET_CONFLICT_THEMES = frozenset({"opendark", "openlight"})

# Color rules per viewport-capture mode (applied to _capture_btn).
_CAPTURE_MODE_COLORS = {
    "off": "",
    "every_message": "font-weight: bold; color: #4fc3f7;",  # light blue
    "after_changes": "font-weight: bold; color: #aed581;",  # light green
}

TEXT_FILE_EXTENSIONS = frozenset({
    "txt", "md", "csv", "tsv", "json", "xml", "yaml", "yml",
    "ini", "cfg", "conf", "toml", "log", "py", "js", "ts",
    "html", "htm", "css", "sql", "sh", "bash", "bat", "ps1",
    "c", "cpp", "h", "hpp", "java", "rs", "go", "rb", "lua",
    "r", "m", "tex", "bib", "svg", "makefile", "dockerfile",
})
