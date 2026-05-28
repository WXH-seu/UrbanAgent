from pathlib import Path


def read_project_structure(path: str = ".", max_depth: int = 2) -> str:
    """
    Read and summarize the directory structure of a local project.

    Args:
        path: Directory path to inspect.
        max_depth: Maximum directory depth to display.

    Returns:
        A text tree of the project directory.
    """
    root = Path(path).resolve()

    if not root.exists():
        return f"Path does not exist: {root}"

    if not root.is_dir():
        return f"Path is not a directory: {root}"

    lines = [f"Project structure for: {root}"]

    def walk(current: Path, depth: int):
        if depth > max_depth:
            return

        try:
            children = sorted(
                current.iterdir(),
                key=lambda p: (not p.is_dir(), p.name.lower())
            )
        except PermissionError:
            lines.append("  " * depth + "[Permission denied]")
            return

        for child in children:
            if child.name in {".venv", "__pycache__", ".git", "rag_index", "memory"}:
                continue

            prefix = "  " * depth + "- "
            suffix = "/" if child.is_dir() else ""
            lines.append(f"{prefix}{child.name}{suffix}")

            if child.is_dir():
                walk(child, depth + 1)

    walk(root, 1)
    return "\n".join(lines)