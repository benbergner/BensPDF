"""
Result shape for every tool in the library.

Tools return dictionaries rather than raising, so an agent can read the reason
and recover. Using these two helpers everywhere keeps the shape identical
across every verb, which matters because the model learns the shape.
"""

from typing import Any, Dict


def ok(**fields: Any) -> Dict[str, Any]:
    """Build a successful result.

    Example:
        >>> ok(page_count=12)
        {'success': True, 'page_count': 12}
    """
    return {"success": True, **fields}


def err(message: str, **fields: Any) -> Dict[str, Any]:
    """Build a failed result.

    Args:
        message: Human and model readable explanation of what went wrong,
            ideally including what to do about it.
        **fields: Any extra context worth returning alongside the error.

    Example:
        >>> err("File not found: nope.pdf", file_exists=False)
        {'success': False, 'error': 'File not found: nope.pdf', 'file_exists': False}
    """
    return {"success": False, "error": message, **fields}
