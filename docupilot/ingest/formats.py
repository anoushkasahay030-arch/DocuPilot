"""Upload formats shared by ingestion and the UI."""

TEXT_TYPES = {".txt", ".md", ".markdown"}
HTML_TYPES = {".html", ".htm", ".xhtml"}
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
CODE_TYPES = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".java", ".c", ".h", ".cc", ".cpp", ".hpp",
    ".cs", ".go", ".rs", ".rb", ".php", ".swift", ".kt", ".kts", ".scala", ".sh", ".bash",
    ".zsh", ".ps1", ".sql", ".r", ".lua", ".pl", ".m", ".mm", ".vue", ".svelte", ".css",
    ".scss", ".json", ".yaml", ".yml", ".toml", ".xml", ".ini", ".cfg",
}
CODE_NAMES = {"dockerfile", "containerfile", "makefile", "cmakelists.txt", ".gitignore", ".dockerignore"}
SHEET_TYPES = {".csv", ".tsv", ".xlsx", ".xlsm"}
DOC_TYPES = {".pdf", ".docx", ".pptx"} | TEXT_TYPES | HTML_TYPES | IMAGE_TYPES | CODE_TYPES
SUPPORTED = DOC_TYPES | SHEET_TYPES
