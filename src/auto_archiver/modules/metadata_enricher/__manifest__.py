{
    "name": "Media Metadata Enricher",
    "type": ["enricher"],
    "requires_setup": True,
    "dependencies": {"python": ["loguru"], "bin": ["exiftool"]},
    "configs": {
        "look_for_keys": {
            "default": [],
            "help": "Optional list of metadata keys (or special terms 'author', 'datetime', 'location') to filter the ExifTool output down to. If empty, all extracted metadata is kept.",
            "type": "list",
        },
    },
    "description": """
    Extracts metadata information from files using ExifTool.

    ### Features
    - Uses ExifTool to extract detailed metadata from media files.
    - Processes file-specific data like camera settings, geolocation, timestamps, and other embedded metadata.
    - Adds extracted metadata to the corresponding `Media` object within the `Metadata`.

    ### Notes
    - Requires ExifTool to be installed and accessible via the system's PATH.
    - Skips enrichment for files where metadata extraction fails.
    """,
}
